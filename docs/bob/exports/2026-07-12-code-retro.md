---
tool: IBM Bob (Bob Shell)
date: 2026-07-12
scope: src/racecoach/control/simple_driver.py
prompt: Summarise how this driver decides braking points, steering, and throttle. List parameters and conditions that limit cornering speed.
---

## SimpleDriver Control Logic Summary

### Steering Decision
The driver uses a **proportional controller** combining two factors:
- **Track angle** (`angle`): Multiplied by `steer_angle_gain` (default: 1.0) to point the car toward the road
- **Lateral position** (`trackPos`): Multiplied by `steer_pos_gain` (default: 0.5) to center the car on track

Formula: `steer = (steer_angle_gain × angle - steer_pos_gain × trackPos) / STEER_LOCK_RAD`

Output is clamped to [-1.0, 1.0] range.

### Braking Point Decision
Braking is triggered when **current speed exceeds target speed**:
- **Target speed** is calculated from the forward rangefinder beam: `target_kmh = beam_speed_gain × ahead_distance`
- Clamped between `min_speed_kmh` (50 km/h) and `max_speed_kmh` (220 km/h)
- **Brake intensity**: `brake_gain × speed_excess` (default gain: 0.10)

The driver uses **one pedal at a time** — either throttle OR brake, never both.

### Throttle Decision
Throttle is applied when **current speed is below target speed**:
- **Throttle intensity**: `accel_gain × speed_deficit` (default gain: 0.35)
- Clamped to [0.0, 1.0]

**Special case**: When off-track (`|trackPos| > 1.0`), throttle is limited to `offtrack_throttle` (0.3) and gear is capped at 3rd.

### Parameters Limiting Cornering Speed

**Primary Speed Limiters:**
1. **`beam_speed_gain`** (1.3 km/h per meter): Converts clear road ahead into target speed — lower values = slower cornering
2. **`max_speed_kmh`** (220): Absolute speed ceiling
3. **`min_speed_kmh`** (50): Absolute speed floor
4. **Forward rangefinder beam** (`track[9]`): Physical measurement of clear road ahead — shorter distance = lower target speed

**Secondary Limiters:**
5. **`accel_gain`** (0.35): Throttle response rate — lower = slower acceleration into corners
6. **`brake_gain`** (0.10): Braking response rate — lower = gentler braking
7. **`offtrack_throttle`** (0.3): Severe speed limit when off-track
8. **Gear limits**: Off-track gear capped at 3rd, normal operation capped at 6th

**Conditions Limiting Cornering Speed:**
- **Tight corners**: Shorter rangefinder reading → lower target speed → earlier/harder braking
- **Off-track position**: Triggers crawl mode with 30% throttle and low gear
- **Track angle**: Large angles increase steering demand but don't directly limit speed (speed limit comes from rangefinder)

The driver's cornering speed is fundamentally limited by **how far ahead it can "see"** (rangefinder) multiplied by the **beam_speed_gain** parameter.
