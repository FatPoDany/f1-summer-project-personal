"""Granite 4.1 live contract, client, and asynchronous advisor tests."""

import json
import threading
from pathlib import Path

import pytest

from racecoach.granite.client import (
    GraniteClient,
    GraniteError,
    MockGraniteClient,
    TelemetrySnapshot,
    advice_from_dict,
)
from racecoach.granite.live import LiveGraniteAdvisor
from racecoach.telemetry import scr


def snapshot(**overrides) -> TelemetrySnapshot:
    values = {
        "tick": 500,
        "lap": 2,
        "sim_time_s": 10.0,
        "speed_kmh": 142.5,
        "track_position": 0.12,
        "heading_angle_rad": -0.03,
        "clear_road_ahead_m": 55.0,
        "fuel_l": 42.0,
        "damage": 0.0,
        "rpm": 6800.0,
        "gear": 4,
        "nearest_opponent_m": 31.0,
        "throttle_cmd": 0.0,
        "brake_cmd": 0.4,
        "steer_cmd": -0.1,
    }
    values.update(overrides)
    return TelemetrySnapshot(**values)


def valid_payload(snap: TelemetrySnapshot) -> dict:
    return {
        "message": "Brake progressively for the restricted road ahead.",
        "focus": "braking",
        "urgency": "soon",
        "evidence": [
            {
                "metric": "clear_road_ahead_m",
                "value": snap.clear_road_ahead_m,
                "unit": "m",
            }
        ],
    }


def test_snapshot_uses_scr_state_and_never_invents_unavailable_channels():
    state = {
        "speedX": 120,
        "trackPos": -0.25,
        "angle": 0.1,
        "track": [200.0] * 9 + [48.0] + [200.0] * 9,
        "opponents": [80.0, 35.0, 120.0],
        "fuel": 50,
        "damage": 2,
        "rpm": 7000,
        "gear": 4,
        "simTime": 12.3456,
    }
    snap = TelemetrySnapshot.from_scr(
        50,
        1,
        state,
        {"accel": 0.2, "brake": 0.0, "steer": 0.1},
    )
    assert snap.sim_time_s == 12.346 and snap.clear_road_ahead_m == 48.0
    assert snap.nearest_opponent_m == 35.0
    assert "tire_wear" not in snap.to_dict()
    assert not any(key.startswith("tire_") for key in snap.evidence_values())


def test_snapshot_exposes_stock_torcs_139_tire_channels_as_citable_evidence():
    state = {
        "speedX": 120,
        "track": [200.0] * 19,
        "tireWear": [0.1, 0.72, 0.2, 0.3],
        "tireTempC": [88.1, 102.3, 91.0, 93.4],
        "tirePressureKPa": [202.2, 205.1, 201.8, 203.0],
        "tireGraining": [0.01, 0.15, 0.03, 0.04],
    }
    snap = TelemetrySnapshot.from_scr(
        50,
        1,
        state,
        {"accel": 0.2, "brake": 0.0, "steer": 0.1},
    )
    evidence = snap.evidence_values()
    assert snap.tire_wear == (0.1, 0.72, 0.2, 0.3)
    assert evidence["tire_wear_front_left"] == (0.72, "ratio (0=new, 1=worn)")
    assert evidence["tire_temp_c_rear_left"] == (93.4, "degC")
    advice = MockGraniteClient().generate(snap).advice
    assert advice.focus == "safety" and advice.evidence[0].metric == "tire_wear_front_left"


def test_snapshot_rejects_non_finite_public_values_and_sanitizes_scr_actions():
    with pytest.raises(ValueError, match="speed_kmh must be finite"):
        snapshot(speed_kmh=float("nan"))
    with pytest.raises(ValueError, match="tire_wear"):
        snapshot(tire_wear=(0.1, 0.2, float("inf"), 0.4))

    snap = TelemetrySnapshot.from_scr(
        1,
        1,
        {"speedX": 50.0},
        {"accel": float("nan"), "brake": "bad", "steer": float("inf")},
    )
    assert (snap.throttle_cmd, snap.brake_cmd, snap.steer_cmd) == (0.0, 0.0, 0.0)


