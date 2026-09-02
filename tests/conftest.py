"""Shared test setup.

A modal dialog with nobody to dismiss it does not fail a headless test -- it
hangs it, and a suite that hangs teaches nothing. The participant questionnaire
is the only modal raised on a normal path, so it is neutralised here for every
test, and the tests that care about it drive it explicitly instead.

The stored Qt settings are redirected for the same class of reason: left alone,
the suite reads the machine it happens to be running on.
"""

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings


@pytest.fixture(autouse=True)
def no_background_prompt(monkeypatch):
    monkeypatch.setattr(
        "apex.capture_view._prompt_for_background",
        lambda _participant_id, _parent: None,
        raising=False,
    )


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Keep stored Qt settings out of the developer's own Apex install.

    ``_research_mode_enabled`` falls back to the stored setting when the
    environment variable is absent -- precisely the state the "research tools
    are off by default" assertions depend on. On Windows that store is the
    user's registry, so a developer who once turned Research tools on in the
    real app made two unrelated tests fail on their machine and pass everywhere
    else. CI never saw it because its runners start empty.

    ``QSettings.setDefaultFormat`` does not reach the ``QSettings(org, app)``
    constructor the app uses -- only the constructor that is handed a format
    explicitly -- so redirect that constructor itself. Every settings object the
    app creates during a test is then backed by a file under the test's own
    directory: nothing a test writes reaches the real app, and nothing the
    developer has set reaches a test.
    """
    store = tmp_path / "qt-settings.ini"

    def isolated(*_args, **_kwargs):
        return QSettings(str(store), QSettings.Format.IniFormat)

    monkeypatch.setattr("apex.main_window.QSettings", isolated)


@pytest.fixture(scope="session", autouse=True)
def workspace_floor(tmp_path_factory):
    """A workspace for the whole run, underneath the per-test one.

    The per-test fixture below is undone at teardown, and a worker that outlives
    its test -- the coaching queue runs on a pool -- then writes with the
    environment already restored. Measured: one granite audit record per full
    run, landing in ~/Apex/coaching beside the participants' own, written at a
    moment nobody was driving and identical in shape to a real one.

    This one is never undone, so a late write has somewhere harmless to go.
    """
    floor = tmp_path_factory.mktemp("apex-workspace-floor")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("APEX_WORKSPACE", str(floor))
        yield floor


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch, workspace_floor):
    """Keep every test out of the workspace the study's own data lives in.

    Most tests already set ``APEX_WORKSPACE`` themselves, and the ones that do
    still win -- their own fixture runs after this one. The ones that did not
    were writing into ``~/Apex``: a run of the suite left mock coaching records
    in the same folder as the participants', identical in shape to a real one
    and distinguishable only by having been written at a moment nobody was
    driving. Nothing downstream separates them, and the audit trail is one of
    the things the study is measuring.

    Not a cleanup afterwards, because a test that crashes never reaches it, and
    because the file has already been created in the wrong place by then.
    """
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "apex-workspace"))


@pytest.fixture(autouse=True)
def no_automatic_handoff(monkeypatch):
    """Finishing a session must not really save, send, or open anything.

    The capture view starts the hand-off by itself now, which is the point of
    it, and that reaches three things a test has no business touching: the
    participant's Desktop, the team's server, and a browser. Neutralised for
    every test; the ones that care drive `finish_fn` explicitly instead.
    """
    from racecoach.telemetry.handoff import Handoff

    monkeypatch.setattr(
        "apex.capture_view.finish_session",
        lambda capture_dir, **_kwargs: Handoff(capture_dir=Path(capture_dir)),
        raising=False,
    )
