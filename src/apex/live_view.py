"""Live Pit Wall: visible TORCS bridge telemetry and Granite 4.1 advice.

The UDP capture and model calls stay off the Qt GUI thread.  This view only
receives queued signals containing bounded telemetry snapshots and validated
advice objects; it never owns or mutates vehicle controls.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from apex import theme
from racecoach.cli import load_race_config
from racecoach.control.simple_driver import SimpleDriver
from racecoach.granite import GraniteClient, LiveGraniteAdvisor, MockGraniteClient
from racecoach.granite.client import GraniteResult, TelemetrySnapshot
from racecoach.telemetry.live import CaptureCancelled, LiveConfig, capture_run

WHEELS = ("FR", "FL", "RR", "RL")  # TORCS/SCR protocol order; do not reorder
UI_SAMPLE_TICKS = 5  # 10 Hz at the bridge's normal 50 Hz callback rate
TICK_S = 0.02
STATUS_COLOURS = {
    "idle": theme.TEXT_DIM,
    "waiting": theme.YELLOW,
    "online": theme.GREEN,
    "error": theme.RED,
}

CaptureFunction = Callable[..., Path]
ConfigLoader = Callable[[Path], tuple[LiveConfig, SimpleDriver]]
BackendFactory = Callable[[str], object]


def _default_backend(mode: str):
    return MockGraniteClient() if mode == "mock" else GraniteClient()


class _LiveSignals(QObject):
    bridgeStatus = Signal(object, str, str)  # task token, text, tone
    modelStatus = Signal(object, str, str)
    telemetryReady = Signal(object)  # task token; buffer itself is latest-only
    advice = Signal(object, object, object, float)  # token, source, result, latency
    completed = Signal(object, str)
    stopped = Signal(object, str)  # token, saved path (or empty before telemetry)
    failed = Signal(object, str)

    def __init__(self, token: object) -> None:
        super().__init__()
        self.token = token
        self._telemetry = _LatestTelemetry()

    def publish_telemetry(self, snapshot: TelemetrySnapshot) -> None:
        if self._telemetry.put(snapshot):
            self.telemetryReady.emit(self.token)

    def take_telemetry(self) -> TelemetrySnapshot | None:
        return self._telemetry.take()


class _LatestTelemetry:
    """One pending GUI wake-up and one replaceable snapshot, never a tick backlog."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: TelemetrySnapshot | None = None
        self._pending = False

    def put(self, snapshot: TelemetrySnapshot) -> bool:
        """Store newest data; return true only when a GUI wake-up is needed."""
        with self._lock:
            self._latest = snapshot
            if self._pending:
                return False
            self._pending = True
            return True

    def take(self) -> TelemetrySnapshot | None:
        with self._lock:
            snapshot = self._latest
            self._latest = None
            self._pending = False
            return snapshot


class _ReportingBackend:
    """Time model calls and expose failures without bypassing advisor auditing."""

    def __init__(
        self,
        backend,
        signals: _LiveSignals,
        *,
        test_mode: bool,
        is_active: Callable[[], bool],
    ) -> None:
        self._backend = backend
        self._signals = signals
        self._test_mode = test_mode
        self._is_active = is_active
        self._latencies: dict[int, float] = {}
        self._has_succeeded = False

    def generate(self, snapshot: TelemetrySnapshot) -> GraniteResult:
        if not self._has_succeeded and self._is_active():
            prefix = "TEST mock" if self._test_mode else "Granite 4.1"
            self._signals.modelStatus.emit(
                self._signals.token,
                f"Querying {prefix} — not Online until a validated response arrives",
                "waiting",
            )
        started = monotonic()
        try:
            result = self._backend.generate(snapshot)
        except Exception as exc:
            if self._is_active():
                self._signals.modelStatus.emit(
                    self._signals.token,
                    f"Model error — {type(exc).__name__}: {exc}",
                    "error",
                )
            raise
        # The advisor runs one inference at a time. Keep only the newest
        # latency so a deliberately suppressed stale result cannot accumulate
        # bookkeeping during a long race.
        self._latencies.clear()
        self._latencies[snapshot.tick] = monotonic() - started
        self._has_succeeded = True
        return result

    def take_latency(self, tick: int) -> float:
        return self._latencies.pop(tick, 0.0)


