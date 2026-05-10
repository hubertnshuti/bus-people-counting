# Bus People Counting

A full-stack IoT system for real-time passenger counting on buses. An ESP32 with two ultrasonic sensors detects entries and exits through the doorway, streams events to a FastAPI backend, and a Streamlit dashboard shows live occupancy with a predictive model that estimates when the bus will reach capacity.

---

## How it works

```
Passengers → [Ultrasonic Sensors] → [ESP32] ──HTTP──► [FastAPI] ──► [SQLite]
                                        ▲                               │
                                        └────── device state ◄──────────┤
                                                                        │
                                        [Streamlit Dashboard] ◄─────────┘
```

Two HC-SR04 sensors are mounted across the bus doorway. The firmware runs a state machine that tracks which sensor breaks first and which clears last — this gives reliable entry vs exit detection without any false counts from people hesitating or backing away.

The dashboard updates every second with no page flash (Streamlit fragments + pure HTML/CSS). A linear regression model, blended with a mechanical calculation, predicts how many minutes until the bus is full.

---

## Project structure

```
bus-people-counting/
├── firmware/               ESP32 firmware (PlatformIO / Arduino)
│   ├── src/main.cpp        Sensor reading, detection state machine, WiFi, HTTP
│   ├── include/
│   │   ├── config.h        ← gitignored, copy from config.h.example
│   │   └── config.h.example
│   └── platformio.ini
│
├── backend/                FastAPI server
│   ├── app/
│   │   ├── main.py         App entry point
│   │   ├── database.py     SQLite connection + schema init
│   │   ├── models.py       Pydantic request/response models
│   │   └── routes/
│   │       ├── events.py   POST /events, GET /events/recent
│   │       └── device.py   GET/POST /device/state
│   ├── data/               bus.db lives here (gitignored)
│   ├── requirements.txt
│   └── .env.example
│
├── dashboard/              Streamlit dashboard
│   ├── app.py
│   ├── .streamlit/config.toml
│   └── requirements.txt
│
├── scripts/
│   └── seed_history.py     Seeds 14 days of historical data for model training
│
└── docs/
    └── wiring.md           Hardware wiring and sensor placement guide
```

---

## Setup

### 1. Firmware

Copy the config template and fill in your WiFi credentials and server IP:

```bash
cp firmware/include/config.h.example firmware/include/config.h
```

Edit `firmware/include/config.h`:

```c
#define WIFI_SSID     "your_network"
#define WIFI_PASSWORD "your_password"
#define FALLBACK_SERVER_IP "192.168.x.x"   // IP of the machine running the backend
```

Open the `firmware/` folder in VS Code with PlatformIO installed, then build and upload to the ESP32.

See `docs/wiring.md` for the full hardware wiring guide.

### 2. Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The server creates `data/bus.db` on first run. Check `http://localhost:8000/docs` for the auto-generated API docs.

### 3. Seed historical data (optional but recommended)

The prediction model needs enough training data to give useful predictions. Run the seed script once:

```bash
python scripts/seed_history.py
```

This inserts 14 days of realistic boarding patterns (morning rush, lunch, afternoon rush, weekends) under `device_id='bus01-seed'`. Your real device data is never touched.

### 4. Dashboard

```bash
cd dashboard
pip install -r requirements.txt
streamlit run app.py
```

Open `http://localhost:8501`. Use the sidebar to set capacity, pause counting, or reset the counter — changes reach the device within one second.

---

## API endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `POST` | `/events` | Receive entry/exit event from ESP32 |
| `GET` | `/events/recent` | Last N events |
| `GET` | `/device/state` | Current capacity / paused / reset_token |
| `POST` | `/device/state` | Update device control state |

---

## Firmware behaviour

- **Direction detection**: sensor A (outer) → sensor B (inner) = entry. Reverse = exit.
- **False positive filters**: partial pass (only one beam triggered), retreat (person backed away), timeout (passage took more than 5 seconds) are all discarded.
- **Sensor fault detection**: if a sensor gives no valid reading for 6 seconds it is flagged; the LCD shows "Please wait..." so passengers aren't confused by raw debug messages.
- **Event queue**: FreeRTOS queue (16 slots) buffers events during WiFi drops. No events are lost unless the queue overflows.
- **Persistent count**: count survives power cycles via ESP32 NVS (Preferences). Reset via the dashboard sends a token; the device only zeros once per token so accidental double-resets don't happen.
- **mDNS**: the device resolves `buscounter.local` first; falls back to a hardcoded IP if mDNS fails.

---

## Prediction model

Features fed to the LinearRegression model:

| Feature | Description |
|---|---|
| `current_count` | People currently on the bus |
| `boarding_rate` | Entries per minute over the last 5 minutes |
| `hour_of_day` | 0–23 |
| `weekday` | 0=Monday … 6=Sunday |

The raw ML prediction is blended with a simple mechanical estimate `(capacity - count) / rate` at 40/60 weight. This keeps the prediction stable when the boarding rate is low or the model hasn't seen many similar situations.

---

## Hardware

- ESP32 DOIT DevKit V1
- 2× HC-SR04 ultrasonic sensors
- 16×2 I2C LCD (address 0x27)
- Green + Red LED with 220Ω resistors
- Active 5V buzzer