def test_openai_compatible_client_uses_granite_41_and_validates_json(monkeypatch):
    snap = snapshot()
    captured = {}

    def transport(url, payload, headers, timeout):
        captured.update(url=url, payload=payload, headers=headers, timeout=timeout)
        return {
            "model": "ibm-granite/granite-4.1-3b",
            "choices": [{"message": {"content": json.dumps(valid_payload(snap))}}],
        }

    result = GraniteClient(transport=transport, api_key="test-only", timeout_s=3).generate(snap)
    assert result.model == "ibm-granite/granite-4.1-3b"
    assert result.advice.focus == "braking"
    assert result.provenance["model_sha256"] is None
    assert result.provenance["provenance_source"] == "unverified"
    assert result.provenance["provenance_verified"] is False
    assert captured["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    assert captured["payload"]["temperature"] == 0.0
    response_format = captured["payload"]["response_format"]
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]["schema"]
    assert set(schema["required"]) == {"message", "focus", "urgency", "evidence"}
    assert "clear_road_ahead_m" in schema["properties"]["evidence"]["items"][
        "properties"
    ]["metric"]["enum"]
    assert captured["headers"]["Authorization"] == "Bearer test-only"
    assert "never emit steering" in captured["payload"]["messages"][0]["content"]

    monkeypatch.setenv("GRANITE_MODEL_SHA256", "declared-sha")
    declared = GraniteClient(transport=transport, timeout_s=3).generate(snap)
    assert declared.provenance["model_sha256"] == "declared-sha"
    assert declared.provenance["provenance_source"] == "environment-declared"
    assert declared.provenance["provenance_verified"] is False


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda item: item.update(metric="tyre_wear"), "unknown telemetry metric"),
        (lambda item: item.update(value=999), "snapshot contains"),
        (lambda item: item.update(unit="mph"), "expected 'm'"),
    ],
)
def test_contract_rejects_unsupported_or_fabricated_evidence(mutation, message):
    snap = snapshot()
    payload = valid_payload(snap)
    mutation(payload["evidence"][0])
    with pytest.raises(GraniteError, match=message):
        advice_from_dict(payload, snap)


def test_contract_rejects_numbers_hidden_in_model_prose():
    snap = snapshot()
    payload = valid_payload(snap)
    payload["message"] = "Brake at 120 metres."
    with pytest.raises(GraniteError, match="unvalidated numbers"):
        advice_from_dict(payload, snap)


def test_contract_rejects_extra_fields_and_duplicate_evidence():
    snap = snapshot()
    payload = valid_payload(snap)
    payload["brake"] = 1.0
    with pytest.raises(GraniteError, match="keys must be exactly"):
        advice_from_dict(payload, snap)

    payload = valid_payload(snap)
    payload["evidence"].append(dict(payload["evidence"][0]))
    with pytest.raises(GraniteError, match="more than once"):
        advice_from_dict(payload, snap)


def test_mock_backend_obeys_the_same_contract():
    result = MockGraniteClient().generate(snapshot(track_position=1.1))
    assert result.advice.focus == "position" and result.advice.urgency == "now"
    assert result.advice.evidence[0].metric == "track_position"


