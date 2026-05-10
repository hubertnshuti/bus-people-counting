# System Architecture

## Component overview

```
┌─────────────────────────────────────────────────────┐
│  HARDWARE                                           │
│  ESP32 DevKit V1                                    │
│   ├── Sensor A (GPIO 17/16)  ──┐                   │
│   ├── Sensor B (GPIO 5/18)   ──┼── Passage detect  │
│   ├── LCD 16x2 I2C (21/22)      │   state machine   │
│   ├── Green LED (GPIO 25)        │                   │
│   ├── Red LED   (GPIO 26)        │                   │
│   └── Buzzer    (GPIO 23)        │                   │
└──────────────────────────────────────────────────────┘
          │  HTTP over WiFi (FreeRTOS task, core 0)
          ▼
┌─────────────────────────────────────────────────────┐
│  BACKEND  FastAPI  :8000                            │
│   POST /events          ← receives entry/exit       │
│   GET  /device/state    ← polled every 1s by ESP32  │
│   POST /device/state    ← capacity / pause / reset  │
│   GET  /events/recent                               │
│          │                                          │
│        SQLite (data/bus.db)                         │
│          ├── events table                           │
│          └── device_state table                     │
└─────────────────────────────────────────────────────┘
          │  reads bus.db directly + HTTP to /device/state
          ▼
┌─────────────────────────────────────────────────────┐
│  DASHBOARD  Streamlit  :8501                        │
│   fast_fragment      (1s)  live count + capacity bar│
│   prediction_fragment(1s)  ML overcrowding alert    │
│   count_chart        (30s) line chart over time     │
│   heatmap_fragment   (60s) boarding heatmap         │
│   daily_history      (60s) per-day entries/exits    │
│   recent_events      (5s)  live event table         │
└─────────────────────────────────────────────────────┘
```

## Data flow

1. Passenger crosses doorway → both ultrasonic beams break in sequence
2. ESP32 state machine validates direction (entry or exit)
3. Event enqueued into FreeRTOS queue (core 0 network task picks it up)
4. HTTP POST to `/events` → stored in SQLite
5. Dashboard reads SQLite at its own refresh interval per fragment
6. Sidebar control → dashboard POST `/device/state` → ESP32 polls and applies

## Key design decisions

**Why two sensors instead of one?**
A single sensor can't tell which direction someone is moving. Two sensors in sequence give entry vs exit. The state machine also filters retreats and partial passes so only real crossings are counted.

**Why FreeRTOS queue for HTTP?**
The main loop runs sensor sampling every 18 ms. A blocking HTTP call would miss passages during that time. Offloading HTTP to a separate task on core 0 keeps the sensor loop responsive. The queue also buffers events during WiFi drops.

**Why blend ML with mechanical prediction?**
The pure linear regression model was too optimistic at low boarding rates — it had seen mostly busy periods in training. Weighting the mechanical calculation (remaining seats / boarding rate) at 60% keeps the prediction stable and accurate in quieter periods.

**Why fragments in Streamlit?**
Without fragments the whole page re-renders on every refresh, which causes visible flash. Fragments let different parts of the page update independently. Live metrics and prediction use 1-second fragments with pure HTML/CSS so there's no Plotly re-render flash at all.
