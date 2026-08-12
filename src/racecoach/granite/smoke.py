"""One real request that verifies the Granite server and strict advice contract."""

from __future__ import annotations

import argparse
import json
import sys

from racecoach.granite.client import (
    GraniteClient,
    GraniteError,
    MockGraniteClient,
    TelemetrySnapshot,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mock", action="store_true", help="validate without a model server")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible /v1 endpoint")
    parser.add_argument("--model", default=None, help="served Granite model alias")
    args = parser.parse_args(argv)

    snapshot = TelemetrySnapshot(
        tick=500,
        lap=2,
        sim_time_s=10.0,
        speed_kmh=142.5,
        track_position=0.12,
        heading_angle_rad=-0.03,
        clear_road_ahead_m=55.0,
        fuel_l=42.0,
        damage=0.0,
        rpm=6800.0,
        gear=4,
        nearest_opponent_m=31.0,
        throttle_cmd=0.0,
        brake_cmd=0.4,
        steer_cmd=-0.1,
        tire_wear=(0.18, 0.21, 0.73, 0.35),
        tire_temp_c=(91.0, 92.5, 104.0, 96.0),
        tire_pressure_kpa=(202.0, 202.5, 207.0, 204.0),
        tire_graining=(0.02, 0.03, 0.14, 0.05),
    )
    backend = (
        MockGraniteClient()
        if args.mock
        else GraniteClient(base_url=args.base_url, model=args.model)
    )
    try:
        result = backend.generate(snapshot)
    except GraniteError as exc:
        print(f"granite smoke: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "model": result.model,
                "prompt_version": result.prompt_version,
                "provenance": result.provenance,
                "advice": result.advice.to_dict(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
