"""Bob Shell automation: the subprocess wrapper, export writing, CLI wiring.

Bob Shell isn't installable in CI, so a stub `bob` (RACECOACH_BOBSHELL_BIN)
stands in — the same pattern as the SCR stub used for `racecoach run`.
"""

import json
import os
from datetime import date

import pytest

from racecoach.cli import main
from racecoach.ibm.bob import list_exports
from racecoach.ibm.bobshell import (
    BobShellError,
    analyze_with_bobshell,
    run_bobshell,
    write_export,
)
from racecoach.telemetry.run_store import import_run, list_runs, load_run
from test_racecoach_analysis import make_run_frame

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="the Bob Shell test double is a POSIX shell script"
)

TODAY = date.today().isoformat()
ANSWER = "The controller brakes on lookahead curvature and steers on track-angle error."
HAPPY = """\
if [ -n "$BOB_STUB_RECORD" ]; then
printf 'args:%s\\n' "$*" > "$BOB_STUB_RECORD"
printf 'key:%s\\n' "$BOBSHELL_API_KEY" >> "$BOB_STUB_RECORD"
fi
echo "thinking: reading the files..."
echo "BOB_ANSWER_BEGIN"
echo "{answer}"
echo "BOB_ANSWER_END"
echo "post-answer chatter"
"""


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    exports = tmp_path / "exports"
    exports.mkdir()
    monkeypatch.setenv("RACECOACH_BOB_EXPORTS", str(exports))
    monkeypatch.delenv("BOBSHELL_API_KEY", raising=False)
    monkeypatch.delenv("RACECOACH_BOBSHELL_BIN", raising=False)
    monkeypatch.delenv("BOB_STUB_RECORD", raising=False)
    return exports


@pytest.fixture
def workdir(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "driver.cpp").write_text("void drive() {}")
    return repo


@pytest.fixture
def key_file(tmp_path):
    path = tmp_path / "keyfile.json"
    path.write_text(json.dumps({"name": "Apex", "apikey": "bob_test_key_123"}))
    return path


def stub_bob(tmp_path, monkeypatch, body):
    stub = tmp_path / "stub" / "bob"
    stub.parent.mkdir(exist_ok=True)
    stub.write_text("#!/bin/sh\n" + body)
    stub.chmod(0o755)
    monkeypatch.setenv("RACECOACH_BOBSHELL_BIN", str(stub))
    return stub


def test_happy_path_archives_a_dated_marker_extracted_export(
    tmp_path, monkeypatch, workdir, key_file
):
    stub_bob(tmp_path, monkeypatch, HAPPY.format(answer=ANSWER))
    record = tmp_path / "record.txt"
    monkeypatch.setenv("BOB_STUB_RECORD", str(record))

    destination = analyze_with_bobshell(
        "How does braking work?", ["driver.cpp"],
        topic="Braking Retro!", cwd=workdir, key_file=key_file,
    )

    assert destination.name == f"{TODAY}-braking-retro.md"
    text = destination.read_text("utf-8")
    assert text.startswith("---\ntool: IBM Bob (Bob Shell)\n")
    assert f"date: {TODAY}" in text and "scope: driver.cpp" in text
    assert "prompt: How does braking work?" in text
    assert ANSWER in text
    assert "thinking" not in text and "chatter" not in text  # markers won
    assert destination in list_exports()  # coach --bob will see it

    recorded = record.read_text("utf-8")
    assert "--auth-method api-key" in recorded and "--hide-intermediary-output" in recorded
    assert "How does braking work?" in recorded  # the prompt travels positionally
    assert " -p " not in recorded  # ... not behind the deprecated stdin-reading -p
    assert "key:bob_test_key_123" in recorded  # key arrived via the environment
    assert recorded.count("bob_test_key_123") == 1  # ... and never via argv


def test_key_resolution_prefers_env_and_reads_as_instructions(
    tmp_path, monkeypatch, workdir
):
    stub_bob(tmp_path, monkeypatch, HAPPY.format(answer=ANSWER))
    with pytest.raises(BobShellError, match="BOBSHELL_API_KEY"):
        run_bobshell("q", ["driver.cpp"], cwd=workdir)
    monkeypatch.setenv("BOBSHELL_API_KEY", "bob_env_key")
    assert ANSWER in run_bobshell("q", ["driver.cpp"], cwd=workdir)


def test_unmarked_output_is_kept_and_empty_output_is_an_error(
    tmp_path, monkeypatch, workdir, key_file
):
    stub_bob(tmp_path, monkeypatch, f'echo "plain {ANSWER}"\n')
    text = run_bobshell("q", ["driver.cpp"], cwd=workdir, key_file=key_file)
    assert text == f"plain {ANSWER}"

    stub_bob(tmp_path, monkeypatch, 'echo "BOB_ANSWER_BEGIN"\necho\necho "BOB_ANSWER_END"\n')
    with pytest.raises(BobShellError, match="empty answer"):
        run_bobshell("q", ["driver.cpp"], cwd=workdir, key_file=key_file)


