"""Shared test setup.

A modal dialog with nobody to dismiss it does not fail a headless test -- it
hangs it, and a suite that hangs teaches nothing. The participant questionnaire
is the only modal raised on a normal path, so it is neutralised here for every
test, and the tests that care about it drive it explicitly instead.

The stored Qt settings are redirected for the same class of reason: left alone,
the suite reads the machine it happens to be running on.
"""

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
