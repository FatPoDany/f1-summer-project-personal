"""One run -> one HTML file: meta header, lap table, best-lap channel charts
with event bands, section table, event log, and (when present) the validated
five-section feedback. Everything shown is read back from the same artifacts
the pipeline persisted (metrics.json / feedback.json) — the report renders
evidence, it never recomputes it."""

import html
import json
from pathlib import Path

import numpy as np

from f1coach_core.report import _polyline  # same-family reuse, like audit's _unique_dest
from racecoach.analysis import channels as ch
from racecoach.analysis.metrics import METRICS_NAME, analyze_run
from racecoach.feedback.engine import FEEDBACK_NAME
from racecoach.telemetry.run_store import LoadedRun, load_run

REPORT_NAME = "report.html"

BG, PANEL, TEXT, DIM = "#161616", "#1f1f1f", "#f4f4f4", "#c6c6c6"
BLUE, PURPLE, GREEN, YELLOW, RED = "#78a9ff", "#be95ff", "#42be65", "#f1c21b", "#fa4d56"
EVENT_COLORS = {
    "off_track": YELLOW,
    "collision": RED,
    "pedal_overlap": PURPLE,
    "steering_jerk": BLUE,
    "wheel_lockup": RED,
}
SECTION_COLORS = {"straight": "#393939", "corner_left": PURPLE, "corner_right": BLUE}

_CSS = f"""
body {{ background: {BG}; color: {TEXT}; margin: 2rem auto; max-width: 62rem;
       font: 14px/1.5 -apple-system, 'IBM Plex Sans', 'Segoe UI', sans-serif; }}
h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.05rem; margin-top: 2rem; }}
.dim {{ color: {DIM}; }}
table {{ border-collapse: collapse; margin: .5rem 0; }}
td, th {{ border: 1px solid #393939; padding: .3rem .7rem; text-align: right; }}
td:first-child, th:first-child {{ text-align: left; }}
th {{ color: {DIM}; font-weight: 500; }}
.card {{ background: {PANEL}; border: 1px solid #393939; border-radius: 6px;
        padding: .8rem 1rem; margin: .8rem 0; }}
.chip {{ background: #262626; border-radius: 4px; padding: .1rem .5rem;
        font-size: .8rem; color: {DIM}; }}
svg {{ background: {PANEL}; border-radius: 6px; display: block; margin: .5rem 0;
      max-width: 100%; height: auto; }}
.legend span {{ margin-right: 1rem; font-size: .85rem; }}
"""

CHART_W, CHART_H, PAD = 860, 170, 8


def report_run(run_id: str) -> Path:
    """Render runs/<id>/report.html from the persisted artifacts."""
    run = load_run(run_id)
    metrics_path = run.path / METRICS_NAME
    if not metrics_path.is_file():
        analyze_run(run_id)
    metrics = json.loads(metrics_path.read_text("utf-8"))
    feedback_path = run.path / FEEDBACK_NAME
    feedback = (
        json.loads(feedback_path.read_text("utf-8")) if feedback_path.is_file() else None
    )
    destination = run.path / REPORT_NAME
    destination.write_text(render_run_report(run, metrics, feedback), encoding="utf-8")
    return destination


def render_run_report(run: LoadedRun, metrics: dict, feedback: dict | None) -> str:
    parts = [
        f"<style>{_CSS}</style>",
        _header(metrics),
        _lap_table(metrics),
        _charts(run, metrics),
        _section_table(metrics),
        _event_table(metrics),
        _feedback_block(feedback),
        _notes(metrics),
    ]
    title = f"Apex run report — {metrics['run']['run_id']}"
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title></head><body>"
        + "".join(parts)
        + "</body></html>"
    )


def _header(metrics: dict) -> str:
    run = metrics["run"]
    cadence = f"{run['cadence_hz']} Hz" if run["cadence_hz"] else "cadence unknown"
    return (
        f"<h1>Run report — {html.escape(run['run_id'])}</h1>"
        f"<div class='dim'>source {html.escape(run['source_file'])} · imported "
        f"{html.escape(run['imported_at'])} · {run['n_samples']} samples · {cadence} · "
        f"track {metrics['track']['length_m']:.0f} m · cars: "
        f"{html.escape(', '.join(run['car_names']) or '?')}</div>"
    )


