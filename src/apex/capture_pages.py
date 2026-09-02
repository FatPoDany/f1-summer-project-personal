"""Presentation-only pages used by the human telemetry collection guide."""

import os
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from apex import theme
from racecoach.telemetry.human_capture import TorcsStudyPreset


class CaptureSetupPage(QWidget):
    def __init__(
        self,
        torcs_binary: Path,
        study_preset: TorcsStudyPreset,
        *,
        session_issue: str | None = None,
    ) -> None:
        super().__init__()
        self.torcs_binary = torcs_binary
        self.study_preset = study_preset
        self.session_issue = session_issue
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)

        identity_group = QGroupBox("Session details")
        identity_form = QFormLayout(identity_group)
        participant_label = QLabel("Participant ID (required)")
        self.participant_id = QLineEdit()
        self.participant_id.setPlaceholderText("Example: P001 — never enter a name or email")
        self.participant_id.setMaxLength(64)
        self.participant_id.setAccessibleName("Pseudonymous participant ID")
        participant_label.setBuddy(self.participant_id)
        identity_form.addRow(participant_label, self.participant_id)

        phase_label = QLabel("Study phase")
        self.phase = QComboBox()
        # Control is the arm that makes the comparison mean anything. Without a
        # group that drives the second run having been given no advice, a
        # participant getting quicker between baseline and coached is explained
        # just as well by having driven the same track three more times, and
        # nothing in the data can separate the two.
        #
        # Each label says both when the run happens and what the participant was
        # given, because "no coaching" on its own is true of the baseline and of
        # the control alike: what distinguishes them is the point in the session,
        # not the absence of advice. A facilitator reading the list with somebody
        # already sitting at the wheel has to be able to tell them apart at a
        # glance, and picking the wrong one mislabels a whole session.
        self.phase.addItem("Baseline — first run, before any advice", "baseline")
        self.phase.addItem("Coached — second run, after AI advice", "coached")
        self.phase.addItem(
            "Control — second run, own practice only, no AI advice", "control"
        )
        self.phase.addItem("Familiarisation — not measured", "familiarisation")
        self.phase.setAccessibleName("Study phase")
        phase_label.setBuddy(self.phase)
        identity_form.addRow(phase_label, self.phase)

        preset_group = QGroupBox("Assigned driving setup")
        preset_layout = QVBoxLayout(preset_group)
        self.preset_summary = QLabel(
            f"{study_preset.display_name}  ·  "
            f"{study_preset.track_id} ({study_preset.track_category})  ·  "
            f"{study_preset.car_id}  ·  {study_preset.laps} laps"
        )
        self.preset_summary.setWordWrap(True)
        self.preset_summary.setAccessibleName("Assigned track, car, and lap count")
        preset_help = QLabel(
            "Apex locks this setup for every participant and opens the race directly."
        )
        preset_help.setWordWrap(True)
        preset_help.setStyleSheet(f"color: {theme.TEXT_DIM};")
        preset_layout.addWidget(self.preset_summary)
        preset_layout.addWidget(preset_help)

        simulator_group = QGroupBox("Simulator check")
        simulator_layout = QVBoxLayout(simulator_group)
        self.simulator_status = QLabel()
        self.simulator_status.setWordWrap(True)
        self.simulator_help = QLabel()
        self.simulator_help.setWordWrap(True)
        self.simulator_help.setStyleSheet(f"color: {theme.TEXT_DIM};")
        simulator_layout.addWidget(self.simulator_status)
        simulator_layout.addWidget(self.simulator_help)
        self._render_simulator_status()

        readiness_group = QGroupBox("Before opening the simulator")
        readiness_layout = QVBoxLayout(readiness_group)
        readiness = (
            "The keyboard, gamepad, or wheel is connected and tested.",
            "The space is quiet and the participant is ready to begin.",
            "The assigned study phase and participant ID are correct.",
        )
        self.readiness_checks = []
        for text in readiness:
            check = QCheckBox(text)
            readiness_layout.addWidget(check)
            self.readiness_checks.append(check)

        self.form_error = QLabel()
        self.form_error.setWordWrap(True)
        self.form_error.setAccessibleName("Collection setup status")
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.start_button = QPushButton("Open TORCS and start recording")
        self.start_button.setObjectName("primary")
        self.start_button.setMinimumHeight(44)
        buttons.addWidget(self.start_button)

        layout.addWidget(identity_group)
        layout.addWidget(preset_group)
        layout.addWidget(simulator_group)
        layout.addWidget(readiness_group)
        layout.addWidget(self.form_error)
        layout.addStretch(1)
        layout.addLayout(buttons)

    @property
    def simulator_ready(self) -> bool:
        return (
            self.torcs_binary.is_file()
            and os.access(self.torcs_binary, os.X_OK)
            and self.study_preset.race_config.is_file()
            and self.session_issue is None
        )

    def show_message(self, text: str, *, error: bool = False) -> None:
        self.form_error.setText(text)
        colour = theme.RED if error else theme.TEXT_DIM
        self.form_error.setStyleSheet(f"color: {colour};")

    def _render_simulator_status(self) -> None:
        binary_ready = self.torcs_binary.is_file() and os.access(self.torcs_binary, os.X_OK)
        if not binary_ready:
            self.simulator_status.setText("Simulator component is not available.")
            self.simulator_status.setStyleSheet(f"color: {theme.RED};")
            self.simulator_help.setText(
                "Ask the study facilitator to install the complete Apex build "
                "before collecting data."
            )
        elif not self.study_preset.race_config.is_file():
            self.simulator_status.setText("Assigned study preset is not available.")
            self.simulator_status.setStyleSheet(f"color: {theme.RED};")
            self.simulator_help.setText(
                "Ask the study facilitator to repair or reinstall the complete Apex build."
            )
        elif self.session_issue is not None:
            self.simulator_status.setText(
                "Simulator recording is unavailable in this Remote Desktop session."
            )
            self.simulator_status.setStyleSheet(f"color: {theme.RED};")
            self.simulator_help.setText(self.session_issue)
        else:
            self.simulator_status.setText("Ready — the Apex simulator component is available.")
            self.simulator_status.setStyleSheet(f"color: {theme.GREEN};")
            self.simulator_help.setText(
                "Apex will open the assigned TORCS race automatically when collection starts."
            )


