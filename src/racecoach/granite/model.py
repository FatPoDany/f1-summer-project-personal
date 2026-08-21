"""The Granite weights file: where it lives, whether it is trustworthy, how it arrives.

The pins here are the same ones ``integrations/granite-4.1/download-model.sh``
uses, and deliberately so: a participant's machine and a researcher's shell must
end up with byte-identical weights or the two coaching paths are not comparable.

The download is resumable because this is a 2.1 GB file over a home connection.
It lands on a ``.part`` file and is only renamed into place once size and digest
both match, so an interrupted download can never be mistaken for a usable model.
"""

import hashlib
import os
import shutil
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from f1coach_core.workspace import workspace_root

MODEL_REPO = "ibm-granite/granite-4.1-3b-GGUF"
MODEL_REVISION = "ab4701481089b58a082ef63cc1cee738887293ff"
MODEL_FILE = "granite-4.1-3b-Q4_K_M.gguf"
MODEL_SIZE = 2_099_501_664
MODEL_SHA256 = "662b0626cd58f443baea23559b469df6576a81d349649c59413b36a9fb32eb29"
_PATH = f"{MODEL_REPO}/resolve/{MODEL_REVISION}/{MODEL_FILE}"
# Tried in order. A mirror is safe here precisely because of the digest above:
# bytes that do not match the pin are discarded whoever served them, so the only
# thing a second source can change is whether the download completes at all.
# huggingface.co is unreachable from some of the networks participants are on.
MODEL_URLS = (
    f"https://huggingface.co/{_PATH}",
    f"https://hf-mirror.com/{_PATH}",
)
MODEL_URL = MODEL_URLS[0]


def model_urls() -> tuple[str, ...]:
    """Where to look for the weights, most preferred first."""
    override = os.environ.get("GRANITE_MODEL_URL")
    return (override,) if override else MODEL_URLS

_CHUNK = 1024 * 1024


class ModelError(RuntimeError):
    """The weights are absent, untrustworthy, or could not be fetched."""


@dataclass(frozen=True)
class Progress:
    """How far a download has got. ``total`` is the pinned size, never a guess."""

    received: int
    total: int

    @property
    def fraction(self) -> float:
        return 0.0 if self.total <= 0 else min(1.0, self.received / self.total)


ProgressHook = Callable[[Progress], None]


def model_cache() -> Path:
    """Where weights live. Inside the workspace, not the temp directory.

    The shell scripts default to ``$TMPDIR`` because they serve a developer who
    can re-run them. A participant would lose a 2.1 GB download to the next
    reboot, so the packaged app keeps it beside their sessions instead.
    """
    configured = os.environ.get("GRANITE_MODEL_CACHE")
    return Path(configured) if configured else workspace_root() / "models"


def model_path() -> Path:
    configured = os.environ.get("GRANITE_MODEL_PATH")
    return Path(configured) if configured else model_cache() / MODEL_FILE


