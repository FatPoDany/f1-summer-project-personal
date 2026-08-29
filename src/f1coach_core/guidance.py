"""The sentences a participant actually reads, in one place, in plain English.

Every numeric claim in a finding is a JSON-schema constant checked against the
telemetry, and the prose is overwritten from this table before validation
(``llm._ground_model_prose``). So this file is the coaching: the model chooses
which corner and which measurement, and these words are what the choice turns
into on somebody's screen.

Two things were wrong with the sentences this replaces.

*They were written in the register of the fact-checker rather than the driver.*
"The cited brake-onset position is displaced toward the approach" is a true
sentence about a data structure. The people reading it have driven a racing
game a few times, hold a licence and rarely drive (``participant.json``), and
have somewhere between one and three laps to do something about it.

*They asked for a pedal.* The study ships its own TORCS runtime, and its
``drivers/human/preferences.xml`` binds throttle to the Up Arrow and brake to
the Down Arrow; every lap recorded so far was driven that way, and
``input_device`` reads it back out of the steering trace rather than assuming
it. On a keyboard the brake goes from nothing to everything at a rate the game
picks. "Build brake pressure smoothly" is then not hard advice, it is
impossible advice, and a participant who could not follow it is recorded by
``adherence`` as one who did not.

So fourteen of the fifteen have a second version -- every one except corner
entry speed, which was already about where the car is rather than about a foot.
The digital ones never ask for pressure: only for when a key goes down, when it
comes up, and how long it is held, which is the whole of what that driver
controls.

**No digits anywhere.** ``coach._require_measurement_free_prose`` rejects any
prose containing one, because the measurements belong in the citation where
they can be checked, not in a sentence where they cannot.
"""

from dataclasses import dataclass

from f1coach_core.input_device import KEYBOARD

# What the driver is compared against, said the same way in both analysis
# modes. Against a quicker lap it is that lap; against the per-corner bests it
# is whichever lap went through this corner quickest. "Quicker through here" is
# true of both, and neither asks the reader to hold a data model in their head.
THAN_BEST = "than on your quicker run through here"


@dataclass(frozen=True)
class Guidance:
    """One finding's three sentences: what, why, and what to try."""

    issue: str
    cause: str
    action: str


# -- written for a pedal, and correct for one --------------------------------

