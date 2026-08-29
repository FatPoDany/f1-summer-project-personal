"""A reference built from the best each corner was actually driven.

Every comparison in Apex used to be against one lap: the session's quickest.
That lap is a reasonable summary of a session and a poor target for a corner.
Three laps is a small sample, so the quickest of them is partly luck, and it
has its own bad corners — where it does, the driver is told they matched the
reference, which is true and useless.

It falls hardest on the quickest lap itself. It has nothing quicker to be read
against, so it drops to single-lap mode and gets four technique checks and no
corner speeds at all. The lap a driver most wants explained is the one the
software has least to say about.

So: keep one real lap as the grid, and let each corner's target be whichever
lap went through that corner quickest. Nothing here is synthesised. Every
``ref_`` number is a number some lap actually recorded, at a corner that lap
actually drove; only the choice of which lap differs from corner to corner, and
each row says which one it was.

Three things this is careful about.

**One anchor lap defines the corners.** ``detect_corners`` numbers apexes in
the order it finds them, so ``T4`` means "the fourth dip on this lap" and
nothing more. Detect on two laps and one extra dip renames every corner after
it. So corners are detected once, on the anchor, and every lap is measured on
that grid. Corner identity is then true by construction rather than by luck --
the same discipline ``adherence`` needs across sessions, for the same reason.

**Quickest through the corner, not quickest overall.** The choice is made on
time across the zone, measured against the anchor, which is the only quantity
that says who actually drove that piece of road better.

**It has no lap time, so it does not claim one.** A composite is not a lap and
never ran, so ``total_delta_s`` is None rather than a subtraction against a
time nobody set. What is real is the time available across the measured corner
zones, and that is reported instead, under its own name.
"""

from dataclasses import dataclass
from statistics import mean

from f1coach_core.debrief import (
    MIN_TIME_LOST_S,
    DebriefPoint,
    debrief_points,
    review_points,
)
from f1coach_core.features import (
    CornerScatter,
    _channel_corner_facts,
    _flag_technique,
    _on_grid,
    _zone_indices,
    build_evidence_summary,
    detect_corners,
    time_delta,
)
from f1coach_core.lap import Lap

COMPOSITE_MODE = "composite"


@dataclass(frozen=True)
class CornerBest:
    """One corner, and the lap that went through it quickest."""

    corner: str
    apex_m: float
    span_m: tuple[float, float]
    source: str  # the lap this corner's numbers come from
    zone_time_s: float  # time across the zone relative to the anchor; lower is quicker
    facts: dict  # that lap's own deterministic facts for this zone


@dataclass(frozen=True)
class CompositeReference:
    """Per-corner targets drawn from a session, on one anchor lap's corner grid."""

    anchor: Lap
    corners: tuple[CornerBest, ...]

    @property
    def name(self) -> str:
        """What an audit record calls this reference.

        Counted over the laps that actually contributed a corner rather than
        over the laps it was offered. A lap slower than the rest everywhere
        changes neither the targets nor the evidence packet, so it must not
        change the name either -- a name that moved would miss the stored
        report for a comparison that had not changed, and send a participant
        back to waiting on a model for an answer already on disk.
        """
        return f"best corners of {len(self.sources)} laps"

    @property
    def sources(self) -> tuple[str, ...]:
        """The laps that contributed a corner, without repeats."""
        seen: list[str] = []
        for corner in self.corners:
            if corner.source not in seen:
                seen.append(corner.source)
        return tuple(sorted(seen))

    @property
    def gain_s(self) -> float:
        """Time the anchor lap gives away to the rest of the session, in corners.

        Not a lap time and not a promise: it is what the anchor lost at the
        corners another lap took quicker, which is exactly the time this
        reference is asking for and no more.
        """
        return round(-sum(min(corner.zone_time_s, 0.0) for corner in self.corners), 3)

    def by_corner(self) -> dict[str, CornerBest]:
        return {corner.corner: corner for corner in self.corners}