def test_live_advisor_writes_auditable_jsonl_without_blocking(tmp_path):
    delivered = threading.Event()
    advisor = LiveGraniteAdvisor(
        MockGraniteClient(),
        interval_s=0.02,
        on_advice=lambda snap, result: delivered.set(),
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    advisor.start(run_dir)
    advisor.observe(
        0,
        {
            "speedX": 100,
            "trackPos": 0,
            "track": [200.0] * 19,
            "fuel": 60,
            "rpm": 5000,
            "gear": 3,
        },
        {"accel": 0.5, "brake": 0, "steer": 0},
        1,
    )
    assert delivered.wait(2.0)

    assert advisor.audit_path is not None
    records = [json.loads(line) for line in advisor.audit_path.read_text("utf-8").splitlines()]
    assert records[0]["ok"] is True
    assert records[0]["model"] == "mock/granite-4.1-3b-contract"
    assert records[0]["snapshot"]["speed_kmh"] == 100.0
    assert records[0]["advice"]["evidence"]
    assert records[0]["published_for_display"] is True
    assert records[0]["display_suppressed_reason"] is None
    assert advisor.hud_advice == scr.sanitize_hud_advice(
        records[0]["advice"]["message"]
    )
    advisor.close()
    assert advisor.hud_advice is None


def test_model_failure_is_audited_and_does_not_escape_observe(tmp_path):
    class BrokenBackend:
        def generate(self, snap):
            raise GraniteError("model timeout")

    advisor = LiveGraniteAdvisor(BrokenBackend(), interval_s=0.02)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    advisor.start(run_dir)
    advisor.observe(0, {}, {"accel": 0, "brake": 0, "steer": 0}, 1)
    advisor.close()

    records = [json.loads(line) for line in advisor.audit_path.read_text("utf-8").splitlines()]
    assert records[0]["ok"] is False and "model timeout" in records[0]["error"]
    assert advisor.hud_advice is None


def test_live_advisor_reports_shutdown_timeout_and_audit_io_failure(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    class BlockingBackend:
        def generate(self, snap):
            started.set()
            release.wait(2.0)
            return MockGraniteClient().generate(snap)

    advisor = LiveGraniteAdvisor(
        BlockingBackend(), interval_s=0.02, shutdown_timeout_s=0.01
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    advisor.start(run_dir)
    advisor.observe(0, {}, {"accel": 0, "brake": 0, "steer": 0}, 1)
    assert started.wait(1.0)
    advisor.close()
    assert advisor.shutdown_timed_out is True
    release.set()
    advisor._thread.join(1.0)
    assert not advisor._thread.is_alive()

    broken = LiveGraniteAdvisor(MockGraniteClient())
    broken._audit_path = tmp_path / "audit.jsonl"
    original_open = Path.open

    def failing_open(path, *args, **kwargs):
        if path == broken.audit_path:
            raise OSError("disk unavailable")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)
    broken._append({"ok": False})
    assert broken.audit_error is not None and "disk unavailable" in broken.audit_error


def test_inflight_result_after_close_cannot_revive_callback_or_hud(tmp_path):
    started = threading.Event()
    release = threading.Event()
    delivered = []

    class LateBackend:
        def generate(self, snap):
            started.set()
            release.wait(2.0)
            return MockGraniteClient().generate(snap)

    advisor = LiveGraniteAdvisor(
        LateBackend(),
        interval_s=0.02,
        shutdown_timeout_s=0.01,
        on_advice=lambda snap, result: delivered.append((snap, result)),
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    advisor.start(run_dir)
    advisor.observe(0, {}, {"accel": 0, "brake": 0, "steer": 0}, 1)
    assert started.wait(1.0)

    advisor.close()
    assert advisor.shutdown_timed_out is True
    release.set()
    advisor._thread.join(1.0)

    assert delivered == []
    assert advisor.hud_advice is None


def test_hud_advice_expires_when_live_telemetry_moves_two_intervals_ahead(tmp_path):
    delivered = threading.Event()
    advisor = LiveGraniteAdvisor(
        MockGraniteClient(),
        interval_s=0.1,
        on_advice=lambda snap, result: delivered.set(),
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    advisor.start(run_dir)
    actions = {"accel": 0, "brake": 0, "steer": 0}

    advisor.observe(0, {}, actions, 1)
    assert delivered.wait(1.0)
    assert advisor.hud_advice is not None

    # 0.1 s / 0.02 s = five ticks; twice the interval remains fresh, then
    # the next simulator tick clears the display-only payload.
    advisor.observe(9, {}, actions, 1)
    assert advisor.hud_advice is not None
    advisor.observe(11, {}, actions, 1)
    assert advisor.hud_advice is None
    advisor.close()


def test_result_that_arrives_already_stale_is_audited_but_not_displayed(tmp_path):
    started = threading.Event()
    release = threading.Event()
    delivered = []

    class SlowBackend:
        def generate(self, snap):
            started.set()
            release.wait(2.0)
            return MockGraniteClient().generate(snap)

    advisor = LiveGraniteAdvisor(
        SlowBackend(),
        interval_s=0.1,
        on_advice=lambda snap, result: delivered.append((snap, result)),
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    advisor.start(run_dir)
    actions = {"accel": 0, "brake": 0, "steer": 0}
    advisor.observe(0, {}, actions, 1)
    assert started.wait(1.0)
    advisor.observe(11, {}, actions, 1)
    release.set()

    # Wait on the audit instead of the callback, which is intentionally suppressed.
    for _ in range(100):
        if advisor.audit_path.is_file() and advisor.audit_path.stat().st_size:
            break
        threading.Event().wait(0.01)
    assert advisor.audit_path.is_file() and advisor.audit_path.read_text("utf-8")
    record = json.loads(advisor.audit_path.read_text("utf-8").splitlines()[0])
    assert record["published_for_display"] is False
    assert record["display_suppressed_reason"] == "stale"
    assert record["latest_observed_tick"] == 11
    assert delivered == []
    assert advisor.hud_advice is None
    advisor.close()


@pytest.mark.parametrize("value", [-1.0, float("nan"), float("inf")])
def test_live_advisor_rejects_invalid_intervals(value):
    with pytest.raises(ValueError, match="positive and finite"):
        LiveGraniteAdvisor(MockGraniteClient(), interval_s=value)
