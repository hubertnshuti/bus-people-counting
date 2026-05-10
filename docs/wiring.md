# Hardware Wiring Guide

## Components

| Component | Model |
|---|---|
| Microcontroller | ESP32 DOIT DevKit V1 |
| Ultrasonic sensors | HC-SR04 x2 |
| LCD display | 16x2 I2C (address 0x27) |
| LEDs | Green + Red (5mm) |
| Buzzer | Active buzzer 5V |

---

## Pin Connections

### Sensor A (outer — entry side)

| HC-SR04 pin | ESP32 pin |
|---|---|
| VCC | 5V |
| GND | GND |
| TRIG | GPIO 17 |
| ECHO | GPIO 16 |

### Sensor B (inner — exit side)

| HC-SR04 pin | ESP32 pin |
|---|---|
| VCC | 5V |
| GND | GND |
| TRIG | GPIO 5 |
| ECHO | GPIO 18 |

### LCD (I2C)

| LCD pin | ESP32 pin |
|---|---|
| VCC | 5V |
| GND | GND |
| SDA | GPIO 21 (default I2C SDA) |
| SCL | GPIO 22 (default I2C SCL) |

### Outputs

| Component | ESP32 pin |
|---|---|
| Green LED (+ 220Ω resistor) | GPIO 25 |
| Red LED (+ 220Ω resistor) | GPIO 26 |
| Buzzer | GPIO 23 |

---

## Sensor Placement

Mount both HC-SR04 sensors at the bus doorway, one on each side of the frame, facing across the opening (not down). Sensor A should face the outside (boarding side) and Sensor B the inside.

Detection threshold is 50 cm — set the sensors about 40 cm from the opposite wall so a person passing triggers the beam reliably without false readings from the doorframe.

```
  [outside]  ----door frame----  [inside bus]

  Sensor A →                  ← Sensor B
             ←  ~80cm gap  →
```

A person boarding (outside → inside) breaks Sensor A first, then B.
A person alighting breaks Sensor B first, then A. Direction is determined by which sensor was triggered first and which cleared last.

---

## Power

The ESP32 and sensors run off USB 5V during development. For permanent installation use a 5V 2A regulated supply — the ESP32 WiFi radio draws up to 500 mA on transmit bursts.
