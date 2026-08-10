"""Telemetry file contract — v1 DRAFT.

Status: proposed for the A0 spike; M2 confirms (or amends) in the week-one
Phase 3 meeting, after which this docstring is the contract.

A telemetry file is one lap: CSV (parquet later), one row per sample.

    # schema_version: 1
    t,dist,speed,throttle,brake,steer,gear,sector
    0.000,0.00,83.201,1.000,0.000,0.000,8,1
    ...

* Leading lines starting with ``#`` are metadata, formatted ``key: value``.
  ``schema_version`` is the only defined key so far; if absent, 1 is assumed.
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
"""

SCHEMA_VERSION = 1
SUPPORTED_VERSIONS = (1,)

REQUIRED_COLUMNS = ("t", "speed", "throttle", "brake", "steer", "gear")
OPTIONAL_COLUMNS = ("dist", "sector")
CANONICAL_ORDER = ("t", "dist", "speed", "throttle", "brake", "steer", "gear", "sector")
