"""Sending a translated bundle to the team's Coach server, and waiting for it.

The server takes the whole ZIP in one POST, answers with a job id, and runs its
pipeline asynchronously; the race is not reviewable until that job finishes. So
an upload that returns without polling has told the participant nothing useful.

No credential lives in this repository. The token is read from the environment,
and a missing one is an error with instructions rather than a silent
unauthenticated attempt that comes back 401.
"""

import json
import os
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

DEFAULT_MAX_UPLOAD_MB = 300  # nginx's limit on that host
BUSY_DELAYS_S = (4, 8, 16, 32)
POLL_INTERVAL_S = 2.0
JOB_TIMEOUT_S = 15 * 60

TOKEN_ENV = "IBMF1_UPLOAD_TOKEN"
ORIGIN_ENV = "IBMF1_UPLOAD_ORIGIN"
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

    @property
    def import_url(self) -> str:
        return f"{self.origin.rstrip('/')}{IMPORT_PATH}"

    @property
    def job_url(self) -> str:
        return f"{self.origin.rstrip('/')}{JOB_PATH}"


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
    try:
        limit_mb = float(environ.get(MAX_UPLOAD_ENV) or DEFAULT_MAX_UPLOAD_MB)
    except ValueError as exc:
        raise IbmF1UploadError(f"{MAX_UPLOAD_ENV} must be a number of megabytes.") from exc
    return Endpoint(
        origin=(environ.get(ORIGIN_ENV) or DEFAULT_ORIGIN).strip(),
        token=token,
        max_upload_bytes=int(limit_mb * 1024 * 1024),
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
        raise IbmF1UploadError(
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


def wait_for(
    endpoint: Endpoint,
    job_id: str,
    *,
    timeout_s: float = JOB_TIMEOUT_S,
    sleep=time.sleep,
    now=time.monotonic,
) -> dict:
    """Poll until the pipeline finishes, because until it does there is no review."""
    started = now()
    while now() - started <= timeout_s:
        status, raw = _request(
            f"{endpoint.job_url.rstrip('/')}/{job_id}", headers=_headers(endpoint), timeout=30
        )
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
    endpoint = endpoint or endpoint_from_env()
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
                review_url=endpoint.origin,
                duplicate=True,
                message="This race is already on the Coach site; nothing was sent twice.",
                session=accepted.get("session") or {},
            )
        finished = wait_for(endpoint, str(accepted["job_id"]))
        return Delivered(
            review_url=endpoint.origin,
            duplicate=False,
            message=str(finished.get("message") or "The race is stored and ready to review."),
            session=finished.get("session") if isinstance(finished.get("session"), dict) else {},
        )
    raise IbmF1UploadError(
        f"The Coach server stayed busy through {len(delays_s)} retries: {last}"
    )
