"""Self-contained HTML analysis report — feeds the blog, IBM status forms,
and the final write-up. No external assets: inline CSS and inline SVG charts,
dark like the app."""

import html
from datetime import UTC, datetime

import numpy as np

from f1coach_core.analysis import sector_times
from f1coach_core.coach import CoachingReport
from f1coach_core.features import time_delta
from f1coach_core.lap import Lap

BG, PANEL, TEXT, DIM = "#161616", "#1f1f1f", "#f4f4f4", "#c6c6c6"
BLUE, PURPLE, GREEN, YELLOW = "#78a9ff", "#be95ff", "#42be65", "#f1c21b"

_CSS = f"""
body {{ background: {BG}; color: {TEXT}; margin: 2rem auto; max-width: 60rem;
       font: 14px/1.5 -apple-system, 'IBM Plex Sans', 'Segoe UI', sans-serif; }}
h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.05rem; margin-top: 2rem; }}
.dim {{ color: {DIM}; }}
table {{ border-collapse: collapse; margin: .5rem 0; }}
td, th {{ border: 1px solid #393939; padding: .3rem .8rem; text-align: right; }}
th {{ color: {DIM}; font-weight: 500; }}
.card {{ background: {PANEL}; border: 1px solid #393939; border-radius: 6px;
        padding: .8rem 1rem; margin: .8rem 0; }}
.chip {{ background: #262626; border-radius: 4px; padding: .1rem .5rem;
        font-size: .8rem; color: {DIM}; }}
svg {{ background: {PANEL}; border-radius: 6px; display: block; margin: .5rem 0;
      max-width: 100%; height: auto; }}
"""


def _polyline(xs, ys, x_range, y_range, width, height, colour, pad=8) -> str:
    x0, x1 = x_range
    y0, y1 = y_range
    span_x = (x1 - x0) or 1.0
    span_y = (y1 - y0) or 1.0
    stride = max(1, len(xs) // 600)
    points = " ".join(
        f"{pad + (x - x0) / span_x * (width - 2 * pad):.1f},"
        f"{height - pad - (y - y0) / span_y * (height - 2 * pad):.1f}"
        for x, y in zip(xs[::stride], ys[::stride], strict=True)
    )
    return f'<polyline fill="none" stroke="{colour}" stroke-width="1.5" points="{points}"/>'


def _chart(title: str, body: str, width: int, height: int, caption: str) -> str:
    return (
        f"<h2>{html.escape(title)}</h2>"
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}"'
        f' xmlns="http://www.w3.org/2000/svg">{body}</svg>'
        f'<div class="dim">{caption}</div>'
    )


def _speed_chart(lap: Lap, reference: Lap | None) -> str:
    dist = lap.df["dist"].to_numpy(dtype=float)
    speed = lap.speed_kmh.to_numpy()
    x_range = (float(dist[0]), float(dist[-1]))
    tops = [float(speed.max())]
    body = ""
    if reference is not None:
        ref_dist = reference.df["dist"].to_numpy(dtype=float)
        ref_speed = reference.speed_kmh.to_numpy()
        tops.append(float(ref_speed.max()))
        body += _polyline(ref_dist, ref_speed, x_range, (0, max(tops)), 800, 220, PURPLE)
    body += _polyline(dist, speed, x_range, (0, max(tops)), 800, 220, BLUE)
    caption = f"speed (km/h) over distance — {lap.source.stem} in blue"
    if reference is not None:
        caption += f", {reference.source.stem} in purple"
    return _chart("Speed trace", body, 800, 220, caption)


