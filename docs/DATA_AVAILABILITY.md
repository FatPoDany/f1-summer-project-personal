# TORCS Telemetry — Data Availability Report

*Task 1 deliverable — "Have a look and see what's possible, come back with a list."*
*University of Bristol MSc × IBM · 12 July 2026 · status: verified against sources below*

## 1. Scope and method

This report answers one question: **which driving signals can we actually capture from TORCS, from where, and with what caveats?** Every row is traced to a concrete source — a struct definition, a source line, or a team-produced field dictionary. Nothing is quoted from memory.

**Sources inspected**

| # | Source | What it establishes |
|---|--------|---------------------|
| S1 | `M_Lin/torcs_highfreq_field_cheatsheet.csv` (team artifact, 251 fields) | Field dictionary of the team's simuv2 exporter: name, unit, simuv2 struct source, meaning |
| S2 | `M_Lin/torcs_llm_coaching_pipeline_report_en.pdf` (team artifact) | Exporter output proven in practice: 5 runs, 83,980 samples @ ~50 Hz, 40 complete laps, built-in robot drivers (olethros, bt, inferno, tita, berniw) |
| S3 | `Torcs/torcs.app` bundle (TORCS 1.2.4 macOS port, PowerPC) | Ground truth for shipped robots (15 incl. `human`), tracks, and the `simuv2.so` physics module |
| S4 | Verified local TORCS 1.3.9 archive plus `integrations/torcs-1.3.9/overlay/src/drivers/granite_bridge/`; wire compatibility checked against [`fmirus/torcs-1.3.7`](https://github.com/fmirus/torcs-1.3.7) | Exact 1.3.9 robot ABI and tyre structs; standard SCR packet definitions |

**Two capture paths.** The project can obtain telemetry in two distinct ways; availability differs, so the table carries a column for each.

- **Path A — simuv2 exporter (current, proven).** A patched TORCS build writes one CSV row per physics step (stride 10 ≈ 50 Hz) with 249 raw columns, driven by built-in robots or the `human` driver. This is the path behind S1/S2. *Caveat: the exporter's patch source lives with the teammate who built it; we hold its full output schema (S1) but not the patch itself.*
- **Path B — Granite/SCR bridge + Python client (implemented).** Stock TORCS 1.3.9 contains no `scr_server`, so this repository adds a purpose-built non-blocking robot. It streams standard SCR-compatible telemetry around every 20 ms of simulator time, accepts bounded actuator commands from the deterministic Python driver, and appends native 1.3.9 tyre channels. The bridge ABI is compiled against the exact supplied archive by `verify-bridge.sh`.

## 2. Requested-field availability table

Fields the client and spec asked about, plus the candidate list. "A" = exporter path (S1), "B" = SCR path (S4).

| Field | A | B | Source (A) | Source (B) | Unit / range | Coaching use |
|---|---|---|---|---|---|---|
| speed | **yes** | **yes** | `total_speed_mps` ← `tCar.speed`; also body/world xyz components | `speedX/Y/Z` (km/h, ×3.6) — `scr_server.cpp:493-495` | m/s (A), km/h (B) | corner/straight speed |
| track position | **yes** | **yes** | `track_to_middle_m` ← `tTrkLocPos.toMiddle` (`track.h:434`), signed | `trackPos = 2·toMiddle/width` — `scr_server.cpp:388,497`; **±1 = track edge** | m (A), normalized (B) | racing line, off-track |
| angle (heading vs track) | **derivable** | **yes** | no direct column; recipe = track tangent − `yaw_rad`, exactly as `scr_server.cpp:390` computes it | `angle` — `scr_server.cpp:479` | rad | heading stability |
| steering | **yes** | **yes** (cmd) | `steer_cmd` ← `tCarCtrl.steer` **and** `steer_actual_rad` (post-physics) | actuator echo | −1..1 (`car.h:345`) | steering smoothness |
| throttle | **yes** | **yes** | `accel_cmd` ← `tCarCtrl.accelCmd` | actuator echo | 0..1 (`car.h:346`) | throttle strategy |
| brake | **yes** | **yes** | `brake_cmd` ← `tCarCtrl.brakeCmd`; plus per-wheel brake pressure/torque/temp | actuator echo | 0..1 (`car.h:347`) | braking points |
| lap time | **yes** | **yes** | `cur/last/best_lap_time_s` ← `tCarElt.race.*` (`car.h:142-146`) | `curLapTime`, `lastLapTime` — `scr_server.cpp:480,489` | s | overall performance |
| lap counter | **yes** | **yes** | `race_lap` ← `tCarElt.race.laps` | Granite Bridge `raceLap` ← `tCarElt.race.laps`; stock SCR fallback derives line crossings | lap | per-lap grouping and authoritative finish alignment |
| damage | **yes** | **yes** | `damage` ← `tCar.dammage` (`car.h:316`) + `collision`/`simcollision` bitmasks | `damage` — `scr_server.cpp:482-484` | count | crash detection |
| rpm | **yes** | **yes** | `engine_rpm` (derived from `tEngine.rads`) | `rpm` = `_enginerpm×10` (≈ rad/s→rpm) — `scr_server.cpp:492` | rpm | shift strategy |
| gear | **yes** | **yes** | `gear` (actual) **and** `gear_cmd` (requested) | `gear` — `scr_server.cpp:488` | −1..6 (`car.h:349`) | shift strategy |
| fuel | **yes** | **yes** | `fuel_l` ← `tPrivCar.fuel` (`car.h:297`) | `fuel` — `scr_server.cpp:487` | l | strategy context |
| distFromStart / distRaced | **yes** | **yes** | `dist_from_start_m`, `dist_raced_m` (`car.h:158-159`) | `scr_server.cpp:485-486` | m | distance axis for all analysis |
| race position | **yes** | **yes** | `race_pos` | `racePos` — `scr_server.cpp:491` | rank | context |
| clutch | **yes** | **yes** (cmd) | `clutch_cmd` + `clutch_transfer` | actuator echo | 0..1 (`car.h:348`) | launch/shift analysis |
| wheel spin | **yes** | **yes** | `{fr,fl,rr,rl}_spin_vel_rad_s` ← `tWheel.spinVel` | `wheelSpinVel[4]` — `scr_server.cpp:498` | rad/s | wheelspin / lock-up |
| tyre wear / temperature / pressure / graining | **partial** | **yes** | exporter has detailed tyre physics but not all four new 1.3.9 names | Granite Bridge reads `tPrivWheel.currentWear/currentTemperature/currentPressure/currentGraining` | ratio, °C, kPa, ratio | tyre preservation and strategy |
| z (height) | **yes** | **yes** | `pos_z_m` | `z` — `scr_server.cpp:499` | m | kerb/jump detection |
| off-track event | **derived** | **derived** | \|`track_to_middle_m`\| > `track_seg_width_m`/2 | \|`trackPos`\| > 1 | — | rule-based alert & event log |
| crash event | **derived** | **derived** | `damage` delta > 0 or `collision` bit set | `damage` delta | — | risk events |
| straight vs corner | **yes (ground truth)** | derivable | `track_seg_type`: 1 right / 2 left / 3 straight (`track.h:286-288`) | curvature from `track[]` | enum | section splitting without guessing |
| track rangefinder `track[19]` | **no** | **yes** | not exported | `scr_server.cpp:496` | m | (B-only) distance-to-edge beams |
| `opponents[36]` | **no** (direct) | **yes** | derivable across per-car rows when several cars run | `scr_server.cpp:490` | m | traffic awareness |
| `focus[5]` | no | **placeholder only** | — | TORCS 1.3.9 has no focus API; bridge returns five `-1` values | m | unavailable in this build |

## 3. Beyond the brief (Path A bonus fields)

The exporter captures physics no SCR client can see — useful for deeper coaching evidence and for the reliability story:

- **Tyre/grip:** per-wheel slip angle (`*_slip_angle_rad`), longitudinal slip, side-slip speed, friction coefficient `*_mu`
- **Loads:** per-wheel vertical force (`*_force_z_n`), suspension travel/force, front/rear downforce, drag
- **Driver-model truth:** `steer_actual_rad` vs `steer_cmd` (command vs achieved), brake torque & temperature per wheel
- **Track context:** segment id/name/geometry (length, width, radius, arc), surface friction & roughness
- **Race bookkeeping:** `top_speed_mps`, `current_min_speed_for_lap`, `remaining_laps`, `state` bitmask

Full catalogue: appendix A (251 fields).

## 4. Wanted but not available

| Missing | Path | Why | Workaround |
|---|---|---|---|
| `track[19]` rangefinder beams | A | exporter logs car/physics state, not virtual sensors | switch to Path B, or derive edge distances from `toMiddle` + segment geometry |
| `opponents[36]` proximity array | A | idem | join other cars' rows by `sim_time_s` (all cars are exported) |
| direct `angle` column | A | not in exporter schema | one-line derivation, same formula as `scr_server.cpp:390` |
| slip/force/suspension detail | B | SCR packet is fixed & minimal by design | Path A, or accept reduced evidence on B |
| exporter patch source | — | lives with the teammate who built it; only its output schema (S1) is in the vault | request the patch, or re-create: the cheatsheet names every struct member to read |
| real exporter CSVs | — | the 5 runs from S2 were not shared and are not recoverable | generate our own once a runnable TORCS exists (see §6); synthetic runs cover tests meanwhile |

## 5. Sampling, units, ranges

- **Cadence:** exporter stores every 10th physics step ≈ **50 Hz** (S2); the Granite Bridge robot callback is scheduled every **20 ms of simulator time** (about 50 Hz in real-time GUI mode). The bridge also transmits TORCS' `simTime` so the logger does not need to assume wall-clock cadence.
- **Units:** Path A is SI everywhere (S1 lists per-field units); Path B mixes km/h (speeds ×3.6, `scr_server.cpp:493`) and rad/m — a Path-B adapter must normalize to the canonical SI schema.
- **Ranges:** actuator ranges are normative from code comments (`car.h:345-349`): steer −1..1, accel/brake/clutch 0..1, gear −1..6. Sensor ranges (speeds, forces) are *empirical* and still need validation against a real run — blocked on §6, tracked as an open item.

## 6. Environment reality check

- The vault's `Torcs/torcs.app` is the **1.2.4 macOS PowerPC port** and cannot run on current Macs.
- The supplied Linux `torcs-1.3.9.tar.bz2` is valid and its bridge overlay now compiles against the exact 1.3.9 ABI. A full GUI simulator build on the checked Rocky 8 host is still waiting for administrator-provided Xxf86vm, OpenAL/ALUT, Vorbis, and related development packages; see `integrations/torcs-1.3.9/README.md`.
- The Python side has a lock-step UDP integration test, and the real IBM Granite 4.1 GGUF has passed the strict live-advice contract. A complete on-track run remains the final environment-dependent acceptance test after the native GUI dependencies are installed.

## 7. Appendix A — full exporter field catalogue (251 fields, from S1)

| Category | n | Fields |
|---|---|---|
| Sampling | 3 | `sample`, `sim_time_s`, `delta_time_s` |
| Identity | 4 | `car_index`, `car_name`, `run_id`, `source_file` |
| Race state | 8 | `state`, `race_lap`, `remaining_laps`, `race_pos`, `damage`, `fake_damage`, `collision`, `simcollision` |
| Race timing | 5 | `cur_lap_time_s`, `last_lap_time_s`, `best_lap_time_s`, `top_speed_mps`, `current_min_speed_mps` |
| Track position | 6 | `dist_from_start_m`, `dist_raced_m`, `track_to_start_m`, `track_to_right_m`, `track_to_middle_m`, `track_to_left_m` |
| Control | 8 | `steer_cmd`, `steer_actual_rad`, `accel_cmd`, `brake_cmd`, `clutch_cmd`, `gear_cmd`, `race_cmd`, `light_cmd` |
| Powertrain | 7 | `fuel_l`, `gear`, `engine_rpm_rad_s`, `engine_rpm`, `engine_torque_nm`, `engine_fuelcons`, `clutch_transfer` |
| Vehicle state | 3 | `pos_x_m`, `pos_y_m`, `pos_z_m` |
| Vehicle attitude | 10 | `roll_rad`, `pitch_rad`, `yaw_rad`, `yaw_rate_rad_s`, `ang_vel_{x,y,z}_rad_s`, `ang_accel_{x,y,z}` |
| Vehicle dynamics | 13 | `speed_body_{x,y,z}_mps`, `speed_world_{x,y,z}_mps`, `total_speed_mps`, `accel_body_{x,y,z}_mps2`, `accel_body_{x,y,z}_g` |
| Aero | 11 | `air_speed_mps`, `aero_drag_n`, `aero_lift_{front,rear}_n`, `wing_{front,rear}_{fx,fz}_n`, `{front,rear,total}_downforce_kg` |
| Track | 13 | `track_seg_{id,name,type,type2,race_info,length_m,width_m,radius_m,arc_rad}`, `track_surface_{friction,roll_res,roughness,damage}` |
| Vehicle setup | 4 | `mass_kg`, `fuel_tank_l`, `wheelbase_m`, `wheeltrack_m` |
| Wheel/tyre (×4: `fr, fl, rr, rl`) | 39 each = 156 | ride height, susp travel/velocity/force/state, road height, wheel pos xyz, body vel xyz, drive/spin torque, spin vel (+pre), state, axle Fz, slip angle, longitudinal slip, slip side/accel speed, steer angle, radius, mu, mass, camber, pressure, rel vel, brake pressure/torque/temp, force xyz, roll resistance, feedback spin vel/torque/brake torque |

*Prepared by the Apex/racecoach workstream. Verification scripts and citations reproducible from the sources table in §1.*