class _DashboardAdvisor:
    """Constant-bounded UI sampling in front of the asynchronous advisor."""

    def __init__(
        self,
        inner: LiveGraniteAdvisor,
        signals: _LiveSignals,
        is_active: Callable[[], bool],
    ) -> None:
        self._inner = inner
        self._signals = signals
        self._is_active = is_active
        self._streaming_reported = False

    @property
    def hud_advice(self):
        """Preserve the optional in-game HUD payload supplied by the advisor."""
        return getattr(self._inner, "hud_advice", None)

    def start(self, run_dir: Path) -> None:
        self._inner.start(run_dir)
        if self._is_active():
            self._signals.bridgeStatus.emit(
                self._signals.token,
                "Connected — waiting for first telemetry packet",
                "waiting",
            )

    def observe(self, tick: int, state: dict, actions: dict, lap: int) -> None:
        # capture_run invokes this only after sending the time-critical action.
        # Both operations are bounded and guarded so UI work cannot break driving.
        if self._is_active() and not self._streaming_reported:
            self._streaming_reported = True
            self._signals.bridgeStatus.emit(
                self._signals.token,
                "Online — Granite Bridge is streaming",
                "online",
            )
        if self._is_active() and tick % UI_SAMPLE_TICKS == 0:
            try:
                self._signals.publish_telemetry(
                    TelemetrySnapshot.from_scr(tick, lap, state, actions, tick_s=TICK_S)
                )
            except Exception:
                pass
        try:
            self._inner.observe(tick, state, actions, lap)
        except Exception:
            pass

    def close(self) -> None:
        self._inner.close()


class _LiveCaptureTask(QRunnable):
    """One cancellable capture session owned by the global Qt thread pool."""

    def __init__(
        self,
        *,
        mode: str,
        interval_s: float,
        config_path: Path,
        capture_fn: CaptureFunction,
        config_loader: ConfigLoader,
        backend_factory: BackendFactory,
    ) -> None:
        super().__init__()
        self._mode = mode
        self._interval_s = interval_s
        self._config_path = config_path
        self._capture_fn = capture_fn
        self._config_loader = config_loader
        self._backend_factory = backend_factory
        self._token = object()
        self._stop = threading.Event()
        self._active = threading.Event()
        self._active.set()
        self._done = threading.Event()
        self.signals = _LiveSignals(self._token)

    @property
    def token(self) -> object:
        return self._token

    def request_stop(self) -> None:
        self._stop.set()
        self._active.clear()

    def wait(self, timeout_s: float) -> bool:
        return self._done.wait(timeout_s)

    def run(self) -> None:
        try:
            config, driver = self._config_loader(self._config_path)
            # Short receives keep Stop bounded without changing the simulator's
            # idle timeout or its 50 Hz control cadence.
            config = replace(config, timeout_s=min(config.timeout_s, 0.25))
            if self._active.is_set():
                self.signals.bridgeStatus.emit(
                    self._token, f"Connecting to {config.host}:{config.port}…", "waiting"
                )
            test_mode = self._mode == "mock"
            waiting = (
                "Waiting for first TEST response — not Online yet"
                if test_mode
                else "Waiting for first validated Granite 4.1 response — not Online yet"
            )
            if self._active.is_set():
                self.signals.modelStatus.emit(self._token, waiting, "waiting")

            backend = _ReportingBackend(
                self._backend_factory(self._mode),
                self.signals,
                test_mode=test_mode,
                is_active=self._active.is_set,
            )

            def report_advice(snapshot: TelemetrySnapshot, result: GraniteResult) -> None:
                if self._active.is_set():
                    self.signals.advice.emit(
                        self._token,
                        snapshot,
                        result,
                        backend.take_latency(snapshot.tick),
                    )

            granite = LiveGraniteAdvisor(
                backend,
                interval_s=self._interval_s,
                on_advice=report_advice,
                shutdown_timeout_s=1.0,
            )
            advisor = _DashboardAdvisor(granite, self.signals, self._active.is_set)
            run_dir = self._capture_fn(
                config,
                driver,
                quiet=True,
                advisor=advisor,
                stop_requested=self._stop.is_set,
            )
        except CaptureCancelled:
            self.signals.stopped.emit(self._token, "")
        except Exception as exc:
            if self._stop.is_set():
                self.signals.stopped.emit(self._token, "")
            else:
                self.signals.failed.emit(self._token, f"{type(exc).__name__}: {exc}")
        else:
            signal = self.signals.stopped if self._stop.is_set() else self.signals.completed
            signal.emit(self._token, str(run_dir))
        finally:
            self._active.clear()
            self._done.set()


