"""Garage screen: session library, lap table with deltas/status, import and
watched folder. Double-click a lap to open it in Lap Analysis."""

from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from apex import theme
from f1coach_core import (
    Session,
    TelemetrySchemaError,
    create_session,
    import_telemetry,
    list_sessions,
    load_session,
)

TABLE_HEADERS = ("Lap", "Time", "Δ best", "Status")


class GarageView(QWidget):
    lapOpened = Signal(object, object)  # (Lap, Session)
    status = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session: Session | None = None
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._watched_dir_changed)
        self._watched_seen: set[str] = set()

        self._session_list = QListWidget()
        self._session_list.currentRowChanged.connect(lambda _row: self._load_selected())
        new_button = QPushButton("New Session…")
        new_button.clicked.connect(self._new_session)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Sessions"))
        left_layout.addWidget(self._session_list, stretch=1)
        left_layout.addWidget(new_button)

        import_button = QPushButton("Import Telemetry…")
        import_button.clicked.connect(self._import_files)
        self._watch_button = QPushButton("Watch Folder…")
        self._watch_button.setCheckable(True)
        self._watch_button.toggled.connect(self._toggle_watch)

        self._table = QTableWidget(0, len(TABLE_HEADERS))
        self._table.setHorizontalHeaderLabels(TABLE_HEADERS)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.cellDoubleClicked.connect(self._open_row)

        buttons = QHBoxLayout()
        buttons.addWidget(import_button)
        buttons.addWidget(self._watch_button)
        buttons.addStretch(1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addLayout(buttons)
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

    def _populate_table(self) -> None:
        session = self._session
        self._table.setRowCount(0)
        if session is None:
            return
        best = session.best_lap
        rows = len(session.laps) + len(session.problems)
        self._table.setRowCount(rows)
        for row, lap in enumerate(session.laps):
            delta = session.delta_to_best(lap)
            is_best = lap is best
            cells = (
                lap.source.stem,
                f"{lap.lap_time:.3f} s",
                "—" if is_best else f"+{delta:.3f}",
                "BEST" if is_best else "",
            )
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if is_best:
                    item.setForeground(QColor(theme.PURPLE))
                self._table.setItem(row, col, item)
        for i, (name, message) in enumerate(session.problems):
            row = len(session.laps) + i
            cells = (Path(name).stem, "", "", "unreadable")
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(theme.RED))
                item.setToolTip(message)
                self._table.setItem(row, col, item)

    def _open_row(self, row: int, _col: int = 0) -> None:
        if self._session is not None and 0 <= row < len(self._session.laps):
            self.lapOpened.emit(self._session.laps[row], self._session)

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
            self.status.emit(summary)

    def _toggle_watch(self, checked: bool) -> None:
        if not checked:
            if self._watcher.directories():
                self._watcher.removePaths(self._watcher.directories())
            self._watch_button.setText("Watch Folder…")
            self.status.emit("Stopped watching")
            return
        folder = QFileDialog.getExistingDirectory(self, "Watch folder for new laps")
        if not folder:
            self._watch_button.setChecked(False)
            return
        if self._session is None:
            self._new_session()
        if self._session is None:
            self._watch_button.setChecked(False)
            return
        self._watcher.addPath(folder)
        self._watched_seen = {str(p) for p in Path(folder).glob("*.csv")}
        self._watch_button.setText(f"Watching {Path(folder).name} (stop)")
        self.status.emit(f"Watching {folder} — new CSVs import into '{self._session.name}'")

    def _watched_dir_changed(self, folder: str) -> None:
        current = {str(p) for p in Path(folder).glob("*.csv")}
        fresh = sorted(current - self._watched_seen)
        self._watched_seen = current
        if self._session is None:
            return
        for path in fresh:
            if Path(path).parent == self._session.path:
                continue  # watching the session folder itself: nothing to copy
            self._import_one(Path(path))
        if fresh:
            self.refresh_sessions()
