#include <Arduino.h>
#include <Preferences.h>
#include "config.h"

#define SENSOR_A_TRIG  17
#define SENSOR_A_ECHO  16
#define SENSOR_B_TRIG   5
#define SENSOR_B_ECHO  18
#define GREEN_LED      25
#define RED_LED        26
#define BUZZER         23

static const int   DEFAULT_BUS_CAPACITY     = 10;
static const float DETECT_DIST_CM           = 50.0f;
static const float CLEAR_DIST_CM            = 65.0f;
static const unsigned long SAMPLE_INTERVAL_MS      = 18;
static const unsigned long INTER_SENSOR_GAP_US     = 1500;
static const unsigned long PULSE_TIMEOUT_US        = 5000;
static const unsigned long PASSAGE_MAX_DURATION_MS = 5000;
static const unsigned long FEEDBACK_DURATION_MS    = 80;

Preferences prefs;

struct Sensor {
  uint8_t trig; uint8_t echo; const char* name;
  float distanceCm; bool active;
  unsigned long lastRiseMs; unsigned long lastFallMs;
};

Sensor sensorA = {SENSOR_A_TRIG, SENSOR_A_ECHO, "A", -1.0f, false, 0, 0};
Sensor sensorB = {SENSOR_B_TRIG, SENSOR_B_ECHO, "B", -1.0f, false, 0, 0};

enum PassageState { WAITING, TRACKING };
PassageState  passageState   = WAITING;
char          firstBroken    = 0;
bool          aSeenInPassage = false;
bool          bSeenInPassage = false;
unsigned long passageStartMs = 0;

int           peopleCount    = 0;
int           busCapacity    = DEFAULT_BUS_CAPACITY;
unsigned long totalEntries   = 0;
unsigned long totalExits     = 0;
unsigned long discardedTotal = 0;

bool          feedbackActive  = false;
bool          feedbackIsRed   = false;
unsigned long feedbackStartMs = 0;

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
    digitalWrite(GREEN_LED, peopleCount >= busCapacity ? LOW : HIGH);
    digitalWrite(RED_LED,   peopleCount >= busCapacity ? HIGH : LOW);
  }
}

void updateSensor(Sensor& s) {
  s.distanceCm = readDistanceCm(s.trig, s.echo);
  unsigned long now = millis();
  bool inDetect = (s.distanceCm > 0.0f && s.distanceCm <= DETECT_DIST_CM);
  bool inClear  = (s.distanceCm < 0.0f || s.distanceCm >= CLEAR_DIST_CM);
  if (!s.active && inDetect) { s.active = true;  s.lastRiseMs = now; }
  else if (s.active && inClear) { s.active = false; s.lastFallMs = now; }
}

void commitCount(bool isEntry) {
  if (isEntry) { peopleCount++; totalEntries++; }
  else { if (peopleCount > 0) peopleCount--; totalExits++; }
  Serial.printf(">>> %s  count=%d/%d\n",
                isEntry ? "ENTRY" : "EXIT", peopleCount, busCapacity);
  prefs.putInt("count", peopleCount);
  startFeedback(peopleCount >= busCapacity);
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
      else              firstBroken = (sensorA.lastRiseMs <= sensorB.lastRiseMs) ? 'A' : 'B';
      Serial.printf("[passage] start, firstBroken=%c\n", firstBroken);
      break;

    case TRACKING:
      if (a) aSeenInPassage = true;
      if (b) bSeenInPassage = true;
      if (bothClear) {
        char lastCleared = (sensorA.lastFallMs >= sensorB.lastFallMs) ? 'A' : 'B';
        bool bothBeams = aSeenInPassage && bSeenInPassage;
        bool dirOk     = (firstBroken != 0 && firstBroken != lastCleared);

        if (bothBeams && dirOk) {
          commitCount(firstBroken == 'A' && lastCleared == 'B');
        } else {
          discardedTotal++;
          Serial.printf("[discard] %s  first=%c last=%c\n",
            bothBeams ? "retreat" : "partial", firstBroken, lastCleared);
        }
        passageState = WAITING;
        firstBroken = 0; aSeenInPassage = false; bSeenInPassage = false;
        return;
      }
      if (now - passageStartMs > PASSAGE_MAX_DURATION_MS) {
        discardedTotal++;
        Serial.println("[discard] timeout");
        passageState = WAITING;
        firstBroken = 0; aSeenInPassage = false; bSeenInPassage = false;
      }
      break;
  }
}

void setup() {
  Serial.begin(115200); delay(80);
  Serial.println("[BOOT] Bus Counter v1");

  pinMode(SENSOR_A_TRIG, OUTPUT); pinMode(SENSOR_A_ECHO, INPUT);
  pinMode(SENSOR_B_TRIG, OUTPUT); pinMode(SENSOR_B_ECHO, INPUT);
  pinMode(GREEN_LED, OUTPUT); pinMode(RED_LED, OUTPUT); pinMode(BUZZER, OUTPUT);
  digitalWrite(SENSOR_A_TRIG, LOW); digitalWrite(SENSOR_B_TRIG, LOW);
  digitalWrite(BUZZER, LOW);

  prefs.begin("buscount", false);
  peopleCount = prefs.getInt("count", 0);
  if (peopleCount < 0 || peopleCount > 100) peopleCount = 0;

  digitalWrite(GREEN_LED, HIGH);
  Serial.printf("[BOOT] count=%d loaded from flash\n", peopleCount);
}

void loop() {
  static unsigned long lastSampleMs = 0;
  unsigned long now = millis();

  updateFeedback();

  if (now - lastSampleMs >= SAMPLE_INTERVAL_MS) {
    lastSampleMs = now;
    updateSensor(sensorA);
    delayMicroseconds(INTER_SENSOR_GAP_US);
    updateSensor(sensorB);
    updatePassage();
  }

  static unsigned long lastDebug = 0;
  if (now - lastDebug > 500) {
    lastDebug = now;
    Serial.printf("A:%s %.1fcm | B:%s %.1fcm | count:%d/%d in:%lu out:%lu disc:%lu\n",
      sensorA.active ? "ON " : "off", sensorA.distanceCm,
      sensorB.active ? "ON " : "off", sensorB.distanceCm,
      peopleCount, busCapacity, totalEntries, totalExits, discardedTotal);
  }
}