class CaptureDrivePage(QWidget):
    def __init__(self, study_preset: TorcsStudyPreset) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.heading = QLabel("Opening TORCS — recording waits for the race window")
        self.heading.setStyleSheet("font-size: 18px; font-weight: 600;")
        detail = QLabel(
            "Complete these steps in the TORCS window. Apex will finish the import "
            "automatically when TORCS closes."
        )
        detail.setWordWrap(True)
        detail.setStyleSheet(f"color: {theme.TEXT_DIM};")
        instructions = (
            f"1. Apex loaded {study_preset.track_id} with {study_preset.car_id} automatically.",
            "2. Use the connected input device; no track or car selection is required.",
            "3. Complete the familiarisation or measured laps for this phase.",
            "4. Exit TORCS when finished; do not search for or move any CSV files.",
        )
        guide = QGroupBox("Driving guide")
        guide_layout = QVBoxLayout(guide)
        for instruction in instructions:
            label = QLabel(instruction)
            label.setWordWrap(True)
            guide_layout.addWidget(label)
        self.status = QLabel("Launching the simulator…")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {theme.YELLOW};")
        self.stop_button = QPushButton("Stop this collection")
        self.stop_button.setObjectName("danger")
        self.stop_button.setMinimumHeight(44)
        layout.addWidget(self.heading)
        layout.addWidget(detail)
        layout.addWidget(guide)
        layout.addWidget(self.status)
        layout.addStretch(1)
        layout.addWidget(self.stop_button)


class CaptureCompletePage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        heading = QLabel("Driving data saved")
        heading.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {theme.GREEN};")
        self.result_summary = QLabel()
        self.result_summary.setWordWrap(True)
        self.result_path = QLabel()
        self.result_path.setWordWrap(True)
        self.result_path.setStyleSheet(f"color: {theme.TEXT_DIM};")
        # The participant is the one holding the only copy at this point, so the
        # hand-over instruction has to be on the screen they actually end on.
        next_steps = QLabel(
            "Your laps are already in the Garage on this computer — nothing further "
            "is needed to save them.\n\n"
            "To pass them to the research team, use the button below. It puts this "
            "whole session into one file you can email or copy, with a checksum of "
            "every part so the team can tell if anything was lost on the way."
        )
        next_steps.setWordWrap(True)
        buttons = QHBoxLayout()
        # First and widest: for the study, handing the data over is the point of
        # the session, and it is the one step nothing else in Apex can do for them.
        self.package_button = QPushButton("Save a file to send")
        self.package_button.setObjectName("primary")
        self.package_button.setMinimumHeight(44)
        self.package_button.setToolTip(
            "One file containing this session, checksummed so damage in transit shows up"
        )
        self.new_session_button = QPushButton("Collect another session")
        self.new_session_button.setObjectName("quiet")
        self.new_session_button.setMinimumHeight(44)
        self.open_results_button = QPushButton("View my laps")
        self.open_results_button.setObjectName("primary")
        self.open_results_button.setMinimumHeight(44)
        buttons.addWidget(self.package_button)
        buttons.addWidget(self.new_session_button)
        buttons.addWidget(self.open_results_button)
        buttons.addStretch(1)
        layout.addWidget(heading)
        layout.addWidget(self.result_summary)
        layout.addWidget(self.result_path)
        layout.addWidget(next_steps)
        layout.addStretch(1)
        layout.addLayout(buttons)
