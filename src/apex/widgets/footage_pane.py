"""The footage half of the review window: the clip of the corner being discussed.

Everything here degrades rather than fails. A session that was not recorded, a
build without ffmpeg, a Qt without multimedia support -- each ends with a
sentence saying which, next to a track replay that still works. The footage is
what makes the coaching vivid; it is not what makes it true.

Cutting a clip is ffmpeg work, so it happens off the GUI thread. A participant
clicking between corners must not be waiting on an encoder.
"""

import hashlib
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import QLabel, QStackedWidget, QVBoxLayout, QWidget

from apex import theme
from f1coach_core.footage import Window
from racecoach.telemetry.screen_capture import (
    Recording,
    RecordingError,
    clip_start_offset,
    cut_clip,
)


def clip_path(clips_dir: Path, index: int, recording: Recording, window: Window) -> Path:
    """Where the clip of one stretch belongs, named after what is inside it.

    The position in the list is not an identity. A session keeps one `clips`
    folder for all of its laps, and corner three of a technique review is not
    corner three of a comparison -- different laps, different spans, different
    seconds of footage, all previously written to `stretch-03.mp4`. Whoever cut
    last decided what everyone else was shown. The wall-clock bounds are what
    actually decide the contents, so they decide the name, and a clip already on
    disk under that name is the right one by construction.
    """
    key = f"{recording.path}|{window.from_wall_clock:.3f}|{window.to_wall_clock:.3f}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    return clips_dir / f"stretch-{index:02d}-{digest}.mp4"


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
    # The payload only. A token travelled here too, and because the pane
    # connects with a token of its own the slots took the emitted one as the
    # path: every clip was cut and then handed to the player as an `object`,
    # which raised inside the slot and left the pane saying "Preparing…" for
    # ever. Returning to the same corner then crashed on Path(object).
    ready = Signal(str)
    failed = Signal(str)


class _ClipTask(QRunnable):
    """One ffmpeg cut, off the GUI thread."""

    def __init__(self, recording: Recording, window: Window, destination: Path) -> None:
        super().__init__()
        self.signals = _ClipSignals()
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
                self.signals.failed.emit(str(exc))
            except RuntimeError:
                pass  # the window closed while ffmpeg was working
            return
        try:
            self.signals.ready.emit(str(path))
        except RuntimeError:
            pass