def digest_of(path: str | Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            sha.update(chunk)
    return sha.hexdigest()


def is_verified(path: str | Path) -> bool:
    """True only when this is exactly the pinned file.

    Size is checked first because it is free and rejects every truncated
    download without reading 2.1 GB.
    """
    path = Path(path)
    try:
        if path.stat().st_size != MODEL_SIZE:
            return False
    except OSError:
        return False
    return digest_of(path) == MODEL_SHA256


def available() -> bool:
    """Whether coaching can run locally right now, without downloading anything."""
    return is_verified(model_path())


def free_space_bytes(directory: str | Path) -> int:
    """Room on the volume that would hold the download, creating nothing."""
    probe = Path(directory)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def ensure_model(
    *,
    on_progress: ProgressHook | None = None,
    should_cancel: Callable[[], bool] | None = None,
    opener: Callable[[urllib.request.Request], object] | None = None,
) -> Path:
    """Return a verified weights file, downloading it if that is what it takes.

    Raises ModelError rather than returning an unverified path: a caller that
    got a path back is entitled to assume the bytes are the pinned ones.
    """
    destination = model_path()
    if is_verified(destination):
        return destination

    if destination.exists():
        # Present but wrong. Deleting someone's file silently is worse than
        # stopping, because the usual cause is a half-copied or hand-placed
        # model that the person still believes in.
        raise ModelError(
            f"{destination} is not the pinned Granite model. Expected {MODEL_SIZE} "
            f"bytes with SHA-256 {MODEL_SHA256}. Move or delete it, then try again."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    received = partial.stat().st_size if partial.exists() else 0
    if received > MODEL_SIZE:
        partial.unlink()
        received = 0

    needed = MODEL_SIZE - received
    if free_space_bytes(destination.parent) < needed:
        raise ModelError(
            f"Not enough free space for the Granite model: {needed / 1e9:.1f} GB "
            f"still needed under {destination.parent}."
        )

    _download_from_any(partial, received, on_progress, should_cancel, opener)

    if not is_verified(partial):
        partial.unlink(missing_ok=True)
        raise ModelError(
            "The downloaded Granite model failed verification and has been "
            "discarded. This is usually a truncated or intercepted download; "
            "trying again is safe."
        )
    partial.replace(destination)
    return destination


def _download_from_any(
    partial: Path,
    received: int,
    on_progress: ProgressHook | None,
    should_cancel: Callable[[], bool] | None,
    opener: Callable[[urllib.request.Request], object] | None,
) -> None:
    """Try each source in turn, giving up only when every one has failed."""
    failures: list[str] = []
    for url in model_urls():
        try:
            _download(url, partial, received, on_progress, should_cancel, opener)
            return
        except ModelError as exc:
            if should_cancel and should_cancel():
                raise
            failures.append(f"{url}: {exc}")
            # A source that answered partially leaves usable bytes behind, so the
            # next one resumes from wherever this one stopped.
            received = partial.stat().st_size if partial.exists() else 0
    raise ModelError(
        "Could not download the Granite model from any known source.\n  "
        + "\n  ".join(failures)
        + f"\nIf your network cannot reach these, ask the study team for "
        f"{MODEL_FILE} and put it at {model_path()}, or run "
        f"`racecoach install-model <file>`."
    )


def import_model(source: str | Path) -> Path:
    """Adopt a copy of the weights someone supplied by other means.

    Networks that cannot reach a model host are exactly the case this exists for,
    and the digest check means a file that arrived on a memory stick is worth no
    less than one that arrived over HTTPS.
    """
    source = Path(source)
    if not source.is_file():
        raise ModelError(f"No such file: {source}")
    if not is_verified(source):
        raise ModelError(
            f"{source} is not the pinned Granite model. Expected {MODEL_SIZE} "
            f"bytes with SHA-256 {MODEL_SHA256}."
        )
    destination = model_path()
    if is_verified(destination):
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    if free_space_bytes(destination.parent) < MODEL_SIZE:
        raise ModelError(f"Not enough free space under {destination.parent}.")
    shutil.copyfile(source, destination)
    return destination


def _download(
    url: str,
    partial: Path,
    received: int,
    on_progress: ProgressHook | None,
    should_cancel: Callable[[], bool] | None,
    opener: Callable[[urllib.request.Request], object] | None,
) -> None:
    request = urllib.request.Request(url)  # noqa: S310 - pinned https URL
    if received:
        request.add_header("Range", f"bytes={received}-")
    open_url = opener or (lambda req: urllib.request.urlopen(req))  # noqa: S310

    if on_progress:
        on_progress(Progress(received, MODEL_SIZE))
    try:
        response = open_url(request)
    except (urllib.error.URLError, OSError) as exc:
        raise ModelError(f"could not be reached ({exc})") from exc

    # A server that ignores the Range header answers 200 and starts from zero;
    # appending that to what we already have would corrupt the file silently.
    status = getattr(response, "status", None) or getattr(response, "code", None)
    mode = "ab"
    if received and status != 206:
        received = 0
        mode = "wb"

    with response, open(partial, mode) as handle:
        while True:
            if should_cancel and should_cancel():
                raise ModelError("The model download was cancelled.")
            chunk = response.read(_CHUNK)
            if not chunk:
                break
            handle.write(chunk)
            received += len(chunk)
            if on_progress:
                on_progress(Progress(received, MODEL_SIZE))
