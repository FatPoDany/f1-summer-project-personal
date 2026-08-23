"""Garage screen: session library, lap table with deltas/status, import and
watched folder. Double-click a lap to open it in Lap Analysis."""

import json
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from apex import theme
from apex.coaching_queue import CoachingProgress, CoachingStage
from f1coach_core import (
    Session,
    StudyIdentity,
    TelemetrySchemaError,
    create_session,
    delete_session,
    import_telemetry,
    latest_coaching_outcomes,
    list_sessions,
    load_session,
)
from f1coach_core.lap import NO_IDENTITY
from f1coach_core.participant import (
    Background,
    background_summary,
    load_background,
    save_background,
)
from f1coach_core.workspace import RECORDING_POINTER, session_recording
from racecoach.telemetry.handover import HandoverError, handovers_root, unpack

# Driver sits beside Lap so a researcher collecting several participants can
# tell whose laps these are without opening the files.
TABLE_HEADERS = ("Lap", "Driver", "Time", "Δ best", "Status")


def _findings(count: int) -> str:
    return "1 finding" if count == 1 else f"{count} findings"


def _analysed_against(analysed: list[tuple[str | None, int]], best, *, is_best: bool) -> str:
    """Every comparison stored for one lap, newest answer per pair.

    A findings count belongs to a pair, not to a lap, so the column cannot carry
    one honestly: this lap read against four references and against nothing is
    five different answers. The column says only that answers exist; this says
    what they are.
    """
    if not analysed:
        return ""
    best_stem = best.source.stem if best is not None else None
    # The pair this row opens into comes first, because it is the answer the
    # reader is about to see; then the other references in name order, with the
    # lap on its own last.
    opens_against = None if is_best else best_stem
    ordered = sorted(
        analysed,
        key=lambda item: (item[0] != opens_against, item[0] is None, item[0] or ""),
    )
    lines = ["Analysed against"]
    for reference, count in ordered:
        if reference is None:
            label = "single lap"
        elif reference == best_stem:
            label = f"{reference} (session best)"
        else:
            label = reference
        lines.append(f"  {label} — {_findings(count)}")
    return "\n".join(lines)


def _handover_identity(folder: Path) -> tuple[StudyIdentity, dict | None]:
    """Who drove this handover, and where its screen recording now lives.

    Read from the capture's own manifest rather than asked for at import time:
    a package opened months later, by somebody who was not there, still says
    what it came with.
    """
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return NO_IDENTITY, None
    if not isinstance(manifest, dict):
        return NO_IDENTITY, None
    preset = manifest.get("study_preset")
    identity = StudyIdentity(
        driver=manifest.get("participant_id") or None,
        phase=manifest.get("phase") or None,
        setup=preset.get("preset_id") if isinstance(preset, dict) else None,
    )
    recording = manifest.get("recording")
    if not isinstance(recording, dict):
        return identity, None
    # The path recorded on the participant's machine means nothing here, but
    # the file itself travelled inside the package.
    local = folder / Path(str(recording.get("path", ""))).name
    return identity, {**recording, "path": str(local)} if local.is_file() else None


