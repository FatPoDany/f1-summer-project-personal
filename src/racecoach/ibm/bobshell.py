"""Drive IBM Bob Shell non-interactively and archive the answer as an export.

Bob Shell (`bob -p`, API-key auth) is the scriptable side of IBM Bob. This
module runs one code analysis and lands the answer in the exports archive in
the same dated format as the manual IDE flow, so `racecoach coach --bob` and
the evidence chain downstream need no changes. The API key travels only via
the subprocess environment (BOBSHELL_API_KEY), never argv. Errors are
readable: this path is driven from the CLI by people mid-demo.

Set RACECOACH_BOBSHELL_BIN to override which `bob` executable is used
(tests point it at a stub; there is no runnable Bob Shell in CI).
"""

import json
import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

from racecoach.ibm.bob import exports_root

ANSWER_BEGIN = "BOB_ANSWER_BEGIN"
ANSWER_END = "BOB_ANSWER_END"
DEFAULT_TIMEOUT_S = 300.0
DEFAULT_QUESTION = (
    "Summarise how this driver decides braking points, steering, and throttle. "
    "List parameters and conditions that limit cornering speed."
)
INSTALL_HINT = "curl -fsSL https://bob.ibm.com/download/bobshell.sh | bash"


class BobShellError(RuntimeError):
    """Bob Shell could not produce an archivable answer. Messages are for people."""


def bob_binary() -> str:
    name = os.environ.get("RACECOACH_BOBSHELL_BIN", "bob")
    found = shutil.which(name)
    if not found:
        raise BobShellError(
            f"Bob Shell is not installed (no `{name}` on PATH). Install it with\n"
            f"  {INSTALL_HINT}\n"
            "then run once with --accept-license. See docs/bob/README.md."
        )
    return found


def resolve_api_key(key_file: Path | None = None) -> str:
    key = os.environ.get("BOBSHELL_API_KEY", "").strip()
    if key:
        return key
    if key_file is None:
        raise BobShellError(
            "No Bob Shell API key: set BOBSHELL_API_KEY or pass --key-file pointing at a "
            'JSON file with an "apikey" field (downloaded from the Bob web portal). '
            "Non-interactive Bob Shell needs a key with scope Inference."
        )
    key_file = Path(key_file)
    if not key_file.is_file():
        raise BobShellError(f"No such key file: {key_file}")
    try:
        data = json.loads(key_file.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        raise BobShellError(f"{key_file} is not valid JSON: {exc}") from exc
    key = str(data.get("apikey") or "").strip()
    if not key:
        raise BobShellError(f'{key_file} has no "apikey" field.')
    return key


def build_prompt(question: str, files: list[str]) -> str:
    refs = " ".join(f"@{name}" for name in files)
    return (
        f"{question}\n\n"
        f"Analyse these files: {refs}\n\n"
        f"Enclose your final answer between a line reading exactly {ANSWER_BEGIN} "
        f"and a line reading exactly {ANSWER_END}."
    )


def extract_answer(output: str) -> str:
    """The marked answer if Bob honoured the markers, otherwise the whole output.

    `bob -p` may print thinking steps around the answer even with
    --hide-intermediary-output, so the prompt asks for markers and the last
    marked block wins; unmarked output is kept as-is rather than dropped.
    """
    blocks = re.findall(
        rf"^{ANSWER_BEGIN}[ \t]*$(.*?)^{ANSWER_END}[ \t]*$",
        output,
        re.DOTALL | re.MULTILINE,
    )
    text = (blocks[-1] if blocks else output).strip()
    if not text:
        raise BobShellError("Bob Shell returned an empty answer; nothing archived.")
    return text


def run_bobshell(
    question: str,
    files: list[str],
    *,
    cwd: str | Path | None = None,
    key_file: Path | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    accept_license: bool = False,
) -> str:
    workdir = Path(cwd) if cwd else Path.cwd()
    if not workdir.is_dir():
        raise BobShellError(f"No such working directory: {workdir}")
    missing = [name for name in files if not (workdir / name).is_file()]
    if missing:
        raise BobShellError(
            f"No such file(s) under {workdir}: {', '.join(missing)}. "
            "Bob Shell reads @-references relative to where it runs (--cwd)."
        )
    binary = bob_binary()
    key = resolve_api_key(key_file)
    # Flags verified against the installed bobshell 1.0.6 bundle: --auth-method is a
    # hidden option with choices ["sso", "api-key"]; the prompt goes positionally
    # because -p is deprecated ("appended to input on stdin"). stdin is detached so
    # no interactive fallback (login, instance picker, license) can hang a demo —
    # with EOF on stdin such steps fail fast and their message reaches our error.
    command = [binary, "--auth-method", "api-key", "--hide-intermediary-output"]
    if accept_license:
        command.append("--accept-license")
    command.append(build_prompt(question, files))
    env = {**os.environ, "BOBSHELL_API_KEY": key}
    try:
        proc = subprocess.run(
            command, cwd=workdir, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        raise BobShellError(
            f"Bob Shell gave no answer within {timeout_s:.0f}s. Slow analyses can be "
            "given more room with --timeout; also check the network/proxy."
        ) from None
    if proc.returncode != 0:
        raise BobShellError(_explain_failure(proc))
    return extract_answer(proc.stdout)


def _explain_failure(proc: subprocess.CompletedProcess) -> str:
    noise = f"{proc.stderr}\n{proc.stdout}".lower()
    tail = (proc.stderr or proc.stdout).strip()[-400:]
    if "license" in noise:
        return (
            "Bob Shell needs its license accepted once: re-run with --accept-license, "
            f"or start `bob` interactively. It said:\n{tail}"
        )
    if any(marker in noise for marker in ("unauthorized", "api key", "api-key", "401")):
        return (
            "Bob Shell rejected the API key: check BOBSHELL_API_KEY / --key-file, and that "
            f"the key's scope is Inference (Bob web portal). It said:\n{tail}"
        )
    return f"Bob Shell exited with code {proc.returncode}:\n{tail}"


def write_export(answer: str, *, question: str, files: list[str], topic: str) -> Path:
    root = exports_root()
    root.mkdir(parents=True, exist_ok=True)
    day = date.today().isoformat()
    slug = _slug(topic)
    destination = root / f"{day}-{slug}.md"
    counter = 2
    while destination.exists():
        destination = root / f"{day}-{slug}-{counter}.md"
        counter += 1
    header = (
        "---\n"
        "tool: IBM Bob (Bob Shell)\n"
        f"date: {day}\n"
        f"scope: {', '.join(files)}\n"
        f"prompt: {' '.join(question.split())}\n"
        "---\n\n"
    )
    destination.write_text(header + answer + "\n", encoding="utf-8")
    return destination


def _slug(topic: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
    if not slug:
        raise BobShellError(f"--topic {topic!r} has no usable characters for a filename.")
    if "template" in slug:
        raise BobShellError(
            '--topic must not contain "template": the coach ignores such exports '
            "(docs/bob/README.md)."
        )
    return slug


def analyze_with_bobshell(
    question: str,
    files: list[str],
    *,
    topic: str,
    cwd: str | Path | None = None,
    key_file: Path | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    accept_license: bool = False,
) -> Path:
    """Run one Bob Shell analysis and archive it; returns the export path."""
    answer = run_bobshell(
        question,
        files,
        cwd=cwd,
        key_file=key_file,
        timeout_s=timeout_s,
        accept_license=accept_license,
    )
    return write_export(answer, question=question, files=files, topic=topic)
