#include <Arduino.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <Preferences.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ESPmDNS.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "config.h"

LiquidCrystal_I2C lcd(0x27, 16, 2);
Preferences prefs;

#define SENSOR_A_TRIG  17
#define SENSOR_A_ECHO  16
#define SENSOR_B_TRIG   5
#define SENSOR_B_ECHO  18
#define GREEN_LED      25
#define RED_LED        26
#define BUZZER         23

static const int   DEFAULT_BUS_CAPACITY      = 10;
static const float DETECT_DIST_CM            = 50.0f;
static const float CLEAR_DIST_CM             = 65.0f;
static const int   DETECT_CONFIRM_READS      = 2;
static const int   CLEAR_CONFIRM_READS       = 2;
static const unsigned long SAMPLE_INTERVAL_MS       = 18;
static const unsigned long INTER_SENSOR_GAP_US      = 1500;
static const unsigned long PULSE_TIMEOUT_US         = 5000;
static const unsigned long PASSAGE_MAX_DURATION_MS  = 5000;
static const unsigned long SENSOR_FAULT_TIMEOUT_MS  = 6000;
static const unsigned long DISPLAY_MIN_INTERVAL_MS  = 80;
static const unsigned long DISPLAY_QUIET_PERIOD_MS  = 30;
static const unsigned long FEEDBACK_DURATION_MS     = 80;
static const unsigned long PERSIST_DEBOUNCE_MS      = 2000;
static const unsigned long WIFI_CONNECT_TIMEOUT_MS  = 15000;
static const unsigned long WIFI_RETRY_BACKOFF_MS    = 5000;
static const unsigned long STATE_POLL_INTERVAL_MS   = 1000;

String serverBase = "";

struct Sensor {
  uint8_t       trig; uint8_t echo; const char* name;
  float         distanceCm; bool active; int detectCount; int clearCount;
  unsigned long lastRiseMs; unsigned long lastFallMs;
  unsigned long lastValidReadMs; bool faulted;
};

Sensor sensorA = {SENSOR_A_TRIG, SENSOR_A_ECHO, "A", -1.0f, false, 0, 0, 0, 0, 0, false};
Sensor sensorB = {SENSOR_B_TRIG, SENSOR_B_ECHO, "B", -1.0f, false, 0, 0, 0, 0, 0, false};

enum PassageState { WAITING, TRACKING };
PassageState  passageState     = WAITING;
char          firstBroken      = 0;
bool          aSeenInPassage   = false;
bool          bSeenInPassage   = false;
unsigned long passageStartMs   = 0;
unsigned long lastSensorEdgeMs = 0;

int  peopleCount = 0;
int  busCapacity = DEFAULT_BUS_CAPACITY;
bool paused      = false;

volatile long lastResetTokenSeen = -1;
volatile int  pendingCapacity    = DEFAULT_BUS_CAPACITY;
volatile bool pendingPaused      = false;
volatile long pendingResetToken  = -1;

bool          displayDirty       = true;
unsigned long lastDisplayMs      = 0;
int           lastShownCount     = -1;
char          lastShownLine1[17] = "";
int           lastShownCapacity  = -1;

bool          feedbackActive  = false;
bool          feedbackIsRed   = false;
unsigned long feedbackStartMs = 0;

int           lastPersistedCount = 0;
unsigned long countChangedAt     = 0;

unsigned long totalEntries      = 0;
unsigned long totalExits        = 0;
unsigned long discardedRetreats = 0;
unsigned long discardedPartials = 0;
unsigned long discardedTimeouts = 0;

struct PostJob { char eventType[12]; int count; unsigned long uptimeMs; };
QueueHandle_t  postQueue          = nullptr;
TaskHandle_t   networkTaskHandle  = nullptr;
volatile unsigned long httpSuccessCount  = 0;
volatile unsigned long httpFailureCount  = 0;
volatile bool          wifiConnectedFlag = false;

const char* statusMessage() {
  bool fault = (sensorA.faulted || sensorB.faulted);
  if (!wifiConnectedFlag) return "Connecting...";
  if (fault)              return "Please wait...";
  if (paused)             return "Out of service";
  if (peopleCount >= busCapacity) return "Bus is full";
  if (busCapacity > 0 && peopleCount * 100 / busCapacity >= 80) return "Nearly full";
  return "Welcome aboard";
}