def _delta_chart(lap: Lap, reference: Lap) -> str:
    grid, delta = time_delta(lap, reference)
    lo, hi = min(float(delta.min()), 0.0), max(float(delta.max()), 0.0)
    x_range = (float(grid[0]), float(grid[-1]))
    zero = _polyline(grid, np.zeros_like(delta), x_range, (lo, hi), 800, 140, "#393939")
    trace = _polyline(grid, delta, x_range, (lo, hi), 800, 140, YELLOW)
    return _chart(
        "Cumulative time delta",
        zero + trace,
        800,
        140,
        f"Δt (s) vs {reference.source.stem} — above the line is time lost; "
        f"{delta[-1]:+.3f} s at the flag",
    )


def _sector_table(lap: Lap, reference: Lap | None) -> str:
    mine = sector_times(lap)
    if not mine:
        return ""
    ref = sector_times(reference) if reference is not None else {}
    header = "<tr><th>sector</th><th>lap</th>" + ("<th>reference</th><th>Δ</th>" if ref else "")
    rows = [header + "</tr>"]
    for sector in sorted(mine):
        row = f"<tr><td>S{sector}</td><td>{mine[sector]:.3f} s</td>"
        if ref and sector in ref:
            row += f"<td>{ref[sector]:.3f} s</td><td>{mine[sector] - ref[sector]:+.3f} s</td>"
        rows.append(row + "</tr>")
    return "<h2>Sector times</h2><table>" + "".join(rows) + "</table>"


def _findings_section(coaching: CoachingReport | None) -> str:
    if coaching is None:
        return '<h2>Coaching</h2><p class="dim">No coaching was run for this analysis.</p>'
    chip = f'<span class="chip">{html.escape(coaching.model)} · ' \
           f"{html.escape(coaching.prompt_version)}</span>"
    if not coaching.findings:
        return f'<h2>Coaching {chip}</h2><p class="dim">Nothing significant — ' \
               "this lap matches the reference.</p>"
    cards = []
    for finding in coaching.findings:
        evidence = "".join(
            f'<div class="dim">◈ {html.escape(e.corner)} {html.escape(e.metric)}: '
            f"{e.value:g} {html.escape(e.unit)} vs {e.ref:g} {html.escape(e.unit)} "
            f"(at {e.span[0]:.0f}–{e.span[1]:.0f} m)</div>"
            for e in finding.evidence
        )
        cards.append(
            f'<div class="card"><strong>{html.escape(finding.issue)}</strong> '
            f'<span class="chip">confidence {finding.confidence:.2f}</span>'
            f"<div>{html.escape(finding.cause)}</div>"
            f"<div>→ {html.escape(finding.action)}</div>{evidence}</div>"
        )
    return f"<h2>Coaching {chip}</h2>" + "".join(cards)


def render_html_report(
    lap: Lap,
    reference: Lap | None,
    coaching: CoachingReport | None,
    session_name: str | None = None,
) -> str:
    """One self-contained HTML document for the current analysis."""
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    subtitle = f"session {html.escape(session_name)} · " if session_name else ""
    stats = (
        f"<tr><th></th><th>{html.escape(lap.source.stem)}</th>"
        + (f"<th>{html.escape(reference.source.stem)}</th>" if reference else "")
        + "</tr>"
        f"<tr><td>lap time</td><td>{lap.lap_time:.3f} s</td>"
        + (f"<td>{reference.lap_time:.3f} s</td>" if reference else "")
        + "</tr>"
        f"<tr><td>top speed</td><td>{lap.top_speed_kmh:.0f} km/h</td>"
        + (f"<td>{reference.top_speed_kmh:.0f} km/h</td>" if reference else "")
        + "</tr>"
    )
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>Apex report — {html.escape(lap.source.stem)}</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>Apex — lap analysis: {html.escape(lap.source.stem)}</h1>",
        f'<div class="dim">{subtitle}generated {generated}</div>',
        f"<table>{stats}</table>",
        _speed_chart(lap, reference),
    ]
    if reference is not None:
        parts.append(_delta_chart(lap, reference))
    parts.append(_sector_table(lap, reference))
    parts.append(_findings_section(coaching))
    parts.append("</body></html>")
    return "".join(parts)
