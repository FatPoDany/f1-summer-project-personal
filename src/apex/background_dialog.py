"""The few questions asked once, before a participant's first session.

Asked before driving rather than after, so that knowing how they did cannot
colour how they describe themselves. Asked once per participant id, because
repeating it every session is a way to collect contradictions rather than data.

Every item can be left unanswered. A participant is entitled to skip a question
about themselves, and a study that forces an answer gets a made-up one.
"""

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
)

from f1coach_core.participant import (
    AGE_BANDS,
    DECLINED,
    DRIVING_BANDS,
    RACING_GAME_BANDS,
    SIM_RACING_BANDS,
    Background,
)

PREFER_NOT_TO_SAY = "Prefer not to say"


class BackgroundDialog(QDialog):
    """Prior experience, in coarse bands, for checking the groups were comparable."""

    def __init__(self, participant_id: str, existing: Background | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Before you drive")
        self._participant_id = participant_id

        intro = QLabel(
            "Two minutes, once. This tells the research team whether the groups "
            "being compared started out similar — it is not a test, and it does "
            "not affect what you do next. Every question can be skipped."
        )
        intro.setWordWrap(True)

        form = QFormLayout()
        self._racing = _band_box(RACING_GAME_BANDS, existing and existing.racing_games)
        self._sim = _band_box(SIM_RACING_BANDS, existing and existing.sim_racing)
        self._driving = _band_box(DRIVING_BANDS, existing and existing.driving)
        self._age = _band_box(AGE_BANDS, existing and existing.age_band)
        form.addRow("How often do you play racing games?", self._racing)
        form.addRow("Sim racing experience", self._sim)
        form.addRow("Driving on the road", self._driving)
        form.addRow("Age", self._age)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Skip")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def background(self) -> Background:
        from datetime import UTC, datetime

        return Background(
            participant_id=self._participant_id,
            racing_games=_value(self._racing),
            sim_racing=_value(self._sim),
            driving=_value(self._driving),
            age_band=_value(self._age),
            recorded_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )


def _band_box(bands: tuple[str, ...], current: str | None) -> QComboBox:
    box = QComboBox()
    # First and preselected, so leaving the form alone records a declined answer
    # rather than silently asserting whatever happened to be at the top.
    box.addItem(PREFER_NOT_TO_SAY, DECLINED)
    for band in bands:
        box.addItem(band, band)
    if current:
        index = box.findData(current)
        if index >= 0:
            box.setCurrentIndex(index)
    return box


def _value(box: QComboBox) -> str:
    return box.currentData() or DECLINED