void refreshDisplay(bool force = false) {
  const char* msg = statusMessage();
  char line0[17], line1[17];
  snprintf(line0, sizeof(line0), "People: %2d/%2d   ", peopleCount, busCapacity);
  snprintf(line1, sizeof(line1), "%-16s", msg);

  if (!force && peopleCount == lastShownCount && busCapacity == lastShownCapacity
      && strcmp(line1, lastShownLine1) == 0) return;

  lcd.setCursor(0, 0); lcd.print(line0);
  lcd.setCursor(0, 1); lcd.print(line1);
  lastShownCount    = peopleCount;
  lastShownCapacity = busCapacity;
  strncpy(lastShownLine1, line1, sizeof(lastShownLine1));
  lastShownLine1[sizeof(lastShownLine1) - 1] = '\0';
}

void setIdleLEDs() {
  if (peopleCount >= busCapacity) { digitalWrite(RED_LED, HIGH); digitalWrite(GREEN_LED, LOW); }
  else                            { digitalWrite(RED_LED, LOW);  digitalWrite(GREEN_LED, HIGH); }
}

float readDistanceCm(uint8_t trig, uint8_t echo) {
  digitalWrite(trig, LOW); delayMicroseconds(2);
  digitalWrite(trig, HIGH); delayMicroseconds(10); digitalWrite(trig, LOW);
  unsigned long us = pulseIn(echo, HIGH, PULSE_TIMEOUT_US);
  if (us == 0) return -1.0f;
  return us * 0.0343f / 2.0f;
}

void startFeedback(bool isRed) {
  feedbackActive = true; feedbackIsRed = isRed; feedbackStartMs = millis();
  digitalWrite(BUZZER, HIGH);
  digitalWrite(GREEN_LED, isRed ? LOW : HIGH);
  digitalWrite(RED_LED,   isRed ? HIGH : LOW);
}

void updateFeedback() {
  if (!feedbackActive) return;
  if (millis() - feedbackStartMs >= FEEDBACK_DURATION_MS) {
    feedbackActive = false;
    digitalWrite(BUZZER, LOW);
    setIdleLEDs();
  }
}


// Distinct triple-buzz alert when the bus reaches capacity.
// Blocking (~900ms) but only fires the instant the bus fills, so it
// won't disrupt normal counting.
void capacityAlarm() {
  digitalWrite(GREEN_LED, LOW);
  digitalWrite(RED_LED, HIGH);
  for (int i = 0; i < 3; i++) {
    digitalWrite(BUZZER, HIGH);
    delay(200);
    digitalWrite(BUZZER, LOW);
    delay(100);
  }
}

void enqueueEvent(const char* type) {
  if (!postQueue) return;
  PostJob job;
  strncpy(job.eventType, type, sizeof(job.eventType) - 1);
  job.eventType[sizeof(job.eventType) - 1] = '\0';
  job.count    = peopleCount;
  job.uptimeMs = millis();
  if (xQueueSend(postQueue, &job, 0) != pdTRUE)
    Serial.println("[NET] queue full, dropping event");
}

void updateSensor(Sensor& s) {
  s.distanceCm = readDistanceCm(s.trig, s.echo);
  unsigned long now = millis();

  if (s.distanceCm > 0.0f) {
    s.lastValidReadMs = now;
    if (s.faulted) { s.faulted = false; displayDirty = true;
      Serial.printf("Sensor %s recovered.\n", s.name); }
  } else if (now - s.lastValidReadMs > SENSOR_FAULT_TIMEOUT_MS) {
    if (!s.faulted) { s.faulted = true; displayDirty = true;
      Serial.printf("[WARNING] Sensor %s not responding.\n", s.name); }
  }

  bool inDetect = (s.distanceCm > 0.0f && s.distanceCm <= DETECT_DIST_CM);
  bool inClear  = (s.distanceCm < 0.0f || s.distanceCm >= CLEAR_DIST_CM);

  if (inDetect)     { s.detectCount++; s.clearCount = 0; }
  else if (inClear) { s.clearCount++;  s.detectCount = 0; }

  if (!s.active && s.detectCount >= DETECT_CONFIRM_READS) {
    s.active = true; s.lastRiseMs = now;
    s.detectCount = 0; s.clearCount = 0; lastSensorEdgeMs = now;
  } else if (s.active && s.clearCount >= CLEAR_CONFIRM_READS) {
    s.active = false; s.lastFallMs = now;
    s.detectCount = 0; s.clearCount = 0; lastSensorEdgeMs = now;
  }
}

