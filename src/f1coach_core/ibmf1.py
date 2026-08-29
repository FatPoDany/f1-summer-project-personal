"""Handing an Apex capture to the team's Coach server, without re-measuring anything.

The team's other half (IBMF1) ingests a race as a ZIP and runs its own rule
detection, evidence packets and review UI over it. Its importer accepts any CSV
whose header carries six columns, and an Apex human capture already carries all
six: both projects grew out of the same 249-column TORCS exporter vocabulary,
which is also why ``f1coach_core.torcs`` can read theirs.

So this is a translation, not a second measurement. Nothing here computes a
telemetry fact. Three kinds of work happen:

* four columns their pipeline reads under a different name or in a different
  unit are added from the ones the recorder actually wrote;
* ten columns their pipeline reads that this recorder cannot obtain are added
  empty, because their pipeline indexes them unguarded and a bundle without them
  dies at ``add_features`` -- see ``UNAVAILABLE_CHANNELS`` for why empty rather
  than absent, and why empty rather than zero;
* one ``session.json`` is written, in their schema, from what the capture's own
  manifest already recorded.

What is deliberately not sent: the capture manifest, whose paths name this
machine and its user account and whose digests describe the pre-translation
files; the handover manifest, for the same digest reason; and the exposure log,
which records what coaching a participant read and is this study's own
measurement rather than anything the Coach server can use.
"""

import hashlib
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from f1coach_core.participant import Background, load_background

# Their schema, not ours. Named here so a reader can find the other end:
# player_kit/tools/pack_session.py in the IBMF1 repository writes it.
SESSION_SCHEMA_VERSION = "ibmf1-player-session-v2"
SESSION_NAME = "session.json"
BACKGROUND_NAME = "participant.json"

# What their importer requires of a CSV before it will treat it as telemetry at
# all (deploy/coach_api/import_torcs_bundle.py, REQUIRED_TELEMETRY). An Apex
# human capture carries every one of them already; this is asserted rather than
# assumed, because a file that quietly fails the check is not rejected -- it is
# ignored, and a bundle can arrive with no telemetry in it.
REQUIRED_COLUMNS = (
    "sim_time_s",
    "race_lap",
    "speed_body_x_mps",
    "pos_x_m",
    "pos_y_m",
    "track_seg_type",
)

# Same channel, different name. Their exporter reads the width off the track
# segment; the Apex recorder reads it off the same segment through
# RtTrackGetWidth and calls it track_width_m.
RENAMED_COLUMNS = {"track_seg_width_m": "track_width_m"}

GRAVITY_MPS2 = 9.80665

# Same channel, different unit. SI is canonical on this side (see AGENTS.md), so
# the conversion happens here, at the protocol boundary, and nowhere else.
GRAVITY_COLUMNS = {f"accel_body_{axis}_g": f"accel_body_{axis}_mps2" for axis in "xyz"}

# Channels their pipeline indexes that no driver-side recorder can obtain.
#
# The Apex recorder lives inside the TORCS `human` driver and sees the published
# driver ABI: tCarElt and priv.wheel[] (tWheelState). Their exporter is a patch
# to simuv2 itself and reads the physics module's private structures -- slip
# angle is tWheel.sa, the aero figures come off tCar.aero, and neither is
# reachable from a driver. This is a boundary of the simulator, not an omission
# in the recorder.
#
# They are written as empty cells rather than left out because
# build_torcs_coaching_pipeline.py indexes them without a guard
# (`df[[f"{w}_slip_angle_rad" ...]]`), so a bundle without them fails the import
# outright. Empty is also the only honest value: pandas reads it as NaN and
# every statistic their pipeline computes over these columns skips it, so the
# detectors that depend on them produce nothing instead of producing something
# from a number nobody measured. Writing zeros would have invented a channel.
#
# The proper fix is upstream -- their pipeline guarding these the way it guards
# an absent video -- after which this list can shrink to nothing.
UNAVAILABLE_CHANNELS = (
    "fr_slip_angle_rad",
    "fl_slip_angle_rad",
    "rr_slip_angle_rad",
    "rl_slip_angle_rad",
    "fr_longitudinal_slip",
    "fl_longitudinal_slip",
    "rr_longitudinal_slip",
    "rl_longitudinal_slip",
    "total_downforce_kg",
    "aero_drag_n",
)

VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".mkv", ".avi"})
ARRIVING_ENCODING = "utf-8-sig"

# The one phase that may see coaching. Everything else -- control, baseline, and
# anything a future preset introduces -- is sent through as-is, which their
# server withholds coaching from, because it recognises only "coached" and
# refuses arms it does not know. Getting this wrong in the safe direction costs
# a participant a review they could have had; getting it wrong in the other
# direction unblinds them and silently ends the comparison.
COACHED_PHASE = "coached"
UNDECLARED_ARM = "unknown"


class IbmF1ExportError(RuntimeError):
    """A capture could not be translated into a bundle their server would accept."""


@dataclass(frozen=True)
class Bundle:
    """One capture, translated and packed for the Coach server."""

    path: Path
    participant_id: str
    study_arm: str
    runs: tuple[str, ...]
    focus_run_id: str
    rows: int
    has_video: bool

    @property
    def bytes(self) -> int:
        return self.path.stat().st_size


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            sha.update(chunk)
    return sha.hexdigest()


def to_ibmf1_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Add the columns their pipeline reads, leaving every measured value alone.

    Existing columns are never overwritten: if a future recorder starts writing
    one of these itself, the measured column wins over the translated one.
    """
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise IbmF1ExportError(
            "This telemetry is not a TORCS capture their importer can read: "
            f"missing {', '.join(missing)}."
        )
    added: dict[str, object] = {}
    for target, source in RENAMED_COLUMNS.items():
        if target not in frame.columns:
            if source not in frame.columns:
                raise IbmF1ExportError(
                    f"Cannot supply {target}: this capture has neither it nor {source}."
                )
            added[target] = frame[source]
    for target, source in GRAVITY_COLUMNS.items():
        if target not in frame.columns:
            if source not in frame.columns:
                raise IbmF1ExportError(
                    f"Cannot supply {target}: this capture has neither it nor {source}."
                )
            added[target] = frame[source] / GRAVITY_MPS2
    for column in UNAVAILABLE_CHANNELS:
        if column not in frame.columns:
            added[column] = pd.NA
    if not added:
        return frame
    return pd.concat([frame, pd.DataFrame(added, index=frame.index)], axis=1)


def study_arm_for(phase: str | None) -> str:
    """Which study group to declare, from the phase the capture recorded.

    Only a coached phase is declared coachable. A baseline run is driven before
    any coaching exists for that participant and a control run must never see
    any, so both go over under their own name, which their server does not
    recognise and therefore withholds coaching from. A capture that recorded no
    phase declares an arm explicitly rather than declaring none: an absent arm
    is the one value their server reads as "coaching is fine".
    """
    cleaned = (phase or "").strip().casefold()
    if not cleaned:
        return UNDECLARED_ARM
    return cleaned


def _capture_manifest(capture_dir: Path) -> dict:
    try:
        data = json.loads(
            (capture_dir / "manifest.json").read_text(encoding=ARRIVING_ENCODING)
        )
    except (OSError, ValueError) as exc:
        raise IbmF1ExportError(
            f"{capture_dir} has no readable manifest.json, so nothing can say whose "
            "race this is or which phase it belongs to."
        ) from exc
    if not isinstance(data, dict):
        raise IbmF1ExportError(f"{capture_dir}/manifest.json is not a JSON object.")
    return data


def _declared_runs(manifest: dict) -> list[str]:
    """The CSVs the capture itself recorded as completed runs."""
    runs = manifest.get("runs")
    if not isinstance(runs, list):
        return []
    return [
        str(entry["file"])
        for entry in runs
        if isinstance(entry, dict) and entry.get("file")
    ]


def _telemetry_files(capture_dir: Path, manifest: dict) -> list[Path]:
    """Which CSVs to send, preferring what the capture said it completed.

    A capture folder routinely holds an export for a race that was started and
    abandoned: a file with a header and no rows. Their importer recognises it as
    a second run purely from its header, and it would arrive on the website as a
    race nobody drove.
    """
    declared = _declared_runs(manifest)
    candidates = [capture_dir / name for name in declared if (capture_dir / name).is_file()]
    if not candidates:
        candidates = sorted(capture_dir.glob("*.csv"))
    keep: list[Path] = []
    for path in candidates:
        try:
            header = pd.read_csv(path, nrows=0, encoding=ARRIVING_ENCODING)
        except (OSError, ValueError, pd.errors.ParserError):
            continue
        if not set(REQUIRED_COLUMNS).issubset(header.columns):
            continue
        if _row_count(path) == 0:
            continue
        keep.append(path)
    if not keep:
        raise IbmF1ExportError(
            f"{capture_dir} holds no completed telemetry their importer would accept. "
            "A capture whose race was abandoned before the first sample has nothing "
            "to review."
        )
    return keep


def _background_for(capture_dir: Path, manifest: dict) -> Background | None:
    """The questionnaire, from the folder if it travelled there, else the workspace.

    Both places are real. On the machine that drove the race it is in the
    workspace, keyed by participant; in a handover somebody unpacked it is
    sitting in the folder, and that machine may never have adopted it. Reading
    only the workspace would silently drop it in the second case, which is the
    one loss a handover cannot recover from.
    """
    local = capture_dir / BACKGROUND_NAME
    if local.is_file():
        try:
            data = json.loads(local.read_text(encoding=ARRIVING_ENCODING))
            if isinstance(data, dict):
                return Background.from_dict(data)
        except (OSError, ValueError, TypeError):
            pass  # fall through to the workspace rather than fail the export
    participant = str(manifest.get("participant_id") or "")
    background = load_background(participant) if participant else None
    return background if isinstance(background, Background) else None


def _row_count(path: Path) -> int:
    with path.open("r", encoding=ARRIVING_ENCODING, newline="") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def build_session(
    manifest: dict,
    *,
    runs: dict[str, dict],
    focus_run_id: str,
    telemetry_name: str,
    telemetry_sha256: str,
    source_sha256: str | None,
    rows: int,
    video_file: str | None,
    session_number: int | None = None,
) -> dict:
    """The ``session.json`` their importer reads, from what the capture recorded.

    Their site derives a player's session ordering from the recorded time in the
    bundle, so a number is sent only when a caller knows one. Inventing a
    sequence here would order somebody's races by when they happened to be
    exported.
    """
    participant = str(manifest.get("participant_id") or "").strip()
    if not participant:
        raise IbmF1ExportError(
            "This capture records no participant id, so the site would file it "
            "under an anonymous player with everybody else's."
        )
    phase = manifest.get("phase")
    preset = manifest.get("study_preset") if isinstance(manifest.get("study_preset"), dict) else {}
    exported = datetime.now(UTC).isoformat(timespec="seconds")
    session = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "session_id": f"{participant}-{manifest.get('started_at') or exported}",
        # A pseudonym in both fields. The site shows display_name publicly, and
        # nothing here may ever carry a real name.
        "player_id": participant,
        "display_name": participant,
        "study_arm": study_arm_for(phase),
        "attempt_label": str(phase).strip() if phase else UNDECLARED_ARM,
        "recorded_at": manifest.get("started_at"),
        "started_at": manifest.get("started_at"),
        "finished_at": manifest.get("finished_at"),
        "exported_at": exported,
        "run_id": focus_run_id,
        "focus_run_id": focus_run_id,
        "controller": "human",
        "track": preset.get("track_id"),
        "vehicle": preset.get("car_id"),
        "sample_rate_hz": 50,
        "telemetry_file": telemetry_name,
        "telemetry_files": sorted(runs),
        "telemetry_sha256": telemetry_sha256,
        "row_count": rows,
        "runs": runs,
        "video_file": video_file,
        "media": "screen_capture" if video_file else "telemetry_only",
        "coach_ingest": True,
        # Keys below are Apex's own; their importer reads a fixed field list and
        # ignores the rest. They exist so a stored race can be tied back to the
        # file this workspace measured, which is not the file that was sent.
        "apex_source_sha256": source_sha256,
        "apex_added_columns": sorted(
            [*RENAMED_COLUMNS, *GRAVITY_COLUMNS, *UNAVAILABLE_CHANNELS]
        ),
        "apex_empty_columns": sorted(UNAVAILABLE_CHANNELS),
    }
    if session_number is not None:
        session["session_number"] = session_number
        session["attempt"] = session_number
    return session


def package(
    capture_dir: str | Path,
    destination: str | Path,
    *,
    session_number: int | None = None,
    include_background: bool = True,
    include_video: bool = True,
) -> Bundle:
    """Translate one capture folder into a ZIP their Coach server will accept.

    The participant's questionnaire travels with it by default. It is
    pseudonymous and coarse by construction (see f1coach_core.participant), and
    their importer stores it as research metadata rather than publishing it;
    ``include_background=False`` is there for a deployment whose consent does not
    cover sending it.

    The screen recording is most of the bytes and the only part their server can
    do without: a bundle with no video imports as a telemetry-only race, with
    generated track keyframes in place of real ones. ``include_video=False`` is
    how an upload gets under a body limit without dropping any measurement.
    """
    capture_dir = Path(capture_dir)
    if not capture_dir.is_dir():
        raise IbmF1ExportError(f"Not a capture folder: {capture_dir}")
    manifest = _capture_manifest(capture_dir)
    sources = _telemetry_files(capture_dir, manifest)

    videos = sorted(
        path
        for path in capture_dir.glob("*")
        if include_video and path.suffix.lower() in VIDEO_SUFFIXES
    )
    if len(videos) > 1:
        raise IbmF1ExportError(
            f"{capture_dir} holds {len(videos)} recordings; their importer accepts "
            "at most one per race."
        )

    # (source, member name, rows, digest of the bytes actually sent)
    translated: list[tuple[Path, str, int, str]] = []
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for source in sources:
            frame = pd.read_csv(source, encoding=ARRIVING_ENCODING)
            payload = to_ibmf1_columns(frame).to_csv(
                index=False, lineterminator="\n"
            ).encode("utf-8")
            archive.writestr(source.name, payload)
            translated.append(
                (source, source.name, len(frame), hashlib.sha256(payload).hexdigest())
            )

        focus_source, focus_name, focus_rows, sent_digest = max(
            translated, key=lambda item: item[2]
        )
        runs = {
            Path(name).stem: {
                "csv_file": name,
                "controller": "human",
                "display_name": str(manifest.get("participant_id") or ""),
                "is_focus": name == focus_name,
                "car_index": 0,
            }
            for _source, name, _rows, _sha in translated
        }

        video_name = videos[0].name if videos else None
        if video_name:
            archive.write(videos[0], video_name)

        if include_background:
            background = _background_for(capture_dir, manifest)
            if background is not None:
                archive.writestr(
                    BACKGROUND_NAME,
                    json.dumps(background.to_dict(), indent=2, ensure_ascii=False),
                )

        session = build_session(
            manifest,
            runs=runs,
            focus_run_id=Path(focus_name).stem,
            telemetry_name=focus_name,
            telemetry_sha256=sent_digest,
            source_sha256=_digest(focus_source),
            rows=focus_rows,
            video_file=video_name,
            session_number=session_number,
        )
        archive.writestr(
            SESSION_NAME, json.dumps(session, indent=2, ensure_ascii=False) + "\n"
        )

    return Bundle(
        path=destination,
        participant_id=session["player_id"],
        study_arm=session["study_arm"],
        runs=tuple(sorted(runs)),
        focus_run_id=session["focus_run_id"],
        rows=focus_rows,
        has_video=video_name is not None,
    )
