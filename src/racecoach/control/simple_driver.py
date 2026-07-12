"""A baseline rule-based SCR driver — the controller under coaching.

The logic follows the championship's reference driver in spirit: steer from
track angle and lateral position, target speed from the ahead rangefinder,
gears from rpm thresholds. It is a pure function of one state dict, so the
whole policy is unit-testable without a simulator, and every tunable lives
on the dataclass where the coach's code recommendations can name it.
"""

from dataclasses import dataclass

STEER_LOCK_RAD = 0.785398  # scr_server steering range is ±45°


@dataclass(frozen=True)
class SimpleDriver:
    max_speed_kmh: float = 220.0
    min_speed_kmh: float = 50.0
    beam_speed_gain: float = 1.3  # km/h of target speed per metre of clear road ahead
    steer_angle_gain: float = 1.0
    steer_pos_gain: float = 0.5
    accel_gain: float = 0.35  # throttle per km/h of speed deficit
    brake_gain: float = 0.10  # brake per km/h of speed excess
    gear_up_rpm: float = 7500.0
    gear_down_rpm: float = 3000.0
    offtrack_throttle: float = 0.3

    def drive(self, state: dict) -> dict:
        """SCR state dict -> action dict (accel/brake/steer/gear/clutch)."""
        angle = float(state.get("angle", 0.0))
        track_pos = float(state.get("trackPos", 0.0))
        speed_kmh = float(state.get("speedX", 0.0))
        rpm = float(state.get("rpm", 0.0))
        gear = int(state.get("gear", 1)) or 1

        steer = (self.steer_angle_gain * angle - self.steer_pos_gain * track_pos)
        steer = max(-1.0, min(1.0, steer / STEER_LOCK_RAD))

        if abs(track_pos) > 1.0:  # off the track: crawl back on, pointed at the road
            return {
                "accel": self.offtrack_throttle,
                "brake": 0.0,
                "steer": steer,
                "gear": min(gear, 3),
                "clutch": 0.0,
            }

        beams = state.get("track", [])
        ahead_m = float(beams[9]) if isinstance(beams, list) and len(beams) == 19 else 200.0
        target_kmh = min(
            self.max_speed_kmh,
            max(self.min_speed_kmh, self.beam_speed_gain * ahead_m),
        )

        deficit = target_kmh - speed_kmh
        if deficit >= 0:  # one pedal at a time, always
            accel, brake = min(1.0, self.accel_gain * deficit), 0.0
        else:
            accel, brake = 0.0, min(1.0, self.brake_gain * -deficit)

        if rpm > self.gear_up_rpm and gear < 6:
            gear += 1
        elif rpm < self.gear_down_rpm and gear > 1:
            gear -= 1

        return {"accel": accel, "brake": brake, "steer": steer, "gear": gear, "clutch": 0.0}