def test_missing_binary_names_the_install_script(tmp_path, monkeypatch, workdir, key_file):
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(BobShellError, match="bob.ibm.com"):
        run_bobshell("q", ["driver.cpp"], cwd=workdir, key_file=key_file)


def test_license_and_auth_failures_read_as_instructions(
    tmp_path, monkeypatch, workdir, key_file
):
    stub_bob(tmp_path, monkeypatch, 'echo "You must accept the license agreement" >&2\nexit 1\n')
    with pytest.raises(BobShellError, match="--accept-license"):
        run_bobshell("q", ["driver.cpp"], cwd=workdir, key_file=key_file)

    stub_bob(tmp_path, monkeypatch, 'echo "401 unauthorized" >&2\nexit 1\n')
    with pytest.raises(BobShellError, match="Inference"):
        run_bobshell("q", ["driver.cpp"], cwd=workdir, key_file=key_file)


def test_hung_bob_times_out_readably(tmp_path, monkeypatch, workdir, key_file):
    stub_bob(tmp_path, monkeypatch, "sleep 2\n")
    with pytest.raises(BobShellError, match="--timeout"):
        run_bobshell("q", ["driver.cpp"], cwd=workdir, key_file=key_file, timeout_s=0.2)


def test_missing_analysis_files_are_a_readable_error(tmp_path, monkeypatch, workdir, key_file):
    stub_bob(tmp_path, monkeypatch, HAPPY.format(answer=ANSWER))
    with pytest.raises(BobShellError, match="No such file"):
        run_bobshell("q", ["nope.cpp"], cwd=workdir, key_file=key_file)


def test_same_day_topic_collisions_get_a_counter_and_template_is_rejected():
    first = write_export(ANSWER, question="q", files=["a.py"], topic="retro")
    second = write_export(ANSWER, question="q", files=["a.py"], topic="retro")
    assert first.name == f"{TODAY}-retro.md"
    assert second.name == f"{TODAY}-retro-2.md"
    with pytest.raises(BobShellError, match="template"):
        write_export(ANSWER, question="q", files=["a.py"], topic="my-template")


def test_cli_bob_analyze_feeds_coach_bob_end_to_end(
    tmp_path, monkeypatch, workdir, key_file, capsys
):
    stub_bob(tmp_path, monkeypatch, HAPPY.format(answer=ANSWER))
    csv = tmp_path / "bot_run.csv"
    make_run_frame().to_csv(csv, index=False)
    import_run(csv)
    run_id = list_runs()[0].run_id

    assert main([
        "bob-analyze", "driver.cpp",
        "--cwd", str(workdir), "--key-file", str(key_file), "--topic", "retro",
    ]) == 0
    out = capsys.readouterr().out
    assert "archived as" in out and f"{TODAY}-retro.md" in out

    assert main(["coach", run_id, "--bob"]) == 0
    assert f"Grounding feedback with Bob export {TODAY}-retro.md" in capsys.readouterr().out

    # the audit record proves the chain: Bob Shell answer -> prompt -> feedback
    run = load_run(run_id)
    (audit,) = (run.path / "coaching").glob("*-mock.json")
    record = json.loads(audit.read_text("utf-8"))
    assert "lookahead curvature" in record["prompt"]


def test_bob_never_inherits_stdin(tmp_path, monkeypatch, workdir, key_file):
    """An interactive fallback inside bob (login, instance picker) must fail
    fast on EOF, never sit waiting on the demo machine's terminal."""
    import subprocess

    from racecoach.ibm import bobshell

    stub_bob(tmp_path, monkeypatch, HAPPY.format(answer=ANSWER))
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"], seen["stdin"] = command, kwargs.get("stdin")
        return subprocess.CompletedProcess(command, 0, stdout=ANSWER, stderr="")

    monkeypatch.setattr(bobshell.subprocess, "run", fake_run)
    run_bobshell("q", ["driver.cpp"], cwd=workdir, key_file=key_file)
    assert seen["stdin"] is subprocess.DEVNULL
    assert "-p" not in seen["command"] and seen["command"][-1].startswith("q")


def test_ctrl_c_mid_analysis_reads_as_interrupted(monkeypatch, capsys):
    import racecoach.cli as cli

    def boom(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "analyze_with_bobshell", boom)
    assert main(["bob-analyze"]) == 130
    assert "racecoach: interrupted." in capsys.readouterr().err


def test_cli_bob_analyze_failure_is_exit_2(tmp_path, monkeypatch, workdir, key_file, capsys):
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    code = main(["bob-analyze", "driver.cpp", "--cwd", str(workdir), "--key-file", str(key_file)])
    assert code == 2
    assert "racecoach: Bob Shell is not installed" in capsys.readouterr().err
