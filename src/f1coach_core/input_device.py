"""What the participant was actually driving with, read off their own telemetry.

Advice that asks for something the hardware cannot do is not advice. "Build
brake pressure smoothly" is a sentence about a pedal; on a keyboard the brake
ramps from nothing to everything at a rate the game chooses, and the only thing
under the driver's control is when the key goes down and when it comes up. A
participant given the first sentence has not been told anything they can act
on, and in a study they then enter the compliance measure as somebody who was
told something and did not do it -- which is the same confound ``adherence``
exists to remove, except that here it is guaranteed rather than possible.

Nothing records the device. The capture manifest fixes the track, the car and
the lap count (``human_capture.default_study_preset``) and says nothing about
the controls, so the only witness is the driving itself.

**The signature is a machine artefact, not a style.** Held keys are ramped by
the game at a fixed rate, so steering moves at one speed or not at all. Across
all eighteen real laps recorded so far -- five study runs and the sample
session -- 94 to 95 per cent of every steering change happens within a tenth of
one rate, about 1.23 per second. A hand on a wheel or a stick does not turn at
a constant angular rate for nineteen samples in twenty; nothing but a ramp
does.

That is why the threshold is one-sided and set far below what was measured. A
wrong ``analogue`` verdict costs the phrasing this module exists to improve; a
wrong ``keyboard`` verdict tells somebody about arrow keys they are not
holding, which is visibly wrong to them and worth avoiding at the cost of the
occasional miss.
"""

from dataclasses import dataclass

import numpy as np

from f1coach_core.lap import Lap

KEYBOARD = "keyboard"
ANALOGUE = "analogue"
UNKNOWN = "unknown"

# Within a tenth of the median rate counts as "the same rate": the ramp is
# fixed, but the sampling is not aligned to it, so the first and last step of
# each press are partial.
RATE_TOLERANCE = 0.1
# Measured at 0.94-0.95 on every real lap. Set here so that only a driver whose
# hand held one angular rate for four steering changes in five is mistaken for
# a keyboard, which is not a thing hands do.
KEYBOARD_RATE_SHARE = 0.8
# A lap that barely steered has no signature to read. Roughly a second of a
# 45 Hz capture; below this the share is a coincidence rather than a habit.
MIN_MOVING_SAMPLES = 50


@dataclass(frozen=True)
class InputDevice:
    """The verdict, and the measurement it was reached by.

    The evidence travels with the answer because this is an inference about a
    participant's setup made after they went home. A researcher who disagrees
    with the verdict should be able to see exactly what it was based on rather
    than take it on trust.
    """

    kind: str
    steady_rate_share: float | None  # of steering changes, how many shared one rate
    moving_samples: int

    @property
    def is_digital(self) -> bool:
        """Whether pedal advice must be about presses rather than pressure."""
        return self.kind == KEYBOARD


UNKNOWN_DEVICE = InputDevice(kind=UNKNOWN, steady_rate_share=None, moving_samples=0)


def detect_input_device(lap: Lap) -> InputDevice:
    """Read the input device off one lap of steering.

    Steering rather than a pedal because it is the channel a driver uses
    continuously and in both directions, and because the pedals in this game
    ramp at rates that differ going up and coming down, which blurs the same
    test.
    """
    frame = lap.df
    if "steer" not in frame.columns or "t" not in frame.columns:
        return UNKNOWN_DEVICE

    times = frame["t"].to_numpy(dtype=float)
    steer = frame["steer"].to_numpy(dtype=float)
    if len(times) < 2:
        return UNKNOWN_DEVICE

    span = np.diff(times)
    with np.errstate(divide="ignore", invalid="ignore"):
        rate = np.abs(np.diff(steer) / span)
    rate = rate[np.isfinite(rate)]
    moving = rate[rate > 1e-6]
    if len(moving) < MIN_MOVING_SAMPLES:
        return InputDevice(
            kind=UNKNOWN, steady_rate_share=None, moving_samples=int(len(moving))
        )

    middle = float(np.median(moving))
    share = float(np.mean(np.abs(moving - middle) <= RATE_TOLERANCE * middle))
    return InputDevice(
        kind=KEYBOARD if share >= KEYBOARD_RATE_SHARE else ANALOGUE,
        steady_rate_share=round(share, 3),
        moving_samples=int(len(moving)),
    )
