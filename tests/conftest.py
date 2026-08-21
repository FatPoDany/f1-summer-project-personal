"""Shared test setup.

A modal dialog with nobody to dismiss it does not fail a headless test -- it
hangs it, and a suite that hangs teaches nothing. The participant questionnaire
is the only modal raised on a normal path, so it is neutralised here for every
test, and the tests that care about it drive it explicitly instead.
"""

import pytest


@pytest.fixture(autouse=True)
def no_background_prompt(monkeypatch):
    monkeypatch.setattr(
        "apex.capture_view._prompt_for_background",
        lambda _participant_id, _parent: None,
        raising=False,
    )