def _adopt_background(folder: Path) -> None:
    """Keep the questionnaire that travelled with the laps.

    Nothing in a telemetry file records prior experience, and a comparability
    check months from now cannot go back and ask. Dropping it on import is the
    one loss a handover cannot recover from.
    """
    try:
        data = json.loads((folder / "participant.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    try:
        background = Background.from_dict(data)
    except TypeError:
        return
    if background.participant_id:
        save_background(background)


class GarageView(QWidget):
    lapOpened = Signal(object, object)  # (Lap, Session)
    coachingRequested = Signal(object)  # Session
    sessionDeleted = Signal(str)  # absolute managed session path
    status = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session: Session | None = None
        self._fresh: set[str] = set()  # lap stems imported this run, not yet opened
        self._coaching_progress: dict[Path, CoachingProgress] = {}

        self._session_list = QListWidget()
        self._session_list.currentRowChanged.connect(lambda _row: self._load_selected())
        # Creating and deleting a session are things you do *to* a session, so
        # they belong on it rather than as permanent buttons underneath.
        self._session_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._session_list.customContextMenuRequested.connect(self._session_menu)
        self._session_list.setToolTip("Right-click for new and delete")

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Sessions"))
        left_layout.addWidget(self._session_list, stretch=1)

        # One import. Watching a folder was a second way to do the same thing,
        # from before capture put its laps in the Garage by itself.
        import_button = QPushButton("Import…")
        import_button.clicked.connect(self._import_files)

        # Opening a lap used to be available only through a hidden double-click
        # or context-menu gesture.  That left a selected row beside a disabled
        # Lap Analysis toolbar action and made the AI setup state look broken.
        self._open_button = QPushButton("Analyze selected lap")
        self._open_button.setEnabled(False)
        self._open_button.setToolTip(
            "Open Lap Analysis, including the AI Race Engineer and technique review"
        )
        self._open_button.clicked.connect(self._open_selected)

        self._table = QTableWidget(0, len(TABLE_HEADERS))
        self._table.setHorizontalHeaderLabels(TABLE_HEADERS)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.itemSelectionChanged.connect(self._update_open_state)
        self._table.cellDoubleClicked.connect(self._open_row)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._lap_menu)
        self._table.setToolTip(
            "Select a lap and choose Analyze selected lap; double-click also opens it"
        )

        # Who these laps belong to. A handover carries the driver, the phase, the
        # preset and the questionnaire; before this the app stored all four and
        # showed none of them, so importing one looked like it had lost them.
        self._participant = QLabel()
        self._participant.setWordWrap(True)
        self._participant.setAccessibleName("Participant and study background")

        self._footage_status = QLabel()
        self._footage_status.setWordWrap(True)
        self._footage_status.setAccessibleName("Race-window footage status")

        buttons = QHBoxLayout()
        buttons.addWidget(import_button)
        buttons.addStretch(1)
        buttons.addWidget(self._open_button)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addLayout(buttons)
        right_layout.addWidget(self._participant)
        right_layout.addWidget(self._footage_status)
        right_layout.addWidget(self._table, stretch=1)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 4)
        layout.addWidget(splitter)

    @property
    def session(self) -> Session | None:
        return self._session

    # -- session list -----------------------------------------------------

    def refresh_sessions(self, select: str | None = None) -> None:
        current = select or (
            self._session_list.currentItem().text() if self._session_list.currentItem() else None
        )
        self._session_list.blockSignals(True)
        self._session_list.clear()
        names = [path.name for path in list_sessions()]
        self._session_list.addItems(names)
        self._session_list.blockSignals(False)
        if names:
            row = names.index(current) if current in names else 0
            self._session_list.setCurrentRow(row)  # triggers _load_selected
        else:
            self._session = None
            self._table.setRowCount(0)
            self._participant.clear()
            self._footage_status.clear()
            self._update_open_state()

    def _load_selected(self) -> None:
        item = self._session_list.currentItem()
        if item is None:
            return
        match = next((p for p in list_sessions() if p.name == item.text()), None)
        if match is None:  # deleted outside the app since the last refresh
            self.refresh_sessions()
            return
        self._session = load_session(match)
        self._populate_table()
        self.coachingRequested.emit(self._session)

    def _populate_table(self) -> None:
        session = self._session
        self._table.setRowCount(0)
        if session is None:
            self._footage_status.clear()
            return
        self._update_participant(session)
        self._update_footage_status(session)
        best = session.best_lap
        coached = latest_coaching_outcomes(session.path)
        rows = len(session.laps) + len(session.problems)
        self._table.setRowCount(rows)
        for row, lap in enumerate(session.laps):
            delta = session.delta_to_best(lap)
            is_best = lap is best
            status, status_color, status_tip = self._lap_status(lap, is_best, coached, best)
            cells = (
                lap.label,
                lap.identity.driver or "—",
                f"{lap.lap_time:.3f} s",
                "—" if is_best else f"+{delta:.3f}",
                status,
            )
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if is_best:
                    item.setForeground(QColor(theme.PURPLE))
                elif col == len(TABLE_HEADERS) - 1 and status_color:
                    item.setForeground(QColor(status_color))
                if col == len(TABLE_HEADERS) - 1 and status_tip:
                    item.setToolTip(status_tip)
                self._table.setItem(row, col, item)
        for i, (name, message) in enumerate(session.problems):
            row = len(session.laps) + i
            cells = (Path(name).stem, "", "", "", "unreadable")
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(theme.RED))
                item.setToolTip(message)
                self._table.setItem(row, col, item)
        self._update_open_state()

    def _update_participant(self, session: Session) -> None:
        """Say whose laps these are, or say plainly that the file does not.

        Read from the laps themselves rather than from the folder name, which
        anybody can rename.
        """
        identities = {lap.identity for lap in session.laps if lap.identity}
        if not identities:
            self._participant.setText(
                "No participant recorded — these laps were imported as loose CSVs, "
                "which carry no driver, phase or background."
            )
            self._participant.setStyleSheet(f"color: {theme.TEXT_DIM};")
            return
        parts = []
        for identity in sorted(identities, key=lambda one: (one.driver or "", one.phase or "")):
            who = " · ".join(
                value for value in (identity.driver, identity.phase, identity.setup) if value
            )
            parts.append(f"{who} — {background_summary(load_background(identity.driver or ''))}")
        self._participant.setText("Driver " + "   |   ".join(parts))
        self._participant.setStyleSheet(f"color: {theme.TEXT_DIM};")

    def _update_footage_status(self, session: Session) -> None:
        recording = session_recording(session.path)
        if recording is not None:
            text = (
                "Footage available for synchronized corner review. "
                "AI advice uses telemetry, not video."
            )
            colour = theme.GREEN
        elif (session.path / RECORDING_POINTER).exists():
            text = (
                "The race-window footage link is unavailable. AI advice still "
                "uses the saved telemetry."
            )
            colour = theme.YELLOW
        else:
            text = (
                "No race-window footage is attached to this session. AI advice "
                "uses the saved telemetry."
            )
            colour = theme.TEXT_DIM
        self._footage_status.setText(text)
        self._footage_status.setStyleSheet(f"color: {colour};")

    def _lap_status(
        self, lap, is_best: bool, coached: dict[tuple[str, str | None], int], best
    ) -> tuple[str, str, str]:
        """Combine lap significance with explicit automatic-coaching progress."""
        progress = self._coaching_progress.get(lap.source.resolve(strict=False))
        analysed = [
            (reference, count)
            for (name, reference), count in coached.items()
            if name == lap.source.stem
        ]
        ai_status, ai_colour, detail = self._ai_status(progress, analysed)
        if not detail:
            detail = _analysed_against(analysed, best, is_best=is_best)
        if is_best:
            label = "SESSION BEST" + (f" · {ai_status}" if ai_status else "")
            return label, theme.PURPLE, detail
        if ai_status:
            prefix = "NEW · " if lap.source.stem in self._fresh else ""
            return prefix + ai_status, ai_colour, detail
        if lap.source.stem in self._fresh:
            return "NEW — just captured", theme.GREEN, ""
        return "", "", ""

    @staticmethod
    def _ai_status(
        progress: CoachingProgress | None,
        analysed: list[tuple[str | None, int]],
    ) -> tuple[str, str, str]:
        if progress is None:
            if not analysed:
                return "", "", ""
            return "ANALYSED", theme.GREEN, ""
        labels = {
            CoachingStage.QUEUED: ("AI QUEUED", theme.YELLOW),
            CoachingStage.GENERATING: ("AI GENERATING…", theme.YELLOW),
            CoachingStage.FAILED: ("AI FAILED", theme.RED),
            CoachingStage.SETUP_NEEDED: ("AI SETUP NEEDED", theme.YELLOW),
            CoachingStage.UNAVAILABLE: ("AI UNAVAILABLE", theme.RED),
        }
        if progress.stage is CoachingStage.READY:
            return "ANALYSED", theme.GREEN, progress.message
        label, colour = labels[progress.stage]
        return label, colour, progress.message

    def apply_coaching_progress(self, update: CoachingProgress) -> None:
        """Render a trusted queue update without letting stale sessions mutate data."""
        if not isinstance(update, CoachingProgress):
            return
        source = update.lap_source.resolve(strict=False)
        self._coaching_progress[source] = update
        session = self._session
        if session is not None and any(
            lap.source.resolve(strict=False) == source for lap in session.laps
        ):
            self._populate_table()

    def _open_row(self, row: int, _col: int = 0) -> None:
        if self._session is not None and 0 <= row < len(self._session.laps):
            lap = self._session.laps[row]
            if lap.source.stem in self._fresh:
                self._fresh.discard(lap.source.stem)
                self._populate_table()
            self.lapOpened.emit(lap, self._session)

    def _update_open_state(self) -> None:
        row = self._table.currentRow()
        ready = self._session is not None and 0 <= row < len(self._session.laps)
        self._open_button.setEnabled(ready)

    def _open_selected(self) -> None:
        if self._open_button.isEnabled():
            self._open_row(self._table.currentRow())

    # -- import / watch ------------------------------------------------------

    def _new_session(self) -> None:
        name, ok = QInputDialog.getText(self, "New session", "Session name:")
        name = name.strip()
        if ok and name:
            try:
                create_session(name)
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, "Can't create session", str(exc))
                return
            self.refresh_sessions(select=name)

    def _delete_session(self) -> None:
        session = self._session
        if session is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete session?",
            f"Delete '{session.name}' and its {len(session.laps)} managed lap "
            f"cop{'y' if len(session.laps) == 1 else 'ies'}?\n\n"
            "This also removes coaching audit records stored with the session. "
            "Original telemetry files imported from elsewhere are not deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_session(session.name)
        except (ValueError, OSError) as exc:
            QMessageBox.critical(self, "Can't delete session", str(exc))
            return
        name = session.name
        deleted_path = str(session.path.resolve(strict=False))
        self._session = None
        self.refresh_sessions()
        self.sessionDeleted.emit(deleted_path)
        self.status.emit(f"Deleted session '{name}' from the Apex workspace")

    def _session_menu(self, position) -> None:
        menu = QMenu(self)
        menu.addAction("New session…", self._new_session)
        delete = menu.addAction("Delete session…", self._delete_session)
        delete.setEnabled(self._session is not None)
        menu.exec(self._session_list.mapToGlobal(position))

    def _lap_menu(self, position) -> None:
        row = self._table.rowAt(position.y())
        if self._session is None or row < 0 or row >= len(self._session.laps):
            return
        self._table.selectRow(row)
        menu = QMenu(self)
        menu.addAction("Open", lambda: self._open_row(row, 0))
        menu.addAction("Export CSV…", lambda: self._export_row(row))
        menu.exec(self._table.viewport().mapToGlobal(position))

    def _export_row(self, row: int) -> None:
        """Hand the participant the exact file, not a re-rendering of it.

        What they pass to the research team has to be the recorded lap, header
        and all, so this copies rather than writing the table out again.
        """
        lap = self._session.laps[row]
        target, _ = QFileDialog.getSaveFileName(
            self, "Export lap", lap.source.name, "Telemetry CSV (*.csv)"
        )
        if not target:
            return
        try:
            shutil.copyfile(lap.source, target)
        except OSError as exc:
            QMessageBox.critical(self, "Can't export lap", str(exc))
            return
        self.status.emit(f"Exported {lap.label} -> {target}")

    def _import_files(self) -> None:
        chosen, _ = QFileDialog.getOpenFileNames(
            self,
            "Import telemetry or a participant handover",
            "",
            "Telemetry and handovers (*.csv *.zip);;Telemetry CSV (*.csv);;"
            "Participant handover (*.zip)",
        )
        if not chosen:
            return
        paths = [Path(path) for path in chosen]
        # A handover names its own session after the participant, so only loose
        # CSVs need somewhere to be put. Asking for a session name first, then
        # not using it, is worse than not asking.
        if any(path.suffix.lower() != ".zip" for path in paths) and self._session is None:
            self._new_session()
            if self._session is None:
                return
        selected = None
        for path in paths:
            if path.suffix.lower() == ".zip":
                selected = self.import_handover(path) or selected
            else:
                self._import_one(path)
        self.refresh_sessions(select=selected)

    def import_handover(self, archive: Path) -> str | None:
        """Import a participant's whole handover: their laps, and who they are.

        A loose CSV carries the driving and nothing else -- not who drove, not
        which phase they were in, not the background they answered. The package
        exists precisely to keep those together, so importing it keeps them
        together too, and the digests recorded when it was packaged are checked
        on the way in.

        The laps land in a session of their own named after the participant.
        Folding somebody else's run into whichever session happened to be
        selected would destroy the provenance the package was built to carry.
        """
        try:
            handover = unpack(archive, handovers_root())
        except HandoverError as exc:
            QMessageBox.critical(self, "Can't import handover", str(exc))
            return None
        identity, recording = _handover_identity(handover.path)
        _adopt_background(handover.path)
        runs = sorted(handover.path.glob("*.csv"))
        if not runs:
            QMessageBox.critical(
                self,
                "Can't import handover",
                f"{archive.name} unpacked, but holds no telemetry CSV.",
            )
            return None
        # unpack() prefixes the folder with the participant id, and a capture
        # folder is already named after them, so the folder name on its own
        # reads "P007-P007-baseline-...". Use the capture's own name.
        name = handover.path.name
        doubled = f"{handover.participant_id}-" * 2
        if handover.participant_id and name.startswith(doubled):
            name = name[len(handover.participant_id) + 1 :]
        summaries = []
        for run in runs:
            try:
                summaries.append(
                    import_telemetry(run, name, identity=identity, recording=recording)
                )
            except (TelemetrySchemaError, OSError) as exc:
                QMessageBox.critical(self, "Can't import handover", str(exc))
                return None
            self._fresh.add(run.stem)
        who = identity.driver or "an unnamed driver"
        self.status.emit(f"Imported {archive.name} from {who} — " + " · ".join(summaries))
        return name

    def _import_one(self, path: Path) -> None:
        assert self._session is not None
        try:
            summary = import_telemetry(path, self._session.name)
        except (TelemetrySchemaError, OSError) as exc:
            QMessageBox.critical(self, "Can't import telemetry", str(exc))
        else:
            # the copy usually keeps the stem; on rename-collisions the NEW
            # tag is merely missed — it's a hint, not part of the audit trail
            self._fresh.add(path.stem)
            self.status.emit(summary)
