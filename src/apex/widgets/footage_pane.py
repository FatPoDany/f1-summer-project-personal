"""The footage half of the review window: the clip of the corner being discussed.

Everything here degrades rather than fails. A session that was not recorded, a
build without ffmpeg, a Qt without multimedia support -- each ends with a
sentence saying which, next to a track replay that still works. The footage is
what makes the coaching vivid; it is not what makes it true.

Cutting a clip is ffmpeg work, so it happens off the GUI thread. A participant
clicking between corners must not be waiting on an encoder.
"""

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import QLabel, QStackedWidget, QVBoxLayout, QWidget

from apex import theme
from f1coach_core.footage import Window
from racecoach.telemetry.screen_capture import Recording, RecordingError, cut_clip


def video_support() -> tuple[type, type] | None:
    """Qt's multimedia classes, or None when this build has none.

    Imported here rather than at module load: multimedia ships in PySide6-Addons
    and a development install may only have Essentials. A missing player must
    cost the footage, not the review window.
    """
    try:
        from PySide6.QtMultimedia import QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
    except ImportError:
        return None
    return QMediaPlayer, QVideoWidget


class _ClipSignals(QObject):
    ready = Signal(object, str)
    failed = Signal(object, str)


class _ClipTask(QRunnable):
    """One ffmpeg cut, off the GUI thread."""

    def __init__(self, recording: Recording, window: Window, destination: Path) -> None:
        super().__init__()
        self.signals = _ClipSignals()
        self.token = object()
        self._recording = recording
        self._window = window
        self._destination = destination

    def run(self) -> None:  # pragma: no cover - exercised through the pane
        try:
            path = cut_clip(
                self._recording,
                self._window.from_wall_clock,
                self._window.to_wall_clock,
                self._destination,
            )
        except (RecordingError, OSError) as exc:
            try:
                self.signals.failed.emit(self.token, str(exc))
            except RuntimeError:
                pass  # the window closed while ffmpeg was working
            return
        try:
            self.signals.ready.emit(self.token, str(path))
        except RuntimeError:
            pass


class FootagePane(QWidget):
    """Plays the clip of one stretch, or says plainly why it cannot."""

    def __init__(self, parent: QWidget | None = None, *, pool: QThreadPool | None = None):
        super().__init__(parent)
        self._pool = pool or QThreadPool.globalInstance()
        self._token: object | None = None
        self._clips: dict[int, str] = {}  # cut once per stretch, then reused

        self._message = QLabel()
        self._message.setWordWrap(True)
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setStyleSheet(f"color: {theme.TEXT_DIM};")

        self._stack = QStackedWidget()
        self._stack.addWidget(self._message)

        self._player = None
        self._video = None
        support = video_support()
        if support is not None:
            player_class, video_class = support
            self._video = video_class()
            self._player = player_class(self)
            self._player.setVideoOutput(self._video)
            self._stack.addWidget(self._video)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)
        self.setMinimumWidth(320)
        self.setStyleSheet(f"border: 1px solid {theme.NEUTRAL};")
        self._say("Open a stretch to see its footage.")

    # -- state -------------------------------------------------------------

    def clear(self) -> None:
        self._token = None
        self._stop()
        self._say("Open a stretch to see its footage.")

    def show_stretch(
        self, index: int, recording: Recording | None, window: Window | None, clips_dir: Path
    ) -> None:
        """Show the footage for one stretch, cutting it if this is the first time."""
        self._token = token = object()
        self._stop()

        if self._player is None:
            self._say(
                "This build of Apex cannot play video, so the footage is not shown. "
                "The lap replay beside it is unaffected."
            )
            return
        if recording is None:
            self._say(
                "This session was not recorded, so there is no footage of it. "
                "Recording starts with the session; sessions driven before it was "
                "available have none."
            )
            return
        if window is None:
            self._say(
                "This lap has no wall-clock channel, so its footage cannot be "
                "placed accurately. Showing the wrong corner would be worse than "
                "showing none."
            )
            return

        existing = self._clips.get(index)
        if existing and Path(existing).is_file():
            self._play(existing)
            return

        self._say("Preparing the footage of this stretch…")
        task = _ClipTask(recording, window, clips_dir / f"stretch-{index:02d}.mp4")
        task.signals.ready.connect(
            lambda path, t=token, i=index: self._clip_ready(t, i, path)
        )
        task.signals.failed.connect(lambda message, t=token: self._clip_failed(t, message))
        self._pool.start(task)

    # -- outcomes ----------------------------------------------------------

    def _clip_ready(self, token: object, index: int, path: str) -> None:
        self._clips[index] = path
        if token is not self._token:
            return  # the participant moved on; the clip is kept for next time
        self._play(path)

    def _clip_failed(self, token: object, message: str) -> None:
        if token is not self._token:
            return
        self._say(f"The footage of this stretch could not be prepared. {message}")

    def _play(self, path: str) -> None:
        from PySide6.QtCore import QUrl

        self._stack.setCurrentWidget(self._video)
        self._player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
        self._player.setLoops(-1)  # a few seconds of corner, watched over and over
        self._player.play()

    def _stop(self) -> None:
        if self._player is not None:
            self._player.stop()

    def _say(self, text: str) -> None:
        self._message.setText(text)
        self._stack.setCurrentWidget(self._message)