ANALOGUE: dict[str, Guidance] = {
    "brake_point": Guidance(
        f"You start braking earlier {THAN_BEST}.",
        "You are on the brakes before you need to be on the way in.",
        "Try braking a little later, and squeeze the pedal on rather than stabbing at it.",
    ),
    "peak_brake": Guidance(
        f"You brake harder or softer {THAN_BEST}.",
        "The pedal reaches a different peak through this corner.",
        "Build the pressure up to your hardest braking rather than going straight there.",
    ),
    "brake_release": Guidance(
        f"You come off the brakes at a different point {THAN_BEST}.",
        "The brake stays on for a different part of the corner.",
        "Ease the pedal off as you turn in rather than lifting off it all at once.",
    ),
    "entry_speed": Guidance(
        f"You arrive at this corner more slowly {THAN_BEST}.",
        "Speed is being lost before the corner rather than in it.",
        "Keep the approach the same every lap and try carrying a little more speed in.",
    ),
    "min_speed": Guidance(
        f"You slow down more at the slowest part of this corner {THAN_BEST}.",
        "More speed is coming off in the middle than the corner needs.",
        "Ease the brake off sooner so the car keeps rolling through the middle.",
    ),
    "min_speed_point": Guidance(
        f"You are at your slowest at a different place {THAN_BEST}.",
        "The point where the car stops slowing down has moved along the corner.",
        "Get the slowing done earlier so the car is already turning at its slowest point.",
    ),
    "exit_speed": Guidance(
        f"You leave this corner more slowly {THAN_BEST}.",
        "The car is not picking up speed as early on the way out.",
        "Aim for a tidy exit and pick the throttle up as the steering comes back.",
    ),
    "throttle_reapply": Guidance(
        f"You get back on the throttle later {THAN_BEST}.",
        "The car spends longer waiting before it starts pulling again.",
        "Start squeezing the throttle on a little earlier as the corner opens up.",
    ),
    "throttle_point": Guidance(
        f"You reach half throttle later {THAN_BEST}.",
        "The throttle is coming on slowly once you have started it.",
        "Feed the throttle on more confidently as the car settles on the way out.",
    ),
    "full_throttle": Guidance(
        f"You reach full throttle later {THAN_BEST}.",
        "It takes longer to get the pedal all the way down on the way out.",
        "Keep feeding the throttle in smoothly until it is flat rather than pausing.",
    ),
    "exit_throttle": Guidance(
        f"You are using less throttle on the way out {THAN_BEST}.",
        "The pedal is not as far down at the exit of this corner.",
        "Commit to the throttle a little more once the car is pointing down the road.",
    ),
    "coast_distance": Guidance(
        f"You spend longer off both pedals {THAN_BEST}.",
        "There is a gap between letting the brake go and picking the throttle up.",
        "Roll off the brake straight into the throttle so the car is never just coasting.",
    ),
    "brake_applications": Guidance(
        "You go back to the brake more than once on the way into this corner.",
        "The braking is broken into separate presses rather than one.",
        "Try one squeeze of the brake and one steady release for the whole corner.",
    ),
    "throttle_applications": Guidance(
        "You go back to the throttle more than once on the way out of this corner.",
        "The throttle is lifted and picked up again as the car is leaving.",
        "Feed the throttle on once and keep it coming as the steering unwinds.",
    ),
    "pedal_overlap": Guidance(
        "You are on the brake and the throttle at the same time here.",
        "Both pedals are down together through part of this corner.",
        "Finish with the brake before the throttle starts, with a clean gap between them.",
    ),
}


# -- the same findings, for a driver who has keys and not pedals --------------
#
# Only the sentences that were about pressure change. Where the current text
# already asks for timing -- which is all a key gives you -- the analogue
# wording is reused rather than duplicated, so the two cannot drift.

