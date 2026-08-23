"""What we know about a participant before they drive, and why it is recorded.

A comparative study only means anything if the groups were comparable to begin
with. If the coached group happened to contain the people who already play racing
games, their better lap times say nothing about coaching. So prior experience is
recorded once per participant, before their first session, and travels with their
telemetry -- a comparability check run months later on pooled data cannot go back
and ask.

Everything here is deliberately coarse and closed-ended. Ordinal bands are enough
to test whether two groups are balanced, and they cannot re-identify anybody the
way free text or an exact age could. Nothing is required: a participant who
declines an item is still a participant, and a missing answer is recorded as
missing rather than as a middle value nobody gave.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from f1coach_core.workspace import workspace_root

SCHEMA_VERSION = "apex-participant-v1"

# Ordinal, low to high. Order is part of the contract: an analyst ranking these
# has to be able to trust that index 0 is less experience than index 1.
RACING_GAME_BANDS = (
    "never",
    "a few times",
    "monthly",
    "weekly",
    "most days",
)
SIM_RACING_BANDS = (
    "none",
    "casual",
    "regular with a wheel",
)
DRIVING_BANDS = (
    "no licence",
    "licence, rarely drive",
    "licence, drive regularly",
)
AGE_BANDS = ("18-24", "25-34", "35-44", "45+")

DECLINED = ""  # an answer not given, kept distinct from any answer that was


@dataclass(frozen=True)
class Background:
    """One participant's prior experience, as coarse ordinal bands."""

    participant_id: str
    racing_games: str = DECLINED
    sim_racing: str = DECLINED
    driving: str = DECLINED
    age_band: str = DECLINED
    recorded_at: str = ""
    schema_version: str = field(default=SCHEMA_VERSION)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Background":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    @property
    def is_answered(self) -> bool:
        """Whether anything was actually given, as against a form left untouched."""
        return any(
            getattr(self, name) != DECLINED
            for name in ("racing_games", "sim_racing", "driving", "age_band")
        )

    def rank(self, field_name: str) -> int | None:
        """The ordinal index of an answer, or None when it was not given.

        Returned as a rank rather than a label because comparing groups means
        ordering, and an analyst should not have to re-derive the order from
        strings whose wording might change.
        """
        bands = {
            "racing_games": RACING_GAME_BANDS,
            "sim_racing": SIM_RACING_BANDS,
            "driving": DRIVING_BANDS,
            "age_band": AGE_BANDS,
        }[field_name]
        value = getattr(self, field_name)
        return bands.index(value) if value in bands else None


def backgrounds_root() -> Path:
    return workspace_root() / "participants"


def background_path(participant_id: str) -> Path:
    return backgrounds_root() / f"{participant_id}.json"


def load_background(participant_id: str) -> Background | None:
    """The stored answers for this participant, or None if never asked."""
    try:
        data = json.loads(background_path(participant_id).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return Background.from_dict(data)
    except TypeError:
        return None


def save_background(background: Background) -> Path:
    path = background_path(background.participant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(background.to_dict(), indent=2), encoding="utf-8")
    return path


BACKGROUND_COLUMNS = (
    "racing_games",
    "sim_racing",
    "driving",
    "age_band",
)


BACKGROUND_LABELS = {
    "racing_games": "racing games",
    "sim_racing": "sim racing",
    "driving": "driving",
    "age_band": "age",
}


def background_summary(background: Background | None) -> str:
    """The questionnaire as one line a person can read.

    The answers exist to make a comparison defensible, and a stored file nobody
    can see does not do that. Distinguishes never asked from asked and declined:
    they are different facts about the study, and only one of them can be fixed.
    """
    if background is None:
        return "background not recorded"
    if not background.is_answered:
        return "background questionnaire returned no answers"
    given = [
        f"{BACKGROUND_LABELS[name]} {getattr(background, name)}"
        for name in BACKGROUND_COLUMNS
        if getattr(background, name) != DECLINED
    ]
    return " · ".join(given)


def background_columns(background: Background | None) -> dict:
    """Flat columns for the study export, labels and ranks side by side.

    Both, because they answer different questions: the label is what a reader of
    the table understands, and the rank is what a comparability test operates on.
    """
    columns: dict[str, object] = {}
    for name in BACKGROUND_COLUMNS:
        label = getattr(background, name) if background is not None else DECLINED
        rank = background.rank(name) if background is not None else None
        columns[name] = label
        columns[f"{name}_rank"] = "" if rank is None else rank
    return columns