def corner_measurements(
    lap: Lap, anchor: Lap, zones: list[dict] | None = None
) -> dict[str, dict]:
    """One lap's corner facts on the anchor's grid, keyed by corner.

    Each row carries ``zone_time_s``: the time this lap took across the zone
    relative to the anchor. Corners the lap is too short to cover are absent
    rather than guessed at.
    """
    grid, delta = time_delta(lap, anchor)
    channels = _on_grid(lap, grid)
    rows: dict[str, dict] = {}
    for zone in detect_corners(anchor) if zones is None else zones:
        indices = _zone_indices(grid, zone)
        if indices is None:
            continue
        i0, i1, apex, exit_i = indices
        rows[str(zone["corner"])] = {
            **zone,
            **_channel_corner_facts(grid, channels, i0, i1, apex, exit_i),
            "zone_time_s": round(float(delta[i1] - delta[i0]), 3),
        }
    return rows


def composite_reference(laps: list[Lap], anchor: Lap | None = None) -> CompositeReference | None:
    """The quickest version of each corner across a session.

    ``None`` when there is nothing to build one from — fewer than two laps, an
    anchor that was asked for and has no detectable corners, or no lap at all
    with corners on it. The caller then keeps whatever reference it would have
    used before, rather than being handed an empty composite that silently says
    everything is fine.
    """
    usable = [lap for lap in laps if lap is not None]
    if len(usable) < 2:
        return None
    if anchor is not None:
        # An anchor that was asked for is the grid the caller expects corner
        # names to mean something on. If it has no corners, say so rather than
        # quietly answering about a different lap.
        zones = detect_corners(anchor)
        if not zones:
            return None
    else:
        # The quickest lap that actually has corners on it. A truncated capture
        # has an absurdly short lap time and no detectable corners, so picking
        # purely on time hands the grid to a fragment and loses the reference
        # for the whole session.
        anchor, zones = None, []
        for candidate in sorted(usable, key=lambda lap: lap.lap_time):
            found = detect_corners(candidate)
            if found:
                anchor, zones = candidate, found
                break
        if anchor is None:
            return None

    best: dict[str, CornerBest] = {}
    for lap in usable:
        try:
            measured = corner_measurements(lap, anchor, zones)
        except ValueError:  # too short to share a distance grid with the anchor
            continue
        for label, row in measured.items():
            zone_time = float(row["zone_time_s"])
            standing = best.get(label)
            # Strictly quicker, so the anchor keeps a tie. Ties happen: the
            # anchor scores zero against itself, and a lap that matched it
            # would otherwise take the corner on iteration order.
            if standing is not None and zone_time >= standing.zone_time_s:
                continue
            span = row["span_m"]
            best[label] = CornerBest(
                corner=label,
                apex_m=float(row["apex_m"]),
                span_m=(float(span[0]), float(span[1])),
                source=lap.source.stem,
                zone_time_s=zone_time,
                facts={
                    key: value
                    for key, value in row.items()
                    if key not in {"corner", "apex_m", "span_m", "zone_time_s"}
                },
            )
    if not best:
        return None
    return CompositeReference(
        anchor=anchor,
        corners=tuple(best[label] for label in (zone["corner"] for zone in zones) if label in best),
    )


def composite_corner_facts(lap: Lap, composite: CompositeReference) -> list[dict]:
    """``_corner_facts`` rows, with each corner's ``ref_`` from its quickest lap.

    Same shape as a two-lap comparison so the corner table, the debrief and the
    coaching evidence all read it without knowing where it came from — plus
    ``ref_source``, because a driver reading a target is entitled to know which
    of their laps set it.
    """
    mine = corner_measurements(lap, composite.anchor)
    targets = composite.by_corner()
    facts = []
    for label, row in mine.items():
        target = targets.get(label)
        if target is None:
            continue
        fact = {
            "corner": label,
            "apex_m": row["apex_m"],
            "span_m": row["span_m"],
        }
        for key, value in row.items():
            if key in {"corner", "apex_m", "span_m", "zone_time_s"}:
                continue
            fact[key] = value
            fact[f"ref_{key}"] = target.facts.get(key)
        fact["time_lost_s"] = round(float(row["zone_time_s"]) - target.zone_time_s, 3)
        fact["ref_source"] = target.source
        facts.append(fact)
    return facts


