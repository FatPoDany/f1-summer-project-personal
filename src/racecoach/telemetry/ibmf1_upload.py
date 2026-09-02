"""Sending a translated bundle to the team's Coach server, and waiting for it.

The server takes the whole ZIP in one POST, answers with a job id, and runs its
pipeline asynchronously; the race is not reviewable until that job finishes. So
an upload that returns without polling has told the participant nothing useful.

No credential lives in this repository. The token is read from the environment,
or from the configuration file their own player kit ships, and a missing one is
an error with instructions rather than a silent unauthenticated attempt that
comes back 401.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# Their production import host. Deliberately not the site's own origin: that one
# is behind a CDN which refuses a body over about 100 MB, and a race packs past
# that, so the upload is answered 413 by the edge before the server sees it.
DEFAULT_ORIGIN = "https://upload.lzqqq.org"
IMPORT_PATH = "/api/import"
JOB_PATH = "/api/import/jobs"

# Where a stored race is read, which is not where it was sent. The import host
# runs no site: a driver pointed at it is shown nothing. Their own player kit
# keeps the two apart the same way -- player_kit/tools/coach_config.py builds its
# review_url from coach_origin and its import_url from upload_origin.
DEFAULT_REVIEW_ORIGIN = "https://demo.lzqqq.org"

DEFAULT_MAX_UPLOAD_MB = 300  # nginx's limit on that host
BUSY_DELAYS_S = (4, 8, 16, 32)
POLL_INTERVAL_S = 2.0
JOB_TIMEOUT_S = 15 * 60

# A poll can fail because this workspace briefly lost the network rather than
# because anything happened to the race, and the pipeline runs for minutes, so
# over one import there is real time for a DNS hiccup to land in. Measured: a
# 76 MB bundle was accepted, its job started, and the client then died on
# getaddrinfo while their server was still working -- reporting a failure for an
# import that was fine. These bound how long a quiet network is tolerated before
# the wait gives up and says it does not know.
UNREACHABLE_LIMIT = 10
UNREACHABLE_DELAY_S = 15.0

TOKEN_ENV = "IBMF1_UPLOAD_TOKEN"
ORIGIN_ENV = "IBMF1_UPLOAD_ORIGIN"
REVIEW_ORIGIN_ENV = "IBMF1_REVIEW_ORIGIN"
MAX_UPLOAD_ENV = "IBMF1_MAX_UPLOAD_MB"


class IbmF1UploadError(RuntimeError):
    """A bundle could not be delivered, or the server could not process it."""


@dataclass(frozen=True)
class Delivered:
    """What the Coach server did with a bundle."""

    review_url: str
    duplicate: bool
    message: str
    session: dict


@dataclass(frozen=True)
class Endpoint:
    origin: str
    token: str
    max_upload_bytes: int
    review_origin: str = DEFAULT_REVIEW_ORIGIN

    @property
    def import_url(self) -> str:
        return f"{self.origin.rstrip('/')}{IMPORT_PATH}"

    @property
    def job_url(self) -> str:
        return f"{self.origin.rstrip('/')}{JOB_PATH}"

    @property
    def review_url(self) -> str:
        return f"{self.review_origin.rstrip('/')}/"


def _upload_limit(environ: dict[str, str]) -> float:
    try:
        return float(environ.get(MAX_UPLOAD_ENV) or DEFAULT_MAX_UPLOAD_MB)
    except ValueError as exc:
        raise IbmF1UploadError(f"{MAX_UPLOAD_ENV} must be a number of megabytes.") from exc


def endpoint_from_env(environ: dict[str, str] | None = None) -> Endpoint:
    """Read where to send and what to send it with, from the environment only."""
    environ = os.environ if environ is None else environ
    token = (environ.get(TOKEN_ENV) or "").strip()
    if not token:
        raise IbmF1UploadError(
            f"No Coach upload key. Set {TOKEN_ENV} in the environment (or a local "
            ".env that is not committed) to the key the team issued for this "
            "workspace. It is never stored in this repository."
        )
    return Endpoint(
        origin=(environ.get(ORIGIN_ENV) or DEFAULT_ORIGIN).strip(),
        token=token,
        max_upload_bytes=int(_upload_limit(environ) * 1024 * 1024),
        review_origin=(environ.get(REVIEW_ORIGIN_ENV) or DEFAULT_REVIEW_ORIGIN).strip(),
    )


# Their installer carries this file, with the same key inside every copy, and
# their own deployment notes say in as many words that this token is not a
# secret -- unlike the import credential, which is a different header and is
# never read here. Reading their file is what lets the loop close on a machine
# nobody will ever set an environment variable on, which is every machine a
# participant sits at.
KIT_CONFIG_NAME = "coach-endpoint.json"
CONFIG_ENV = "IBMF1_ENDPOINT_FILE"


def kit_config_candidates() -> tuple[Path, ...]:
    """Where a shipped upload configuration may sit, most trusted first.

    The workspace leads so one machine can be pointed elsewhere without touching
    the install. Beside the executable comes next because that is where a frozen
    build's own copy travels, alongside the TORCS and Granite runtimes that are
    already found this way.
    """
    from f1coach_core.workspace import workspace_root

    roots = [workspace_root(), Path(sys.executable).resolve().parent]
    return tuple(root / KIT_CONFIG_NAME for root in roots)


def endpoint_from_kit(
    path: str | Path, environ: dict[str, str] | None = None
) -> Endpoint:
    """Build an endpoint from the configuration file their player kit ships.

    Read with their key names so the file a researcher was handed works
    unedited. Only the two origins and the token are taken: those are the three
    things this module models. Their `import_path` and `job_path` are not read,
    because the paths are constants here and reading them would create a file
    that looks configurable while half of it is ignored.
    """
    environ = os.environ if environ is None else environ
    path = Path(path)
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise IbmF1UploadError(f"Could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise IbmF1UploadError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise IbmF1UploadError(f"{path} does not hold a Coach endpoint object.")
    token = str(config.get("upload_token") or "").strip()
    if not token:
        raise IbmF1UploadError(
            f"{path} carries no upload_token. It is the key their installer ships; "
            f"set {TOKEN_ENV} instead if this machine was given one directly."
        )
    return Endpoint(
        origin=(environ.get(ORIGIN_ENV) or config.get("upload_origin") or DEFAULT_ORIGIN).strip(),
        token=token,
        max_upload_bytes=int(_upload_limit(environ) * 1024 * 1024),
        review_origin=(
            environ.get(REVIEW_ORIGIN_ENV) or config.get("coach_origin") or DEFAULT_REVIEW_ORIGIN
        ).strip(),
    )


def resolve_endpoint(
    environ: dict[str, str] | None = None,
    *,
    candidates: tuple[Path, ...] | None = None,
) -> Endpoint:
    """Where to send this bundle, from whichever source this machine has.

    An environment variable wins, because setting one is deliberate. A path
    named in `IBMF1_ENDPOINT_FILE` that does not resolve is an error rather than
    a fall through to the search: an explicit pointer that silently does nothing
    is worse than no pointer.
    """
    environ = os.environ if environ is None else environ
    if (environ.get(TOKEN_ENV) or "").strip():
        return endpoint_from_env(environ)
    named = (environ.get(CONFIG_ENV) or "").strip()
    if named:
        return endpoint_from_kit(named, environ)
    for candidate in kit_config_candidates() if candidates is None else candidates:
        if candidate.is_file():
            return endpoint_from_kit(candidate, environ)
    raise IbmF1UploadError(
        f"No Coach upload key. Either set {TOKEN_ENV} in the environment, or put "
        f"the {KIT_CONFIG_NAME} their installer ships next to the Apex executable "
        "or in the Apex workspace. Neither is stored in this repository."
    )


def _headers(endpoint: Endpoint, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "X-Player-Upload-Token": endpoint.token,
        "X-IBMF1-Ingest": "player-app",
        "Accept": "application/json",
        # Their CDN rejects urllib's default signature outright, which reaches
        # the client as a retryable-looking failure that never succeeds.
        "User-Agent": "IBMF1-Player",
    }
    if extra:
        headers.update(extra)
    return headers


def _request(
    url: str,
    *,
    headers: dict[str, str],
    method: str = "GET",
    body: bytes | None = None,
    timeout: int = 120,
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except urllib.error.URLError as error:
        raise _Unreachable(
            f"Could not reach the Coach server at {url}: {error.reason}"
        ) from error


def _payload(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _megabytes(count: int) -> str:
    return f"{count / (1024 * 1024):.1f} MB"


def submit(endpoint: Endpoint, archive: Path) -> dict:
    """POST one bundle. Returns the accepted job, or a note that it is already there."""
    body = archive.read_bytes()
    if len(body) > endpoint.max_upload_bytes:
        raise IbmF1UploadError(
            f"{archive.name} is {_megabytes(len(body))}, over the server's "
            f"{_megabytes(endpoint.max_upload_bytes)} limit. Re-pack it without the "
            "screen recording (racecoach export-ibmf1 --no-video); the telemetry is "
            "what the review is built from."
        )
    status, raw = _request(
        endpoint.import_url,
        method="POST",
        headers=_headers(endpoint, {
            "Content-Type": "application/zip",
            "Content-Length": str(len(body)),
        }),
        body=body,
        timeout=900,
    )
    payload = _payload(raw)
    if status == 202 and isinstance(payload.get("job_id"), str):
        return payload
    if status == 409 and payload.get("error") == "duplicate":
        return {"duplicate": True, "session": payload.get("session") or {}}
    if status == 409:
        raise _Busy(str(payload.get("message") or "The Coach server is busy."))
    if status == 401:
        raise IbmF1UploadError(
            f"The Coach server rejected the upload key. Check {TOKEN_ENV}."
        )
    if status == 413:
        raise IbmF1UploadError(
            f"The server refused {archive.name} as too large "
            f"({_megabytes(len(body))}). If the upload host is behind a CDN its own "
            f"body limit applies and is lower; set {MAX_UPLOAD_ENV} to match it."
        )
    raise IbmF1UploadError(
        str(payload.get("message") or payload.get("error") or f"Upload failed ({status}).")
    )


class _Busy(IbmF1UploadError):
    """The server is queueing; the same bundle can be offered again."""


class _Unreachable(IbmF1UploadError):
    """One request never reached the server, so the server said nothing.

    Distinct from every other failure here, all of which are the server's own
    answer. A caller that is waiting on work already running on their machine
    can retry this one; it is still an IbmF1UploadError, so a caller that does
    not care sees no change.
    """


def wait_for(
    endpoint: Endpoint,
    job_id: str,
    *,
    timeout_s: float = JOB_TIMEOUT_S,
    sleep=time.sleep,
    now=time.monotonic,
) -> dict:
    """Poll until the pipeline finishes, because until it does there is no review.

    A poll that never reaches them is not an answer about the job. The import is
    running on their machine and will finish whether or not this workspace can
    see it, so a lost request is retried rather than reported as a failed race.
    What is not tolerated is a silence long enough that nothing can be said
    honestly: after UNREACHABLE_LIMIT polls in a row this gives up and says the
    race may well be stored, rather than implying it is not.
    """
    started = now()
    unreachable = 0
    while now() - started <= timeout_s:
        try:
            status, raw = _request(
                f"{endpoint.job_url.rstrip('/')}/{job_id}",
                headers=_headers(endpoint),
                timeout=30,
            )
        except _Unreachable as blip:
            unreachable += 1
            if unreachable >= UNREACHABLE_LIMIT:
                raise IbmF1UploadError(
                    f"Lost contact with the Coach server for {UNREACHABLE_LIMIT} polls "
                    f"in a row while import job {job_id} was running. It may well have "
                    f"finished: check {endpoint.review_url} before sending the race "
                    "again."
                ) from blip
            sleep(UNREACHABLE_DELAY_S)
            continue
        unreachable = 0
        payload = _payload(raw)
        if status == 404:
            raise IbmF1UploadError(
                "The Coach server has no record of that import job. Check the site "
                "before sending the race again."
            )
        if status >= 400:
            raise IbmF1UploadError(
                str(payload.get("message") or f"Could not read import progress ({status}).")
            )
        state = str(payload.get("status") or "")
        if state == "complete":
            return payload
        if state == "error":
            raise IbmF1UploadError(
                str(payload.get("message") or "The Coach pipeline failed for this race.")
            )
        sleep(POLL_INTERVAL_S)
    raise IbmF1UploadError("Timed out waiting for the Coach review to finish.")


def deliver(
    archive: str | Path,
    *,
    endpoint: Endpoint | None = None,
    delays_s: tuple[int, ...] = BUSY_DELAYS_S,
    sleep=time.sleep,
) -> Delivered:
    """Send one bundle and wait until its review exists on the site."""
    archive = Path(archive)
    if not archive.is_file():
        raise IbmF1UploadError(f"No bundle to upload: {archive}")
    endpoint = endpoint or resolve_endpoint()
    last: _Busy | None = None
    for delay in (0, *delays_s):
        if delay:
            sleep(delay)
        try:
            accepted = submit(endpoint, archive)
        except _Busy as busy:
            last = busy
            continue
        if accepted.get("duplicate"):
            return Delivered(
                review_url=endpoint.review_url,
                duplicate=True,
                message="This race is already on the Coach site; nothing was sent twice.",
                session=accepted.get("session") or {},
            )
        finished = wait_for(endpoint, str(accepted["job_id"]))
        return Delivered(
            review_url=endpoint.review_url,
            duplicate=False,
            message=str(finished.get("message") or "The race is stored and ready to review."),
            session=finished.get("session") if isinstance(finished.get("session"), dict) else {},
        )
    raise IbmF1UploadError(
        f"The Coach server stayed busy through {len(delays_s)} retries: {last}"
    )