void applyPendingControls() {
  bool changed = false;
  if ((int)pendingCapacity != busCapacity && pendingCapacity > 0 && pendingCapacity < 200) {
    busCapacity = pendingCapacity; prefs.putInt("cap", busCapacity);
    Serial.printf("[control] capacity -> %d\n", busCapacity); changed = true;
  }
  if ((bool)pendingPaused != paused) {
    paused = pendingPaused; prefs.putBool("paused", paused);
    Serial.printf("[control] paused -> %d\n", paused ? 1 : 0); changed = true;
  }
  if (pendingResetToken > lastResetTokenSeen) {
    peopleCount = 0; countChangedAt = millis();
    lastResetTokenSeen = pendingResetToken;
    prefs.putLong("reset_t", lastResetTokenSeen);
    prefs.putInt("count", 0); lastPersistedCount = 0;
    Serial.printf("[control] reset (token=%ld)\n", lastResetTokenSeen);
    changed = true; enqueueEvent("reset");
  }
  if (changed) displayDirty = true;
}

void commitCount(bool isEntry) {
  if (isEntry) { peopleCount++; totalEntries++; enqueueEvent("entry"); }
  else { if (peopleCount > 0) peopleCount--; totalExits++; enqueueEvent("exit"); }
  Serial.printf(">>> %s  count=%d (in=%lu out=%lu)\n",
                isEntry ? "ENTRY" : "EXIT", peopleCount, totalEntries, totalExits);
  countChangedAt = millis();
  displayDirty   = true;
  if (isEntry && peopleCount >= busCapacity) {
    capacityAlarm();          // triple buzz when bus becomes full
  } else {
    startFeedback(peopleCount >= busCapacity);  // normal short beep
  }
}

void updatePassage() {
  unsigned long now = millis();
  bool a = sensorA.active, b = sensorB.active;
  bool bothClear = (!a && !b);

  switch (passageState) {
    case WAITING:
      if (bothClear) return;
      passageState = TRACKING; passageStartMs = now;
      aSeenInPassage = a; bSeenInPassage = b;
      if (a && !b)      firstBroken = 'A';
      else if (b && !a) firstBroken = 'B';
      else {
        if      (sensorA.lastRiseMs < sensorB.lastRiseMs) firstBroken = 'A';
        else if (sensorB.lastRiseMs < sensorA.lastRiseMs) firstBroken = 'B';
        else {
          float da = (sensorA.distanceCm > 0) ? sensorA.distanceCm : 9999.0f;
          float db = (sensorB.distanceCm > 0) ? sensorB.distanceCm : 9999.0f;
          firstBroken = (da <= db) ? 'A' : 'B';
        }
      }
      Serial.printf("[passage start] firstBroken=%c\n", firstBroken);
      break;

    case TRACKING:
      if (a) aSeenInPassage = true;
      if (b) bSeenInPassage = true;
      if (bothClear) {
        char lastCleared;
        if      (sensorA.lastFallMs > sensorB.lastFallMs) lastCleared = 'A';
        else if (sensorB.lastFallMs > sensorA.lastFallMs) lastCleared = 'B';
        else lastCleared = (firstBroken == 'A') ? 'B' : 'A';

        bool bothBeams = aSeenInPassage && bSeenInPassage;
        bool dirOk     = (firstBroken != 0 && firstBroken != lastCleared);

        if (bothBeams && dirOk) {
          bool isEntry = (firstBroken == 'A' && lastCleared == 'B');
          if (paused) Serial.println("[paused] passage not counted");
          else commitCount(isEntry);
        } else if (!bothBeams) {
          discardedPartials++;
          Serial.printf("[discard partial] only %s saw activity\n",
            aSeenInPassage ? "A" : (bSeenInPassage ? "B" : "neither"));
        } else {
          discardedRetreats++;
          Serial.printf("[discard retreat] first=%c last=%c\n", firstBroken, lastCleared);
        }
        passageState = WAITING;
        firstBroken = 0; aSeenInPassage = false; bSeenInPassage = false;
        return;
      }
      if (now - passageStartMs > PASSAGE_MAX_DURATION_MS) {
        discardedTimeouts++;
        Serial.println("[discard timeout]");
        passageState = WAITING;
        firstBroken = 0; aSeenInPassage = false; bSeenInPassage = false;
      }
      break;
  }
}

