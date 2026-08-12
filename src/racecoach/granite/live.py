"""Non-blocking Granite advisor attached to the SCR capture loop."""

from __future__ import annotations

import json
import math
import queue
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Protocol

from racecoach.granite.client import GraniteResult, TelemetrySnapshot
from racecoach.telemetry import scr

_STOP = object()


class GraniteBackend(Protocol):
    def generate(self, snapshot: TelemetrySnapshot) -> GraniteResult: ...


AdviceCallback = Callable[[TelemetrySnapshot, GraniteResult], None]


class LiveGraniteAdvisor:
    """Samples SCR ticks and performs inference on a background thread.

    The queue holds only the newest waiting snapshot. If inference is slower
    than the configured cadence, stale advice is dropped instead of slowing
    TORCS or accumulating an unbounded backlog.
    """

    def __init__(
        self,
        backend: GraniteBackend,
        *,
        interval_s: float = 10.0,
        tick_s: float = 0.02,
        on_advice: AdviceCallback | None = None,
        shutdown_timeout_s: float = 65.0,
    ) -> None:
        if not math.isfinite(interval_s) or interval_s <= 0:
            raise ValueError("Granite advice interval must be positive and finite")
        if not math.isfinite(tick_s) or tick_s <= 0:
            raise ValueError("Granite tick interval must be positive and finite")
        if not math.isfinite(shutdown_timeout_s) or shutdown_timeout_s < 0:
            raise ValueError("Granite shutdown timeout must be non-negative and finite")
        self.backend = backend
        self.interval_ticks = max(1, round(interval_s / tick_s))
        self.tick_s = tick_s
        self.on_advice = on_advice
        self.shutdown_timeout_s = shutdown_timeout_s
        self._queue: queue.Queue[TelemetrySnapshot | object] = queue.Queue(maxsize=1)
        self._thread: threading.Thread | None = None
        self._audit_path: Path | None = None
        self._accepting = False
        self._write_lock = threading.Lock()
        self._audit_error: str | None = None
        self._shutdown_timed_out = False
        self._hud_advice: str | None = None
        self._hud_source_tick: int | None = None
        self._latest_observed_tick = 0

    @property
    def audit_path(self) -> Path | None:
        return self._audit_path

    @property
    def audit_error(self) -> str | None:
        return self._audit_error

    @property
    def shutdown_timed_out(self) -> bool:
        return self._shutdown_timed_out

    @property
    def hud_advice(self) -> str | None:
        """Latest fresh validated advice in the bridge's display-only contract.

        The deterministic sender reads this property once per tick.  Expiring
        by simulator tick (rather than wall clock) keeps paused races stable
        while preventing an old instruction from remaining on the TORCS HUD
        when inference falls behind live telemetry.
        """
        source_tick = self._hud_source_tick
        if (
            not self._accepting
            or source_tick is None
            or self._latest_observed_tick - source_tick > 2 * self.interval_ticks
        ):
            return None
        return self._hud_advice

    def start(self, run_dir: Path) -> None:
        if self._thread is not None:
            raise RuntimeError("LiveGraniteAdvisor has already been started")
        coaching_dir = run_dir / "coaching"
        coaching_dir.mkdir(parents=True, exist_ok=True)
        self._audit_path = coaching_dir / "granite-4.1-live.jsonl"
        self._hud_advice = None
        self._hud_source_tick = None
        self._latest_observed_tick = 0
        self._accepting = True
        self._thread = threading.Thread(
            target=self._worker,
            name="granite-torcs-advisor",
            daemon=True,
        )
        self._thread.start()

    def observe(self, tick: int, state: dict, actions: dict, lap: int) -> None:
        """Offer one tick without waiting for inference or filesystem I/O."""
        if not self._accepting:
            return
        self._latest_observed_tick = tick
        if tick % self.interval_ticks:
            return
        snapshot = TelemetrySnapshot.from_scr(
            tick,
            lap,
            state,
            actions,
            tick_s=self.tick_s,
        )
        try:
            self._queue.put_nowait(snapshot)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:  # the worker won the race; try the fresh put below
                pass
            try:
                self._queue.put_nowait(snapshot)
            except queue.Full:
                pass  # inference just took another item; the next cadence will retry

    def close(self) -> None:
        self._accepting = False
        self._hud_advice = None
        self._hud_source_tick = None
        thread = self._thread
        if thread is None:
            return
        try:
            # Give the worker a brief chance to claim the first/last snapshot.
            # If a slow inference already left a stale queued snapshot, drop
            # that one rather than waiting for a second model timeout.
            self._queue.put(_STOP, timeout=min(0.5, self.shutdown_timeout_s))
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(_STOP)
            except queue.Full:
                pass
        thread.join(timeout=self.shutdown_timeout_s)
        self._shutdown_timed_out = thread.is_alive()

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                assert isinstance(item, TelemetrySnapshot)
                started = monotonic()
                try:
                    result = self.backend.generate(item)
                    # A timed-out request may finish after close(). Audit it, but
                    # never revive a stopped session's callback or display state.
                    fresh_for_display = (
                        self._accepting
                        and self._latest_observed_tick - item.tick
                        <= 2 * self.interval_ticks
                    )
                    if fresh_for_display:
                        # Immutable reference assignment keeps this read non-blocking
                        # for the 50 Hz sender. The bridge validates it again.
                        self._hud_advice = scr.sanitize_hud_advice(result.advice.message)
                        self._hud_source_tick = item.tick
                    record = {
                        "recorded_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
                        "ok": True,
                        "latency_s": round(monotonic() - started, 3),
                        # Publication eligibility is auditable, but UDP/TORCS
                        # provides no acknowledgement that a frame was drawn.
                        "published_for_display": fresh_for_display,
                        "display_suppressed_reason": (
                            None
                            if fresh_for_display
                            else ("session-stopped" if not self._accepting else "stale")
                        ),
                        "source_tick": item.tick,
                        "latest_observed_tick": self._latest_observed_tick,
                        "snapshot": item.to_dict(),
                        "model": result.model,
                        "prompt_version": result.prompt_version,
                        "provenance": result.provenance,
                        "prompt": result.prompt,
                        "raw_response": result.raw_text,
                        "advice": result.advice.to_dict(),
                    }
                    self._append(record)
                    if self.on_advice is not None and fresh_for_display:
                        try:
                            self.on_advice(item, result)
                        except Exception as exc:
                            self._append(
                                {
                                    "recorded_at": datetime.now(UTC).isoformat(
                                        timespec="milliseconds"
                                    ),
                                    "ok": False,
                                    "snapshot": item.to_dict(),
                                    "error": f"advice callback failed: {type(exc).__name__}: {exc}",
                                }
                            )
                except Exception as exc:  # one model failure must never stop TORCS
                    self._hud_advice = None
                    self._hud_source_tick = None
                    self._append(
                        {
                            "recorded_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
                            "ok": False,
                            "latency_s": round(monotonic() - started, 3),
                            "snapshot": item.to_dict(),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
            finally:
                self._queue.task_done()

    def _append(self, record: dict) -> None:
        if self._audit_path is None:
            return
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        try:
            with self._write_lock, self._audit_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            self._audit_error = f"{type(exc).__name__}: {exc}"