def _value_label() -> QLabel:
    label = QLabel("—")
    label.setStyleSheet("font-size: 26px; font-weight: 600;")
    return label


class LivePitWallView(QWidget):
    """A deliberately manual live session: opening the page never connects."""

    sessionFinished = Signal(str)
    sessionFailed = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        capture_fn: CaptureFunction = capture_run,
        config_loader: ConfigLoader = load_race_config,
        backend_factory: BackendFactory = _default_backend,
        config_path: str | Path = "configs/race.toml",
    ) -> None:
        super().__init__(parent)
        self._capture_fn = capture_fn
        self._config_loader = config_loader
        self._backend_factory = backend_factory
        self._config_path = Path(config_path)
        self._task: _LiveCaptureTask | None = None
        self._active_mode = "granite"
        self._coach_interval_s = 10.0
        self._latest_snapshot: TelemetrySnapshot | None = None
        self._advice_snapshot: TelemetrySnapshot | None = None
        self._advice_latency_s: float | None = None
        self._last_model = ""
        self._build_ui()
        self._model_changed()

    @property
    def running(self) -> bool:
        return self._task is not None

    def _build_ui(self) -> None:
        title = QLabel("Live Pit Wall")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        subtitle = QLabel(
            "Granite gives evidence-grounded advice; deterministic code keeps control of the car."
        )
        subtitle.setStyleSheet(f"color: {theme.TEXT_DIM};")

        self._model_combo = QComboBox()
        self._model_combo.addItem("Granite 4.1 (local)", "granite")
        self._model_combo.addItem("Mock contract — TEST", "mock")
        self._model_combo.currentIndexChanged.connect(self._model_changed)
        self._interval = QDoubleSpinBox()
        self._interval.setRange(1.0, 60.0)
        self._interval.setDecimals(1)
        self._interval.setValue(10.0)
        self._interval.setSuffix(" s")
        self._interval.setToolTip("Simulation time between Granite snapshots")
        self._start_button = QPushButton("Start live session")
        self._start_button.clicked.connect(self.start_capture)
        self._stop_button = QPushButton("Stop safely")
        self._stop_button.setEnabled(False)
        self._stop_button.clicked.connect(self.stop_capture)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Advisor:"))
        controls.addWidget(self._model_combo)
        controls.addWidget(QLabel("Interval:"))
        controls.addWidget(self._interval)
        controls.addStretch(1)
        controls.addWidget(self._start_button)
        controls.addWidget(self._stop_button)

        self._bridge_status = QLabel()
        self._bridge_status.setWordWrap(True)
        self._model_status = QLabel()
        self._model_status.setWordWrap(True)
        status_group = QGroupBox("Connections")
        status_layout = QGridLayout(status_group)
        status_layout.addWidget(QLabel("Bridge"), 0, 0)
        status_layout.addWidget(self._bridge_status, 0, 1)
        status_layout.addWidget(QLabel("Model"), 1, 0)
        status_layout.addWidget(self._model_status, 1, 1)
        status_layout.setColumnStretch(1, 1)
        self._set_status(self._bridge_status, "Offline — press Start to connect", "idle")

        self._lap_value = _value_label()
        self._time_value = _value_label()
        self._speed_value = _value_label()
        telemetry_group = QGroupBox("Live telemetry")
        telemetry_layout = QGridLayout(telemetry_group)
        for column, (caption, value) in enumerate(
            (("Lap", self._lap_value), ("Simulation time", self._time_value),
             ("Speed", self._speed_value))
        ):
            label = QLabel(caption)
            label.setStyleSheet(f"color: {theme.TEXT_DIM};")
            telemetry_layout.addWidget(label, 0, column)
            telemetry_layout.addWidget(value, 1, column)
        telemetry_layout.setColumnStretch(0, 1)
        telemetry_layout.setColumnStretch(1, 1)
        telemetry_layout.setColumnStretch(2, 1)

        self._tires = QTableWidget(4, 5)
        self._tires.setHorizontalHeaderLabels(
            ("Wheel", "Wear", "Temperature", "Pressure", "Graining")
        )
        self._tires.verticalHeader().hide()
        self._tires.verticalHeader().setDefaultSectionSize(22)
        self._tires.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tires.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._tires.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._tires.horizontalHeader().setFixedHeight(22)
        self._tires.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Four tyres are simultaneously visible at the 960×540 minimum window;
        # operators should never need to scroll to discover the rear-left state.
        self._tires.setFixedHeight(114)
        for row, wheel in enumerate(WHEELS):
            self._tires.setItem(row, 0, QTableWidgetItem(wheel))
            for column in range(1, 5):
                self._tires.setItem(row, column, QTableWidgetItem("—"))
        tires_group = QGroupBox("Tyres · protocol order FR / FL / RR / RL")
        tires_layout = QVBoxLayout(tires_group)
        tires_layout.addWidget(self._tires)

        self._advice_chip = QLabel("NO ADVICE YET")
        self._advice_chip.setStyleSheet(
            f"background: {theme.NEUTRAL}; color: {theme.TEXT_DIM};"
            " border-radius: 4px; padding: 3px 8px; font-weight: 600;"
        )
        self._advice_message = QLabel(
            "Start a session and race with Granite Bridge to receive validated advice."
        )
        self._advice_message.setWordWrap(True)
        self._advice_message.setStyleSheet("font-size: 16px;")
        self._advice_source = QLabel("Source — · latency —")
        self._advice_source.setWordWrap(True)
        self._advice_source.setStyleSheet(f"color: {theme.TEXT_DIM};")
        self._evidence = QPlainTextEdit()
        self._evidence.setReadOnly(True)
        self._evidence.setMaximumHeight(85)
        self._evidence.setPlaceholderText("Validated telemetry evidence appears here.")
        advice_group = QGroupBox("Latest Granite advice")
        advice_layout = QVBoxLayout(advice_group)
        advice_layout.addWidget(self._advice_chip, alignment=self._advice_chip.alignment())
        advice_layout.addWidget(self._advice_message)
        advice_layout.addWidget(self._advice_source)
        advice_layout.addWidget(self._evidence)

        self._run_path = QLabel("No live run started.")
        self._run_path.setWordWrap(True)
        self._run_path.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 11px;")

        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 10, 12, 8)
        layout.setSpacing(6)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(controls)
        layout.addWidget(status_group)
        layout.addWidget(telemetry_group)
        layout.addWidget(tires_group)
        layout.addWidget(advice_group, stretch=1)
        layout.addWidget(self._run_path)

        scroll = QScrollArea(self)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _model_changed(self) -> None:
        mode = str(self._model_combo.currentData())
        if mode == "mock":
            self._set_status(
                self._model_status,
                "TEST backend selected — no model request has been sent",
                "waiting",
            )
        else:
            self._set_status(
                self._model_status,
                "Offline — Granite 4.1 has not been queried",
                "idle",
            )

    def start_capture(self) -> None:
        if self._task is not None:
            return
        self._active_mode = str(self._model_combo.currentData())
        self._coach_interval_s = self._interval.value()
        self._latest_snapshot = None
        self._advice_snapshot = None
        self._advice_latency_s = None
        self._last_model = ""
        self._reset_readouts()
        self._set_status(self._bridge_status, "Preparing connection…", "waiting")
        self._set_status(
            self._model_status,
            "Waiting — no validated model response yet",
            "waiting",
        )
        self._run_path.setText("Capture starting…")
        self._set_controls_running(True)

        task = _LiveCaptureTask(
            mode=self._active_mode,
            interval_s=self._coach_interval_s,
            config_path=self._config_path,
            capture_fn=self._capture_fn,
            config_loader=self._config_loader,
            backend_factory=self._backend_factory,
        )
        task.signals.bridgeStatus.connect(self._on_bridge_status)
        task.signals.modelStatus.connect(self._on_model_status)
        task.signals.telemetryReady.connect(self._take_telemetry)
        task.signals.advice.connect(self._on_advice)
        task.signals.completed.connect(self._on_completed)
        task.signals.stopped.connect(self._on_stopped)
        task.signals.failed.connect(self._on_failed)
        self._task = task
        QThreadPool.globalInstance().start(task)

    def stop_capture(self) -> None:
        if self._task is None:
            return
        self._set_status(self._bridge_status, "Stopping safely — sending full brake…", "waiting")
        self._stop_button.setEnabled(False)
        self._task.request_stop()

    def shutdown(self, timeout_s: float = 2.0) -> bool:
        """Request stop and wait for a bounded period during window teardown."""
        task = self._task
        if task is None:
            return True
        task.request_stop()
        return task.wait(timeout_s)

    def _is_current_task(self, token: object) -> bool:
        return self._task is not None and token is self._task.token

    def _on_bridge_status(self, token: object, text: str, tone: str) -> None:
        if not self._is_current_task(token):
            return
        self._set_status(self._bridge_status, text, tone)

    def _on_model_status(self, token: object, text: str, tone: str) -> None:
        if not self._is_current_task(token):
            return
        self._set_status(self._model_status, text, tone)

    def _take_telemetry(self, token: object) -> None:
        task = self._task
        if task is None or token is not task.token:
            return
        snapshot = task.signals.take_telemetry()
        if snapshot is not None:
            self._render_telemetry(snapshot)

    def _render_telemetry(self, snapshot: TelemetrySnapshot) -> None:
        self._latest_snapshot = snapshot
        self._lap_value.setText(str(snapshot.lap))
        self._time_value.setText(f"{snapshot.sim_time_s:.1f} s")
        self._speed_value.setText(f"{snapshot.speed_kmh:.1f} km/h")
        self._render_tire_column(1, snapshot.tire_wear, lambda v: f"{v * 100:.1f}%")
        self._render_tire_column(2, snapshot.tire_temp_c, lambda v: f"{v:.1f} °C")
        self._render_tire_column(3, snapshot.tire_pressure_kpa, lambda v: f"{v:.1f} kPa")
        self._render_tire_column(4, snapshot.tire_graining, lambda v: f"{v * 100:.1f}%")
        self._render_advice_source()

    def _on_advice(
        self,
        token: object,
        snapshot: TelemetrySnapshot,
        result: GraniteResult,
        latency_s: float,
    ) -> None:
        if not self._is_current_task(token):
            return
        self._render_advice(snapshot, result, latency_s)

    def _render_advice(
        self, snapshot: TelemetrySnapshot, result: GraniteResult, latency_s: float
    ) -> None:
        self._advice_snapshot = snapshot
        self._advice_latency_s = latency_s
        self._last_model = result.model
        prefix = "Online — TEST · " if self._active_mode == "mock" else "Online — "
        self._set_status(self._model_status, prefix + result.model, "online")
        advice = result.advice
        test = "TEST · " if self._active_mode == "mock" else ""
        self._advice_chip.setText(f"{test}{advice.urgency.upper()} · {advice.focus}")
        self._advice_chip.setStyleSheet(
            f"background: {theme.NEUTRAL}; color: {theme.GREEN};"
            " border-radius: 4px; padding: 3px 8px; font-weight: 600;"
        )
        self._advice_message.setText(advice.message)
        self._evidence.setPlainText(
            "\n".join(
                f"{item.metric} = {item.value:g} {item.unit}" for item in advice.evidence
            )
        )
        self._render_advice_source()

    def _render_advice_source(self) -> None:
        source = self._advice_snapshot
        if source is None or self._advice_latency_s is None:
            self._advice_source.setText("Source — · latency —")
            self._advice_source.setStyleSheet(f"color: {theme.TEXT_DIM};")
            return
        age_s = 0.0
        if self._latest_snapshot is not None:
            delta = self._latest_snapshot.sim_time_s - source.sim_time_s
            age_s = (
                delta
                if delta >= 0
                else max(0.0, (self._latest_snapshot.tick - source.tick) * TICK_S)
            )
        stale = age_s > 2.0 * self._coach_interval_s
        prefix = "STALE · " if stale else ""
        age = f" · age {age_s:.1f} s" if age_s > 0.05 else ""
        self._advice_source.setText(
            f"{prefix}Source lap {source.lap} · sim {source.sim_time_s:.1f} s"
            f" · latency {self._advice_latency_s:.2f} s{age}"
        )
        colour = theme.YELLOW if stale else theme.TEXT_DIM
        self._advice_source.setStyleSheet(f"color: {colour};")

    def _render_tire_column(
        self,
        column: int,
        readings: tuple[float, float, float, float] | None,
        formatter: Callable[[float], str],
    ) -> None:
        for row in range(4):
            self._tires.item(row, column).setText(
                "—" if readings is None else formatter(readings[row])
            )

    def _on_completed(self, token: object, path: str) -> None:
        if not self._is_current_task(token):
            return
        self._finish_session()
        self._set_status(self._bridge_status, "Offline — race session completed", "idle")
        self._mark_model_idle()
        self._run_path.setText(f"Saved run: {path}")
        self.sessionFinished.emit(path)

    def _on_stopped(self, token: object, path: str) -> None:
        if not self._is_current_task(token):
            return
        self._finish_session()
        detail = "run saved" if path else "no telemetry was captured"
        self._set_status(self._bridge_status, f"Offline — stopped safely; {detail}", "idle")
        self._mark_model_idle()
        self._run_path.setText(f"Saved run: {path}" if path else "Stopped before a run was saved.")
        self.sessionFinished.emit(path)

    def _on_failed(self, token: object, message: str) -> None:
        if not self._is_current_task(token):
            return
        self._finish_session()
        self._set_status(self._bridge_status, f"Connection error — {message}", "error")
        if not self._last_model:
            self._set_status(
                self._model_status,
                "Offline — no validated model response received",
                "idle",
            )
        self._run_path.setText("Live session failed; TORCS remains safe.")
        self.sessionFailed.emit(message)

    def _finish_session(self) -> None:
        self._task = None
        self._set_controls_running(False)

    def _mark_model_idle(self) -> None:
        if self._last_model:
            prefix = "Last TEST response OK" if self._active_mode == "mock" else "Last response OK"
            self._set_status(
                self._model_status, f"{prefix} — {self._last_model}; session ended", "idle"
            )
        else:
            self._set_status(
                self._model_status, "Offline — no validated response received", "idle"
            )

    def _set_controls_running(self, running: bool) -> None:
        self._start_button.setEnabled(not running)
        self._stop_button.setEnabled(running)
        self._model_combo.setEnabled(not running)
        self._interval.setEnabled(not running)

    def _reset_readouts(self) -> None:
        for value in (self._lap_value, self._time_value, self._speed_value):
            value.setText("—")
        for row in range(4):
            for column in range(1, 5):
                self._tires.item(row, column).setText("—")
        self._advice_chip.setText("NO ADVICE YET")
        self._advice_message.setText("Waiting for a validated advisory response…")
        self._advice_source.setText("Source — · latency —")
        self._evidence.clear()

    @staticmethod
    def _set_status(label: QLabel, text: str, tone: str) -> None:
        label.setText(text)
        label.setStyleSheet(f"color: {STATUS_COLOURS.get(tone, theme.TEXT_DIM)};")