void maybePersistCount() {
  if (peopleCount == lastPersistedCount) return;
  if (millis() - countChangedAt < PERSIST_DEBOUNCE_MS) return;
  prefs.putInt("count", peopleCount);
  lastPersistedCount = peopleCount;
  Serial.printf("[persist] count=%d saved\n", peopleCount);
}

static void httpFireOne(const PostJob& job) {
  HTTPClient http;
  String url = serverBase + "/events";
  if (!http.begin(url)) { httpFailureCount++; return; }
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(4000);
  String payload = String("{\"device\":\"") + DEVICE_ID
                 + "\",\"event_type\":\"" + job.eventType
                 + "\",\"count\":" + job.count
                 + ",\"uptime_ms\":" + (unsigned long)job.uptimeMs + "}";
  Serial.printf("[HTTP] POST %s  %s\n", url.c_str(), payload.c_str());
  int code = http.POST(payload);
  if (code >= 200 && code < 300) httpSuccessCount++;
  else httpFailureCount++;
  http.end();
}

static void pollDeviceState() {
  HTTPClient http;
  String url = serverBase + "/device/state?device_id=" + DEVICE_ID;
  if (!http.begin(url)) return;
  http.setTimeout(2500);
  int code = http.GET();
  if (code == 200) {
    String body = http.getString();
    int capIdx = body.indexOf("\"capacity\":");
    int pauIdx = body.indexOf("\"paused\":");
    int rstIdx = body.indexOf("\"reset_token\":");
    if (capIdx >= 0) {
      int s = capIdx + 11, e = body.indexOf(',', s);
      if (e < 0) e = body.indexOf('}', s);
      pendingCapacity = body.substring(s, e).toInt();
    }
    if (pauIdx >= 0) pendingPaused = (body.charAt(pauIdx + 9) == 't');
    if (rstIdx >= 0) {
      int s = rstIdx + 14, e = body.indexOf('}', s);
      if (e < 0) e = body.length();
      pendingResetToken = body.substring(s, e).toInt();
    }
  }
  http.end();
}

void resolveServerBase() {
  Serial.printf("[mDNS] looking up %s.local...\n", MDNS_HOST);
  if (!MDNS.begin("buscounter-esp32")) {
    Serial.println("[mDNS] init failed, using fallback IP");
    serverBase = String("http://") + FALLBACK_SERVER_IP + ":" + SERVER_PORT;
    return;
  }
  IPAddress ip;
  for (int i = 1; i <= 3; i++) {
    ip = MDNS.queryHost(MDNS_HOST, 2000);
    if (ip != IPAddress(0, 0, 0, 0)) {
      serverBase = String("http://") + ip.toString() + ":" + SERVER_PORT;
      Serial.printf("[mDNS] resolved -> %s\n", ip.toString().c_str());
      return;
    }
    Serial.printf("[mDNS] attempt %d failed\n", i);
    vTaskDelay(pdMS_TO_TICKS(500));
  }
  Serial.printf("[mDNS] giving up, fallback: %s\n", FALLBACK_SERVER_IP);
  serverBase = String("http://") + FALLBACK_SERVER_IP + ":" + SERVER_PORT;
}

static void wifiConnectBlocking() {
  Serial.printf("[WIFI] connecting to \"%s\"\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true, true);
  vTaskDelay(pdMS_TO_TICKS(100));
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - start > WIFI_CONNECT_TIMEOUT_MS) {
      Serial.println("[WIFI] timeout, retrying...");
      WiFi.disconnect(true, true);
      vTaskDelay(pdMS_TO_TICKS(WIFI_RETRY_BACKOFF_MS));
      WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
      start = millis();
    }
    vTaskDelay(pdMS_TO_TICKS(200));
  }
  Serial.printf("[WIFI] connected! IP=%s RSSI=%d\n",
                WiFi.localIP().toString().c_str(), WiFi.RSSI());
  wifiConnectedFlag = true;
}