DIGITAL: dict[str, Guidance] = {
    "brake_point": Guidance(
        f"You start braking earlier {THAN_BEST}.",
        "The Down Arrow goes down before you need it on the way in.",
        "Try leaving the Down Arrow alone for a moment longer on the approach.",
    ),
    "peak_brake": Guidance(
        f"You use a different amount of brake here {THAN_BEST}.",
        "On a keyboard how much brake you get comes from how long the Down Arrow is"
        " held down, not from how far it goes.",
        "Change how long you hold the Down Arrow rather than how you press it.",
    ),
    "brake_release": Guidance(
        f"You let go of the brake at a different point {THAN_BEST}.",
        "The Down Arrow stays down for a different part of the corner.",
        "Let the Down Arrow up as you start to turn rather than carrying it into the corner.",
    ),
    "min_speed": Guidance(
        f"You slow down more at the slowest part of this corner {THAN_BEST}.",
        "The Down Arrow is held past the point where the car has slowed enough.",
        "Let go of the Down Arrow sooner so the car keeps rolling through the middle.",
    ),
    "min_speed_point": Guidance(
        f"You are at your slowest at a different place {THAN_BEST}.",
        "The point where the car stops slowing down has moved along the corner.",
        "Get off the Down Arrow earlier so the car is already turning at its slowest point.",
    ),
    "exit_speed": Guidance(
        f"You leave this corner more slowly {THAN_BEST}.",
        "The Up Arrow goes down late, so the car starts pulling late.",
        "Aim for a tidy exit and get on the Up Arrow as the steering comes back.",
    ),
    "throttle_reapply": Guidance(
        f"You get back on the throttle later {THAN_BEST}.",
        "The car spends longer waiting before the Up Arrow goes down.",
        "Press the Up Arrow a little earlier as the corner opens up.",
    ),
    "throttle_point": Guidance(
        f"You reach half throttle later {THAN_BEST}.",
        "On a keyboard the throttle builds while the Up Arrow is held, and yours is"
        " picked up late.",
        "Get on the Up Arrow earlier and hold it rather than tapping at it.",
    ),
    "full_throttle": Guidance(
        f"You reach full throttle later {THAN_BEST}.",
        "The Up Arrow is not held long enough in one go to get all the way there.",
        "Once the car is straight, hold the Up Arrow down and leave it down.",
    ),
    "exit_throttle": Guidance(
        f"You are using less throttle on the way out {THAN_BEST}.",
        "The Up Arrow is being released or tapped at the exit rather than held.",
        "Hold the Up Arrow once the car is pointing down the road.",
    ),
    "coast_distance": Guidance(
        f"You spend longer off both arrows {THAN_BEST}.",
        "There is a gap between letting the Down Arrow up and putting the Up Arrow down.",
        "Move straight from one to the other so the car is never just coasting.",
    ),
    # Deliberately not "brake once". Short presses are how a keyboard brakes
    # gently at all, so telling this driver to use a single application asks
    # them to give up their only way of braking less than fully -- and the
    # study's own laps show no sign that extra presses cost time: corners with
    # more of them than the reference lost less, not more. The ask that is
    # still safe is about where the presses happen, not how many there are.
    "brake_applications": Guidance(
        "You go back to the brake more than once on the way into this corner.",
        "Short presses of the Down Arrow are how a keyboard brakes gently, so more"
        " than one is not wrong in itself.",
        "Try to have the braking finished before you turn in, rather than adding more"
        " once the car is already in the corner.",
    ),
    "throttle_applications": Guidance(
        "You go back to the throttle more than once on the way out of this corner.",
        "Tapping the Up Arrow is how a keyboard uses part throttle, so more than one"
        " press is not wrong in itself.",
        "As the steering comes back, aim to settle on holding the Up Arrow rather than"
        " tapping it.",
    ),
    "pedal_overlap": Guidance(
        "You are holding both arrows at the same time here.",
        "The Up Arrow goes down before the Down Arrow has come up.",
        "Let the Down Arrow up first, then press the Up Arrow, with a clean gap between.",
    ),
}

# Single-lap analysis has no reference, so a coasting finding cannot be phrased
# as a comparison. Same fact, said without one.
SINGLE_LAP: dict[str, Guidance] = {
    "coast_distance": Guidance(
        "You spend a long stretch of this corner off both pedals.",
        "There is a gap between letting the brake go and picking the throttle up.",
        "Roll off the brake straight into the throttle so the car is never just coasting.",
    ),
}

SINGLE_LAP_DIGITAL: dict[str, Guidance] = {
    "coast_distance": Guidance(
        "You spend a long stretch of this corner off both arrows.",
        "There is a gap between letting the Down Arrow up and putting the Up Arrow down.",
        "Move straight from one to the other so the car is never just coasting.",
    ),
}


def guidance_for(
    metric: str, *, device: str = "", single_lap: bool = False
) -> Guidance | None:
    """The three sentences for one metric, phrased for the device in hand.

    ``device`` unknown falls back to the pedal wording, which is the wording
    that existed before any of this and is wrong only in a way that was already
    being shipped. Nothing here invents a verdict: the caller passes what
    ``input_device.detect_input_device`` measured, or nothing at all.
    """
    digital = device == KEYBOARD
    if single_lap:
        table = SINGLE_LAP_DIGITAL if digital else SINGLE_LAP
        found = table.get(metric)
        if found is not None:
            return found
    if digital:
        found = DIGITAL.get(metric)
        if found is not None:
            return found
    return ANALOGUE.get(metric)
