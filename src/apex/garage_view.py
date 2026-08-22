"""Garage screen: session library, lap table with deltas/status, import and
watched folder. Double-click a lap to open it in Lap Analysis."""

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
    TelemetrySchemaError,
    create_session,
    delete_session,
    import_telemetry,
    latest_coaching_outcomes,
    list_sessions,
    load_session,
)
from f1coach_core.workspace import RECORDING_POINTER, session_recording

# Driver sits beside Lap so a researcher collecting several participants can
# tell whose laps these are without opening the files.
TABLE_HEADERS = ("Lap", "Driver", "Time", "Δ best", "Status")


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

        self._table = QTableWidget(0, len(TABLE_HEADERS))
        self._table.setHorizontalHeaderLabels(TABLE_HEADERS)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.cellDoubleClicked.connect(self._open_row)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._lap_menu)
        self._table.setToolTip("Double-click to open, right-click to export")

        self._footage_status = QLabel()
        self._footage_status.setWordWrap(True)
        self._footage_status.setAccessibleName("Race-window footage status")

        buttons = QHBoxLayout()
        buttons.addWidget(import_button)
        buttons.addStretch(1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addLayout(buttons)
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
            self._footage_status.clear()

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
        self._update_footage_status(session)
        best = session.best_lap
        coached = latest_coaching_outcomes(session.path)
        rows = len(session.laps) + len(session.problems)
        self._table.setRowCount(rows)
        for row, lap in enumerate(session.laps):
            delta = session.delta_to_best(lap)
            is_best = lap is best
            status, status_color, status_tip = self._lap_status(lap, is_best, coached)
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
        self, lap, is_best: bool, coached: dict[str, int]
    ) -> tuple[str, str, str]:
        """Combine lap significance with explicit automatic-coaching progress."""
        progress = self._coaching_progress.get(lap.source.resolve(strict=False))
        ai_status, ai_colour, detail = self._ai_status(progress, coached.get(lap.source.stem))
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
        progress: CoachingProgress | None, saved_findings: int | None
    ) -> tuple[str, str, str]:
        if progress is None:
            if saved_findings is None:
                return "", "", ""
            return f"AI READY · {saved_findings} tips", theme.GREEN, ""
        labels = {
            CoachingStage.QUEUED: ("AI QUEUED", theme.YELLOW),
            CoachingStage.GENERATING: ("AI GENERATING…", theme.YELLOW),
            CoachingStage.FAILED: ("AI FAILED", theme.RED),
            CoachingStage.SETUP_NEEDED: ("AI SETUP NEEDED", theme.YELLOW),
            CoachingStage.UNAVAILABLE: ("AI UNAVAILABLE", theme.RED),
        }
        if progress.stage is CoachingStage.READY:
            count = progress.findings or 0
            return f"AI READY · {count} tips", theme.GREEN, progress.message
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
        if self._session is None:
            self._new_session()
            if self._session is None:
                return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import telemetry", "", "Telemetry CSV (*.csv)"
        )
        for path in paths:
            self._import_one(Path(path))
        if paths:
            self.refresh_sessions()

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