void networkTask(void* /*arg*/) {
  Serial.printf("[NET] task on core %d\n", xPortGetCoreID());
  wifiConnectBlocking();
  resolveServerBase();

  unsigned long lastStatePoll = 0;
  for (;;) {
    if (WiFi.status() != WL_CONNECTED) {
      wifiConnectedFlag = false;
      Serial.println("[WIFI] lost connection, reconnecting...");
      wifiConnectBlocking();
      resolveServerBase();
    }
    PostJob job;
    if (xQueueReceive(postQueue, &job, pdMS_TO_TICKS(200)) == pdTRUE)
      httpFireOne(job);
    if (millis() - lastStatePoll >= STATE_POLL_INTERVAL_MS) {
      lastStatePoll = millis();
      pollDeviceState();
    }
  }
}

void setup() {
  Serial.begin(115200); delay(80);
  Serial.println("============================================");
  Serial.println("[BOOT] Bus Counter v3 final");
  Serial.println("============================================");

  pinMode(SENSOR_A_TRIG, OUTPUT); pinMode(SENSOR_A_ECHO, INPUT);
  pinMode(SENSOR_B_TRIG, OUTPUT); pinMode(SENSOR_B_ECHO, INPUT);
  pinMode(GREEN_LED, OUTPUT); pinMode(RED_LED, OUTPUT); pinMode(BUZZER, OUTPUT);
  digitalWrite(SENSOR_A_TRIG, LOW); digitalWrite(SENSOR_B_TRIG, LOW);
  digitalWrite(BUZZER, LOW);

  prefs.begin("buscount", false);
  peopleCount        = prefs.getInt("count", 0);
  if (peopleCount < 0 || peopleCount > 100) peopleCount = 0;
  lastPersistedCount = peopleCount;
  busCapacity        = prefs.getInt("cap", DEFAULT_BUS_CAPACITY);
  paused             = prefs.getBool("paused", false);
  lastResetTokenSeen = prefs.getLong("reset_t", -1);
  pendingCapacity    = busCapacity;
  pendingPaused      = paused;
  pendingResetToken  = lastResetTokenSeen;

  unsigned long now = millis();
  sensorA.lastValidReadMs = now;
  sensorB.lastValidReadMs = now;

  lcd.init(); lcd.backlight(); lcd.clear();
  lcd.setCursor(0, 0); lcd.print("Bus Counter");
  lcd.setCursor(0, 1); lcd.print("Starting up...");
  delay(700);

  refreshDisplay(true);
  setIdleLEDs();
  Serial.printf("[BOOT] count=%d cap=%d paused=%d\n", peopleCount, busCapacity, paused ? 1 : 0);

  postQueue = xQueueCreate(16, sizeof(PostJob));
  xTaskCreatePinnedToCore(networkTask, "networkTask", 8192, nullptr, 1, &networkTaskHandle, 0);
  Serial.println("[BOOT] Network task started on core 0.");
  enqueueEvent("boot");
}

void loop() {
  static unsigned long lastSampleMs = 0;
  unsigned long now = millis();

  updateFeedback();
  if (passageState == WAITING) applyPendingControls();

  if (now - lastSampleMs >= SAMPLE_INTERVAL_MS) {
    lastSampleMs = now;
    updateSensor(sensorA);
    delayMicroseconds(INTER_SENSOR_GAP_US);
    updateSensor(sensorB);
    updatePassage();
    return;
  }

  if (displayDirty && passageState == WAITING
      && (now - lastSensorEdgeMs) > DISPLAY_QUIET_PERIOD_MS
      && (now - lastDisplayMs) > DISPLAY_MIN_INTERVAL_MS) {
    refreshDisplay();
    displayDirty = false; lastDisplayMs = millis();
  }

  static unsigned long lastForced = 0;
  if (now - lastForced > 1000) { lastForced = now; displayDirty = true; }

  maybePersistCount();

  static unsigned long lastDebug = 0;
  if (now - lastDebug > 250) {
    lastDebug = now;
    Serial.printf("A:%s %5.1fcm | B:%s %5.1fcm | st:%d cnt:%d/%d %s | in:%lu out:%lu drops:%lu/%lu/%lu | wifi:%s http:%lu/%lu\n",
      sensorA.active ? "ON " : "off", sensorA.distanceCm,
      sensorB.active ? "ON " : "off", sensorB.distanceCm,
      (int)passageState, peopleCount, busCapacity,
      paused ? "PAUSED" : "      ",
      totalEntries, totalExits,
      discardedRetreats, discardedPartials, discardedTimeouts,
      wifiConnectedFlag ? "UP" : "down",
      httpSuccessCount, httpFailureCount);
  }
}