def _lap_table(metrics: dict) -> str:
    rows = ""
    for lap in metrics["laps"]:
        time = f"{lap['lap_time_s']:.3f} s" if lap["lap_time_s"] else "—"
        top = f"{lap.get('top_speed_kmh', 0):.1f}" if "top_speed_kmh" in lap else "—"
        mean = f"{lap.get('mean_speed_kmh', 0):.1f}" if "mean_speed_kmh" in lap else "—"
        events = sum(1 for event in metrics["events"] if event["lap"] == lap["lap"])
        rows += (
            f"<tr><td>lap {lap['lap']}</td><td>{time}</td><td>{top}</td>"
            f"<td>{mean}</td><td>{lap['distance_covered_m']:.0f}</td>"
            f"<td>{'✓' if lap['complete'] else 'partial'}</td><td>{events}</td></tr>"
        )
    return (
        "<h2>Laps</h2><table><tr><th>Lap</th><th>Time</th><th>Top km/h</th>"
        "<th>Mean km/h</th><th>Distance m</th><th>Complete</th><th>Events</th></tr>"
        + rows + "</table>"
    )


def _best_lap(metrics: dict) -> int | None:
    timed = [lap for lap in metrics["laps"] if lap.get("lap_time_s") and lap["complete"]]
    if not timed:
        timed = [lap for lap in metrics["laps"] if lap.get("lap_time_s")]
    return min(timed, key=lambda lap: lap["lap_time_s"])["lap"] if timed else None


def _charts(run: LoadedRun, metrics: dict) -> str:
    best = _best_lap(metrics)
    if best is None or ch.missing(run.df, ch.DIST):
        return "<h2>Charts</h2><div class='dim'>Not enough data to chart.</div>"
    laps = ch.lap_numbers(run.df)
    on_lap = laps == best
    dist = ch.numeric(run.df, ch.DIST)[on_lap]
    order = np.argsort(dist)
    dist = dist[order]
    x_range = (float(dist[0]), float(dist[-1]))

    channels = [
        ("speed (km/h)", "total_speed_mps", 3.6, BLUE),
        ("throttle (%)", "accel_cmd", 100.0, GREEN),
        ("brake (%)", "brake_cmd", 100.0, RED),
        ("steer (-1..1)", "steer_cmd", 1.0, YELLOW),
    ]
    bands = _event_bands(metrics, best, x_range)
    ribbon = _section_ribbon(metrics, x_range)
    charts = [f"<h2>Best lap channels — lap {best}</h2>", ribbon]
    for title, column, scale, colour in channels:
        if column not in run.df.columns:
            charts.append(f"<div class='dim'>{title}: channel not captured.</div>")
            continue
        values = ch.numeric(run.df, column)[on_lap][order] * scale
        lo = min(0.0, float(np.nanmin(values)))
        hi = max(float(np.nanmax(values)), lo + 1e-6)
        body = bands + _polyline(dist, values, x_range, (lo, hi), CHART_W, CHART_H, colour)
        charts.append(
            f"<svg viewBox='0 0 {CHART_W} {CHART_H}' width='{CHART_W}' height='{CHART_H}'"
            f" xmlns='http://www.w3.org/2000/svg'>{body}</svg>"
            f"<div class='dim'>{html.escape(title)} over distance (m); shaded bands are "
            "detected events</div>"
        )
    legend = "".join(
        f"<span style='color:{colour}'>■ {kind.replace('_', ' ')}</span>"
        for kind, colour in EVENT_COLORS.items()
    )
    charts.append(f"<div class='legend'>{legend}</div>")
    return "".join(charts)


def _event_bands(metrics: dict, lap: int, x_range: tuple[float, float]) -> str:
    x0, x1 = x_range
    span = (x1 - x0) or 1.0
    rects = []
    for event in metrics["events"]:
        if event["lap"] != lap:
            continue
        colour = EVENT_COLORS.get(event["kind"], DIM)
        left = PAD + (event["dist_start_m"] - x0) / span * (CHART_W - 2 * PAD)
        right = PAD + (event["dist_end_m"] - x0) / span * (CHART_W - 2 * PAD)
        width = max(right - left, 4.0)
        rects.append(
            f"<rect x='{left:.1f}' y='0' width='{width:.1f}' height='{CHART_H}'"
            f" fill='{colour}' opacity='0.18'><title>{html.escape(event['event_id'])}: "
            f"{html.escape(event['summary'])}</title></rect>"
        )
    return "".join(rects)


