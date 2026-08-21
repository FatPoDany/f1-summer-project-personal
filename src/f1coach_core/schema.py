"""Telemetry file contract — v1 DRAFT.

Status: proposed for the A0 spike; M2 confirms (or amends) in the week-one
Phase 3 meeting, after which this docstring is the contract.

A telemetry file is one lap: CSV (parquet later), one row per sample.

    # schema_version: 1
    t,dist,speed,throttle,brake,steer,gear,sector
    0.000,0.00,83.201,1.000,0.000,0.000,8,1
    ...

* Leading lines starting with ``#`` are metadata, formatted ``key: value``.
  ``schema_version`` is assumed to be 1 when absent. ``lap`` carries the race
  lap number, and ``driver``/``phase``/``setup`` the study identity, so a lap
  handed to a researcher still states where it came from. All are optional.
* Units are SI in the file; front ends convert for display (km/h etc.).
* Unknown extra columns are preserved, not rejected (forward compatibility).

Column    Unit    Notes
------    ----    -----
t         s       monotonically non-decreasing, starts near 0
dist      m       from the start line; OPTIONAL — when the sim can't export
                  it, the loader derives cumsum(speed * dt)
speed     m/s
throttle  0..1
brake     0..1
steer     -1..1   negative = left
gear      int     -1 reverse, 0 neutral
sector    1|2|3   OPTIONAL until M2's exporter lands
x, y      m       world position; OPTIONAL, present together or not at all.
                  Distance alone cannot draw a track, so the replay needs
                  these; laps from a source without a position channel stay
                  fully analysable and simply cannot be mapped.
track_pos --      lateral position, 0 at the centre line and +-1 at the track
                  edges, so |track_pos| > 1 means the car is off the track.
                  OPTIONAL. Carried because a comparative study is judged on
                  more than lap time: leaving the track is one of the objective
                  measures of whether coaching changed how somebody drove.
damage    --      cumulative damage, monotonically non-decreasing within a lap.
                  OPTIONAL. Increases mark incidents; the level itself is only
                  meaningful relative to where the lap started.
"""

SCHEMA_VERSION = 1
SUPPORTED_VERSIONS = (1,)

REQUIRED_COLUMNS = ("t", "speed", "throttle", "brake", "steer", "gear")
OPTIONAL_COLUMNS = ("dist", "sector", "x", "y", "track_pos", "damage")
CANONICAL_ORDER = ("t", "dist", "speed", "throttle", "brake", "steer", "gear", "sector")
