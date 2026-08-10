"""IBM Bob export archive: listing, ingestion, and the --bob CLI wiring."""

import json

import pytest

from racecoach.cli import main
from racecoach.ibm.bob import BobExportError, list_exports, load_export, newest_export
from racecoach.telemetry.run_store import import_run, list_runs, load_run
from test_racecoach_analysis import make_run_frame

EXPORT = """---
tool: IBM Bob (Bob IDE)
date: {date}
scope: drivers/bt controller
prompt: summarise the braking and steering strategy
---

The controller brakes when the lookahead curvature exceeds a fixed threshold
and holds steer_cmd proportional to track angle error.
"""


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    exports = tmp_path / "exports"
    exports.mkdir()
    monkeypatch.setenv("RACECOACH_BOB_EXPORTS", str(exports))
    return exports


def test_exports_are_listed_dated_and_template_free(isolated):
    (isolated / "2026-07-10-overview.md").write_text(EXPORT.format(date="2026-07-10"))
    (isolated / "2026-07-12-retro.md").write_text(EXPORT.format(date="2026-07-12"))
    (isolated / "2026-07-12-template.md").write_text("TEMPLATE")
    (isolated / "notes.pdf").write_text("not an export")

    names = [path.name for path in list_exports()]
    assert names == ["2026-07-10-overview.md", "2026-07-12-retro.md"]
    assert newest_export().name == "2026-07-12-retro.md"


def test_missing_or_empty_exports_read_as_instructions(isolated):
    with pytest.raises(BobExportError, match="docs/bob/README.md"):
        newest_export()
    empty = isolated / "2026-07-12-empty.md"
    empty.write_text("   ")
    with pytest.raises(BobExportError, match="archive Bob's actual answer"):
        load_export(empty)


def test_long_exports_are_trimmed_for_the_prompt(isolated):
    big = isolated / "2026-07-12-big.md"
    big.write_text("x" * 10_000)
    text = load_export(big)
    assert len(text) < 10_000 and text.endswith("[… trimmed for the prompt …]")


def test_cli_coach_bob_grounds_the_prompt_with_the_export(isolated, tmp_path, capsys):
    (isolated / "2026-07-12-retro.md").write_text(EXPORT.format(date="2026-07-12"))
    csv = tmp_path / "bot_run.csv"
    make_run_frame().to_csv(csv, index=False)
    import_run(csv)
    run_id = list_runs()[0].run_id

    assert main(["coach", run_id, "--bob"]) == 0
    assert "Grounding feedback with Bob export 2026-07-12-retro.md" in capsys.readouterr().out

    # the audit record proves the chain: Bob text -> prompt -> feedback
    run = load_run(run_id)
    (audit,) = (run.path / "coaching").glob("*-mock.json")
    record = json.loads(audit.read_text("utf-8"))
    assert "lookahead curvature" in record["prompt"]
    assert "IBM Bob analysis" in record["prompt"]


def test_cli_coach_bob_without_exports_is_a_readable_error(isolated, tmp_path, capsys):
    csv = tmp_path / "bot_run.csv"
    make_run_frame().to_csv(csv, index=False)
    import_run(csv)
    run_id = list_runs()[0].run_id

    assert main(["coach", run_id, "--bob"]) == 2
    assert "docs/bob/README.md" in capsys.readouterr().err