def _section_ribbon(metrics: dict, x_range: tuple[float, float]) -> str:
    x0, x1 = x_range
    span = (x1 - x0) or 1.0
    rects = []
    for section in metrics["sections"]:
        colour = SECTION_COLORS.get(section["kind"], DIM)
        left = PAD + (section["dist_start_m"] - x0) / span * (CHART_W - 2 * PAD)
        right = PAD + (section["dist_end_m"] - x0) / span * (CHART_W - 2 * PAD)
        rects.append(
            f"<rect x='{left:.1f}' y='2' width='{max(right - left, 2.0):.1f}' height='12'"
            f" fill='{colour}'><title>{section['label']}</title></rect>"
        )
        rects.append(
            f"<text x='{(left + right) / 2:.1f}' y='12' fill='{TEXT}' font-size='9'"
            f" text-anchor='middle'>{section['label']}</text>"
        )
    return (
        f"<svg viewBox='0 0 {CHART_W} 16' width='{CHART_W}' height='16'"
        f" xmlns='http://www.w3.org/2000/svg'>{''.join(rects)}</svg>"
    )


def _section_table(metrics: dict) -> str:
    if not metrics["sections"]:
        return ""
    best = _best_lap(metrics)
    stats = {
        row["section"]: row
        for row in metrics["section_stats"]
        if row["lap"] == best
    }
    rows = ""
    for section in metrics["sections"]:
        row = stats.get(section["label"], {})
        brake = f"{row['brake_point_m']:.0f}" if "brake_point_m" in row else "—"
        min_speed = f"{row['min_speed_kmh']:.1f}" if "min_speed_kmh" in row else "—"
        max_speed = f"{row['max_speed_kmh']:.1f}" if "max_speed_kmh" in row else "—"
        rows += (
            f"<tr><td>{section['label']}</td><td>{section['kind'].replace('_', ' ')}</td>"
            f"<td>{section['dist_start_m']:.0f}–{section['dist_end_m']:.0f}</td>"
            f"<td>{min_speed}</td><td>{max_speed}</td><td>{brake}</td></tr>"
        )
    return (
        f"<h2>Sections (best lap {best})</h2><table><tr><th>Section</th><th>Kind</th>"
        "<th>Span m</th><th>Min km/h</th><th>Max km/h</th><th>Brake point m</th></tr>"
        + rows + "</table>"
    )


def _event_table(metrics: dict) -> str:
    if not metrics["events"]:
        return "<h2>Events</h2><div class='dim'>No incidents detected — a clean run.</div>"
    rows = "".join(
        f"<tr><td>{html.escape(event['event_id'])}</td><td>{event['lap']}</td>"
        f"<td>{event['dist_start_m']:.0f}–{event['dist_end_m']:.0f}</td>"
        f"<td>{event['severity']:.2f}</td><td style='text-align:left'>"
        f"{html.escape(event['summary'])}</td></tr>"
        for event in metrics["events"]
    )
    return (
        "<h2>Events</h2><table><tr><th>Id</th><th>Lap</th><th>Span m</th>"
        "<th>Severity</th><th>Summary</th></tr>" + rows + "</table>"
    )


def _feedback_block(feedback: dict | None) -> str:
    if feedback is None:
        return (
            "<h2>Coach feedback</h2><div class='dim'>No feedback generated yet — "
            "run: racecoach coach &lt;run_id&gt;</div>"
        )
    issues = ""
    for issue in feedback["issues"]:
        refs = ", ".join(
            f"{html.escape(item['ref'])} ({html.escape(item['detail'])})"
            for item in issue["evidence"]
        )
        issues += (
            f"<div class='card'><b>{html.escape(issue['issue'])}</b>"
            f"<div>{html.escape(issue['cause'])}</div>"
            f"<div>→ {html.escape(issue['action'])}</div>"
            f"<div class='dim'>evidence: {refs}</div></div>"
        )
    highlights = "".join(f"<li>{html.escape(line)}</li>" for line in feedback["highlights"])
    recommendations = "".join(
        f"<li><code>{html.escape(line)}</code></li>"
        for line in feedback["code_recommendations"]
    )
    return (
        "<h2>Coach feedback</h2>"
        f"<span class='chip'>{html.escape(feedback['model'])} · "
        f"{html.escape(feedback['prompt_version'])}</span>"
        f"<p>{html.escape(feedback['overall'])}</p>"
        + (f"<ul>{highlights}</ul>" if highlights else "")
        + issues
        + (f"<div>Controller recommendations:<ul>{recommendations}</ul></div>"
           if recommendations else "")
        + f"<p><b>Next experiment:</b> {html.escape(feedback['next_experiment'])}</p>"
        "<div class='dim'>Full prompt/response audit records live beside this file "
        "in coaching/.</div>"
    )


def _notes(metrics: dict) -> str:
    if not metrics["analysis_notes"]:
        return ""
    items = "".join(f"<li>{html.escape(note)}</li>" for note in metrics["analysis_notes"])
    return f"<h2>Analysis notes</h2><ul class='dim'>{items}</ul>"
