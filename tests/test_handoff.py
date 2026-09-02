"""Closing the loop after a race: read it in, save it, send it, open the review.

Every test here drives the orchestration with stubs. What the individual steps
do is tested where they live; what is tested here is the thing that had no
owner before -- the order they run in, and what still happens when one of them
does not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from racecoach.telemetry.handoff import finish_session
from racecoach.telemetry.ibmf1_upload import IbmF1UploadError


@dataclass
class FakeRegistered:
    session: str = "P001-coached-20260902-101500"
    laps: int = 3
    skipped: tuple[str, ...] = ()


@dataclass
class FakePackaged:
    path: Path
    files: int = 7


@dataclass
class FakeBundle:
    path: Path
    rows: int = 18833
    bytes: int = 6_200_000
    paused_s: float = 0.0


@dataclass
class FakeDelivered:
    review_url: str = "https://demo.lzqqq.org/"
    message: str = "Custom session stored - 40 checkpoints"


@dataclass
class Stubs:
    """Everything `finish_session` reaches, replaced, and what it was asked."""

    opened: list[str] = field(default_factory=list)
    exported: list[dict] = field(default_factory=list)
    delivered: list[Path] = field(default_factory=list)
    registered: FakeRegistered = field(default_factory=FakeRegistered)
    has_key: bool = True
    deliver_error: str | None = None
    package_error: str | None = None
    register_error: str | None = None

    def as_kwargs(self) -> dict:
        return {
            "register_fn": self.register,
            "package_fn": self.package,
            "export_fn": self.export,
            "deliver_fn": self.deliver,
            "endpoint_fn": self.endpoint,
            "open_url": self.open_url,
        }

    def register(self, _capture_dir):
        if self.register_error:
            raise RuntimeError(self.register_error)
        return self.registered

    def package(self, _capture_dir, destination):
        if self.package_error:
            raise RuntimeError(self.package_error)
        destination = Path(destination)
        destination.write_bytes(b"zip")
        return FakePackaged(path=destination)

    def export(self, _capture_dir, destination, **kwargs):
        self.exported.append(kwargs)
        destination = Path(destination)
        destination.write_bytes(b"bundle")
        return FakeBundle(path=destination)

    def deliver(self, archive):
        self.delivered.append(Path(archive))
        if self.deliver_error:
            raise IbmF1UploadError(self.deliver_error)
        return FakeDelivered()

    def endpoint(self):
        if not self.has_key:
            raise IbmF1UploadError("No Coach upload key.")
        return object()

    def open_url(self, url: str) -> bool:
        self.opened.append(url)
        return True


def make_capture(tmp_path: Path, *, phase: str = "coached", participant: str = "P001") -> Path:
    directory = tmp_path / f"{participant}-{phase}-20260902-101500"
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(
        json.dumps({"participant_id": participant, "phase": phase}), encoding="utf-8"
    )
    return directory


@pytest.fixture
def out_dir(tmp_path) -> Path:
    return tmp_path / "desktop"


def test_a_finished_race_is_read_in_saved_sent_and_opened(tmp_path, out_dir):
    capture = make_capture(tmp_path)
    stubs = Stubs()

    handoff = finish_session(capture, archive_dir=out_dir, **stubs.as_kwargs())

    assert handoff.laps == 3
    assert handoff.archive == out_dir / f"{capture.name}.zip"
    assert handoff.archive.is_file()
    assert handoff.bundle is not None
    assert handoff.delivered
    assert handoff.review_url == "https://demo.lzqqq.org/"
    assert stubs.opened == ["https://demo.lzqqq.org/"]
    assert handoff.opened


def test_the_file_to_send_is_named_after_the_capture(tmp_path, out_dir):
    """A participant is asked for one file by name; it has to be findable."""
    capture = make_capture(tmp_path, participant="B0826", phase="baseline")

    handoff = finish_session(capture, archive_dir=out_dir, **Stubs().as_kwargs())

    assert handoff.archive.name == "B0826-baseline-20260902-101500.zip"


def test_a_machine_that_was_never_set_up_to_send_still_saves_the_file(tmp_path, out_dir):
    """No key is not a failure. It is a machine nobody configured to send.

    Which is the right default: uploading puts human-subject data on a
    third-party host, so it should take a deliberate act, and the file the
    participant hands over must not depend on that act having happened.
    """
    capture = make_capture(tmp_path)
    stubs = Stubs(has_key=False)

    handoff = finish_session(capture, archive_dir=out_dir, **stubs.as_kwargs())

    assert handoff.archive.is_file()
    assert handoff.bundle is None
    assert not handoff.delivered
    assert stubs.delivered == []
    assert any("not sent" in line for line in handoff.lines)


def test_a_failed_upload_does_not_cost_the_file_that_has_to_be_sent(tmp_path, out_dir):
    """The upload is the part that can be redone; the file is not."""
    capture = make_capture(tmp_path)
    stubs = Stubs(deliver_error="The Coach server stayed busy")

    handoff = finish_session(capture, archive_dir=out_dir, **stubs.as_kwargs())

    assert handoff.archive.is_file()
    assert handoff.bundle is not None  # kept, so upload-ibmf1 can send it again
    assert not handoff.delivered
    assert handoff.opened is False
    assert any("stayed busy" in line for line in handoff.lines)


def test_a_failed_save_does_not_stop_the_upload(tmp_path, out_dir):
    capture = make_capture(tmp_path)
    stubs = Stubs(package_error="the disk is full")

    handoff = finish_session(capture, archive_dir=out_dir, **stubs.as_kwargs())

    assert handoff.archive is None
    assert handoff.delivered
    assert any("the disk is full" in line for line in handoff.lines)


def test_nothing_here_raises_into_the_window(tmp_path, out_dir):
    """The race is already on disk. A failure now is news, not an exception."""
    capture = make_capture(tmp_path)
    stubs = Stubs(register_error="the session index is locked")

    handoff = finish_session(capture, archive_dir=out_dir, **stubs.as_kwargs())

    assert handoff.laps == 0
    assert handoff.archive.is_file()
    assert any("the session index is locked" in line for line in handoff.lines)


def test_a_race_with_no_complete_lap_is_not_sent(tmp_path, out_dir):
    """There is nothing to review, and their pipeline would say so slowly."""
    capture = make_capture(tmp_path)
    stubs = Stubs(registered=FakeRegistered(laps=0))

    handoff = finish_session(capture, archive_dir=out_dir, **stubs.as_kwargs())

    assert handoff.archive.is_file()
    assert handoff.bundle is None
    assert stubs.delivered == []
    assert any("no complete lap" in line for line in handoff.lines)


@pytest.mark.parametrize("phase", ["baseline", "control", "familiarisation"])
def test_only_a_coached_race_opens_the_review(tmp_path, out_dir, phase):
    """Their own control build collects the race and never opens the review.

    Opening it for a participant in any other arm hands them the page the study
    is keeping from them, whatever the server would have shown.
    """
    capture = make_capture(tmp_path, phase=phase)
    stubs = Stubs()

    handoff = finish_session(capture, archive_dir=out_dir, **stubs.as_kwargs())

    assert handoff.delivered
    assert stubs.opened == []
    assert not handoff.opened
    assert any("withholds coaching" in line for line in handoff.lines)


def test_a_coached_race_names_the_assignment_somebody_still_has_to_make(tmp_path, out_dir):
    """Their server ignores the arm a bundle declares; only an assignment counts.

    So a coached race that nobody assigns opens on a page with no coach on it,
    and the person who could fix that is the one reading this line.
    """
    capture = make_capture(tmp_path, participant="B0826")

    handoff = finish_session(capture, archive_dir=out_dir, **Stubs().as_kwargs())

    assert any("name:b0826" in line for line in handoff.lines)
    assert any("Operations Dashboard" in line for line in handoff.lines)


def test_the_review_can_be_opened_for_any_arm_when_asked(tmp_path, out_dir):
    """A researcher checking their own upload is not a participant."""
    capture = make_capture(tmp_path, phase="baseline")
    stubs = Stubs()

    finish_session(capture, archive_dir=out_dir, open_review=True, **stubs.as_kwargs())

    assert stubs.opened == ["https://demo.lzqqq.org/"]


def test_upload_can_be_refused_outright(tmp_path, out_dir):
    capture = make_capture(tmp_path)
    stubs = Stubs()

    handoff = finish_session(capture, archive_dir=out_dir, upload=False, **stubs.as_kwargs())

    assert handoff.archive.is_file()
    assert handoff.bundle is None
    assert stubs.delivered == []


def test_what_is_sent_carries_the_questionnaire_and_the_session_number(tmp_path, out_dir):
    capture = make_capture(tmp_path)
    stubs = Stubs()

    finish_session(capture, archive_dir=out_dir, session_number=2, **stubs.as_kwargs())

    assert stubs.exported == [
        {"session_number": 2, "include_background": True, "include_video": True}
    ]


def test_the_recording_can_be_left_out_of_what_is_sent(tmp_path, out_dir):
    """Their host refuses a body over 300 MB, and a long race packs past it."""
    capture = make_capture(tmp_path)
    stubs = Stubs()

    finish_session(capture, archive_dir=out_dir, include_video=False, **stubs.as_kwargs())

    assert stubs.exported[0]["include_video"] is False


def test_a_capture_with_no_manifest_is_treated_as_an_undeclared_arm(tmp_path, out_dir):
    """Undeclared is not coached, and the safe direction is not to open it.

    A missing manifest is a capture something went wrong in; guessing "coached"
    from a folder name would show a page to somebody the study may be keeping it
    from, on the strength of a string.
    """
    capture = tmp_path / "P009-coached-20260902-101500"
    capture.mkdir()
    stubs = Stubs()

    handoff = finish_session(capture, archive_dir=out_dir, **stubs.as_kwargs())

    assert handoff.delivered
    assert stubs.opened == []


def test_the_progress_lines_are_reported_as_they_happen(tmp_path, out_dir):
    """The slow step is an upload; a screen that says nothing looks broken."""
    capture = make_capture(tmp_path)
    seen: list[str] = []

    handoff = finish_session(
        capture, archive_dir=out_dir, progress=seen.append, **Stubs().as_kwargs()
    )

    assert seen == list(handoff.lines)
    assert seen[0].startswith("ok")
