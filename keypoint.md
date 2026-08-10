#
A1 (chart core) is done and committed — the app now has the full Garage → Lap Analysis flow, and I folded Lin's research in as a working TORCS import path. Two commits on the repo: 23164df (A0) and 52f1fcf (A1), with lint clean and 27/27 tests passing. The screenshots above show both screens live: the Garage with the lap table (best lap in purple, deltas against it) and the Analysis screen with the sector ribbon and synced speed/throttle/brake strips plus the reference lap overlaid in purple.

What Lin's material changed. The report describes a simuv2 exporter producing ~50 Hz, 249-column CSVs where one file is a whole outing (grid start, several laps, a cut-off fragment) — not one lap per file, and with no sector channel. So f1coach_core got a torcs.py adapter: it detects the format from the column signature, splits the run wherever dist_from_start_m snaps back to zero, maps the fields onto our canonical schema (sim_time_s→t, total_speed_mps→speed, accel_cmd→throttle, …), derives sectors as track-length thirds, and writes each complete lap as a canonical CSV with provenance comments (source file, car, lap number). Dropping a TORCS run onto the window imports it as a session in the Garage; incomplete fragments are skipped and reported. One caveat: the folder has the report and cheatsheet but no actual run CSV, so the adapter is validated against synthetic runs built from the cheatsheet's column names — when you get a real export from Lin, drop it on the app and I'll fix whatever surprises surface.

Beyond the adapter, the A1 build added: a workspace store at ~/Apex/sessions (relocatable via APEX_WORKSPACE), session loading that reports unreadable files as table rows instead of crashing, sector timing colours on the ribbon (purple = session-best sector, green/yellow = faster/slower than the reference), a live crosshair readout (distance, speed, throttle, brake, gear), watched-folder auto-import, and an explicit Carbon dark palette so the app is pit-wall dark on every platform. The sample session now has three laps with a coaching story (banker +1.66 s, best, ragged +5.32 s) — that's the raw material A2's mock coach will explain.

To see it yourself: pip install -e ".[dev]" again isn't needed — just run apex (Garage opens on the sample session, double-click a lap). Next milestone is A2, the coach skeleton (gate: 4 Aug): the coaching JSON contract, the mock Granite provider, the AI Race Engineer panel, and evidence-zoom. Say the word and I'll start on it; the other loose end you own is pushing the repo to GitHub so the ubuntu+macos CI (which now uploads both screenshots per OS) starts running.

#
From a terminal:

    cd ~/Desktop/IBM/apex
    source .venv/bin/activate   # the venv you created earlier
    apex

That's it — the window opens in the Garage on the bundled sample session; double-click a lap row (e.g. lap_02) to get the Lap Analysis screen with the strips and sector ribbon. apex path/to/lap.csv opens a lap directly, and dropping any CSV (including a TORCS run export) onto the window works too.

If you're in a fresh terminal without the venv active, the one-liner is:

~/Desktop/IBM/apex/.venv/bin/apex

There's no double-clickable Apex.app yet — packaged builds (PyInstaller) are scheduled for milestone A3.