class FootagePane(QWidget):
    """Plays the clip of one stretch, or says plainly why it cannot."""

    # Cutting takes seconds, and a corner cannot be played back until it is
    # done. The transport beside it hears about that rather than offering a Play
    # button that starts the marker against a picture which is not there yet.
    busyChanged = Signal(bool)

    def __init__(self, parent: QWidget | None = None, *, pool: QThreadPool | None = None):
        super().__init__(parent)
        self._pool = pool or QThreadPool.globalInstance()
        self._token: object | None = None
        # Every task this pane has ever started. The pool owns the C++ runnable,
        # but its signals are a Python object: drop the last reference to one
        # that is still cutting -- which keeping only the newest task did, the
        # moment a participant moved to the next corner -- and the interpreter
        # frees an object ffmpeg's thread is still inside. That is not an
        # exception, it is an access violation, and it takes Apex with it. They
        # are a few hundred bytes each and a session has tens of corners.
        self._tasks: list[_ClipTask] = []
        # What the clip on screen is of, so a moment on track can be turned into
        # a position in it.
        self._recording: Recording | None = None
        self._window: Window | None = None
        # What the transport asked for, which is not always what Qt is doing.
        self._running = False
        self._working = False
        self._rate = 1.0

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
        # Narrow enough that two of these fit side by side in a comparison.
        self.setMinimumWidth(260)
        self.setStyleSheet(f"border: 1px solid {theme.NEUTRAL};")
        self._say("Open a stretch to see its footage.")

    # -- state -------------------------------------------------------------

    @property
    def busy(self) -> bool:
        """Whether a clip is being cut for the stretch now on screen."""
        return self._working

    def clear(self) -> None:
        self._token = None
        self._stop()
        self._busy(False)
        self._say("Open a stretch to see its footage.")

    def show_stretch(
        self, index: int, recording: Recording | None, window: Window | None, clips_dir: Path
    ) -> None:
        """Show the footage for one stretch, cutting it if this is the first time.

        Loaded paused. The replay beside it owns the transport: two players each
        starting on their own is what made the picture and the track marker show
        different moments of the same corner.
        """
        self._token = token = object()
        self._stop()
        self._busy(False)  # whatever was being cut is no longer what is on screen
        self._recording, self._window = recording, window

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

        destination = clip_path(clips_dir, index, recording, window)
        if destination.is_file():
            # Named after its own bounds and moved into place whole, so a file
            # under this name is this stretch, cut in full, whichever run cut it.
            self._play(str(destination))
            return

        self._say("Preparing the footage of this stretch…")
        self._busy(True)
        task = _ClipTask(recording, window, destination)
        # The pool must not delete it out from under the reference kept below.
        task.setAutoDelete(False)
        task.signals.ready.connect(lambda path, t=token: self._clip_ready(t, path))
        task.signals.failed.connect(lambda message, t=token: self._clip_failed(t, message))
        self._tasks.append(task)
        self._pool.start(task)

    # -- outcomes ----------------------------------------------------------

    def _clip_ready(self, token: object, path: str) -> None:
        if token is not self._token:
            return  # the participant moved on; the clip keeps until they return
        try:
            self._busy(False)
            self._play(path)
        except RuntimeError:
            pass  # the review closed while ffmpeg worked; Qt has taken the pane

    def _clip_failed(self, token: object, message: str) -> None:
        if token is not self._token:
            return
        try:
            self._busy(False)
            self._say(f"The footage of this stretch could not be prepared. {message}")
        except RuntimeError:
            pass

    def _busy(self, working: bool) -> None:
        if working != self._working:
            self._working = working
            self.busyChanged.emit(working)

    def _play(self, path: str) -> None:
        """Load the clip and hold it at the start, ready for the transport."""
        from PySide6.QtCore import QUrl

        self._stack.setCurrentWidget(self._video)
        self._player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
        self._player.setPlaybackRate(self._rate)
        self._player.pause()

    # -- transport, driven by the replay beside it -------------------------

    def set_rate(self, rate: float) -> None:
        """How fast this clip must run to keep step with the marker beside it.

        One for the lap being replayed, whose marker advances by the time the
        driver actually took. Not one for a lap being compared with it: that
        driver was somewhere else at every moment, and its picture has to cover
        its own seconds of the corner in the time this one takes.
        """
        self._rate = rate
        if self._player is not None:
            self._player.setPlaybackRate(rate)

    def position_ms_for(self, wall_clock: float) -> int | None:
        """Where in the loaded clip a moment on track is, in milliseconds.

        None when nothing is loaded or the stretch could not be placed, which is
        the same answer as "there is no picture to line up with".
        """
        if self._recording is None or self._window is None:
            return None
        start = clip_start_offset(self._recording, self._window.from_wall_clock)
        return max(0, int((self._recording.offset_of(wall_clock) - start) * 1000))

    def drift_ms(self, wall_clock: float) -> int | None:
        """How far the picture has wandered from the moment it should be showing.

        Positive means the picture is ahead of the moment asked for. None when
        nothing is loaded or the stretch cannot be placed, which is the same
        answer as "there is no drift anyone could correct".

        Both sides are the clip's own media time, and a player's position
        advances at its playback rate, so a pane running at a matched rate is
        measured against where that rate should have carried it by now rather
        than against real time.
        """
        if self._player is None or not self._player.source().isValid():
            return None
        position = self.position_ms_for(wall_clock)
        if position is None:
            return None
        return int(self._player.position() - position)

    def seek_to(self, wall_clock: float) -> None:
        position = self.position_ms_for(wall_clock)
        if self._player is None or position is None:
            return
        self._player.setPosition(position)
        # A clip whose tail was cut short by the end of the recording runs out as
        # the corner does, and Qt stops the player there. Seeking a stopped
        # player leaves the picture frozen for every later round of the replay,
        # so ask again for what the transport is already doing; asking a playing
        # player to play is nothing.
        if self._running:
            self._player.play()

    def resume(self) -> None:
        self._running = True
        if self._player is not None and self._player.source().isValid():
            self._player.play()

    def pause(self) -> None:
        self._running = False
        if self._player is not None:
            self._player.pause()

    def _stop(self) -> None:
        self._running = False
        if self._player is not None:
            self._player.stop()

    def _say(self, text: str) -> None:
        self._message.setText(text)
        self._stack.setCurrentWidget(self._message)