def composite_corner_table(lap: Lap, composite: CompositeReference) -> list[dict]:
    """Analysis-screen corner rows against per-corner bests.

    The comparison shape, so the screen shows the same columns it shows for two
    laps: the delta is real time across the zone, and the technique flags are
    about the driver's own corner either way.
    """
    rows = []
    for fact in composite_corner_facts(lap, composite):
        row = dict(fact)
        row["delta_s"] = row.pop("time_lost_s")
        _flag_technique(row, publish_guides=False)
        rows.append(row)
    return rows


def composite_evidence_summary(lap: Lap, composite: CompositeReference) -> dict:
    """The evidence packet for a lap read against per-corner bests.

    ``analysis_mode`` is its own value so a reader can tell what it is looking
    at, but every rule that branches on the mode treats it as the comparison it
    is: the ``ref`` values are real measurements from real laps, so the same
    directions and sizes mean the same things.
    """
    corners = composite_corner_facts(lap, composite)
    return {
        "analysis_mode": COMPOSITE_MODE,
        "lap": {"name": lap.source.stem, "lap_time_s": round(lap.lap_time, 3)},
        "reference": {
            "name": composite.name,
            # A composite never ran, so it has no lap time. Reporting the sum of
            # its parts as one would put a time on screen that no lap set and
            # nobody can go and look at.
            "lap_time_s": None,
            "anchor": composite.anchor.source.stem,
            "sources": list(composite.sources),
        },
        "total_delta_s": None,
        # What is actually measured: time this lap gives away across the corner
        # zones. It is not a lap-time deficit and is not called one.
        "corner_delta_s": round(sum(fact["time_lost_s"] for fact in corners), 3),
        "corners": corners,
    }


def reference_name(reference: Lap | CompositeReference | None) -> str | None:
    """What to record this reference as. ``None`` means a single-lap review."""
    if reference is None:
        return None
    if isinstance(reference, CompositeReference):
        return reference.name
    return reference.source.stem


def evidence_for(lap: Lap, reference: Lap | CompositeReference | None) -> dict:
    """One entry point for all three ways a lap can be read.

    Keeping the branch here rather than inside ``build_evidence_summary`` keeps
    the features stage unaware of the composite, which is built on top of it.
    """
    if isinstance(reference, CompositeReference):
        return composite_evidence_summary(lap, reference)
    return build_evidence_summary(lap, reference)


def composite_debrief(
    lap: Lap,
    composite: CompositeReference,
    *,
    limit: int = 3,
    min_time_lost_s: float = MIN_TIME_LOST_S,
    scatter: CornerScatter | None = None,
) -> list[DebriefPoint]:
    """The stretches where this lap lost most to the best each corner was driven."""
    return debrief_points(
        composite_corner_facts(lap, composite),
        limit=limit,
        min_time_lost_s=min_time_lost_s,
        scatter=scatter,
    )


def composite_review_points(
    lap: Lap,
    composite: CompositeReference,
    *,
    scatter: CornerScatter | None = None,
) -> list[DebriefPoint]:
    """Every corner as a reviewable stretch, against its own quickest version."""
    return review_points(
        composite_corner_facts(lap, composite), compared=True, scatter=scatter
    )


def composite_summary(facts: list[dict]) -> str:
    """One sentence about a composite comparison, for a heading.

    Says what the reference is before it says anything measured by it. The
    two-lap version opens with a lap-time deficit; this one must not, because
    there is no second lap time to have a deficit against. A driver who reads a
    corner-by-corner target as another lap's time will go and check it against
    the times they can see, find it wrong, and be right.
    """
    lost = [fact for fact in facts if (fact.get("time_lost_s") or 0.0) > 0]
    if not facts:
        return "No corner could be measured against the rest of the session."
    if not lost:
        return "Against the best each corner was driven: nothing left on the table."
    available = sum(fact["time_lost_s"] for fact in lost)
    return (
        f"Against the best each corner was driven: {available:.2f} s available "
        f"across {len(lost)} of {len(facts)} corners, "
        f"averaging {mean(fact['time_lost_s'] for fact in lost):.2f} s each."
    )
