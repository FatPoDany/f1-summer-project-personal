"""A reference built from the best each corner was actually driven.

The property that matters most here is negative: nothing in a composite is
synthesised. Every ``ref_`` number has to be a number some real lap recorded at
a corner that lap really drove, and each row has to say which lap that was --
otherwise this is a fabricated target wearing a measurement's clothes, which is
the one thing the rest of the project spends its effort refusing to do.
"""

import pytest

from f1coach_core import load_sample_session
from f1coach_core.coach import coachable_corners, opportunity_catalog
from f1coach_core.features import build_evidence_summary, detect_corners
from f1coach_core.reference import (
    CompositeReference,
    composite_corner_facts,
    composite_corner_table,
    composite_debrief,
    composite_evidence_summary,
    composite_reference,
    composite_review_points,
    composite_summary,
    corner_measurements,
    evidence_for,
    reference_name,
)

# Two seconds of telemetry: readable as a lap, too short to share a distance
# grid with a real one, and with the shortest "lap time" in any session it
# joins. Exactly what a capture cut off at the pit exit leaves behind.
FRAGMENT = "t,speed,throttle,brake,steer,gear\n0.0,10.0,1,0,0,3\n1.0,20.0,1,0,0,3\n"

CHANNEL_KEYS = (
    "brake_point_m",
    "min_speed_kmh",
    "exit_speed_kmh",
    "throttle_reapply_m",
    "coast_distance_m",
)


@pytest.fixture(scope="module")
def session():
    return load_sample_session()


@pytest.fixture(scope="module")
def composite(session):
    return composite_reference(list(session.laps), anchor=session.best_lap)


# -- what it is --------------------------------------------------------------


def test_every_target_is_a_number_some_lap_really_recorded(session, composite):
    """The whole claim. Checked corner by corner against the owning lap itself.

    A composite that quietly averaged two laps, or interpolated between them,
    would pass every other test in this file and be a fabrication.
    """
    by_source = {lap.source.stem: lap for lap in session.laps}
    for corner in composite.corners:
        owner = by_source[corner.source]
        measured = corner_measurements(owner, composite.anchor)[corner.corner]
        for key in CHANNEL_KEYS:
            assert corner.facts[key] == measured[key], (corner.corner, key)


def test_each_row_says_which_lap_set_its_target(session, composite):
    """A driver reading a target is entitled to know which of their laps set it."""
    facts = composite_corner_facts(session.best_lap, composite)
    sources = {fact["ref_source"] for fact in facts}

    assert sources <= {lap.source.stem for lap in session.laps}
    assert len(sources) > 1, "the sample has corners won by different laps"


def test_corners_come_from_one_lap_so_their_names_cannot_drift(session, composite):
    """``detect_corners`` numbers apexes positionally, so T4 is not portable.

    Detecting on each lap separately and matching by name is the bug this
    module exists to make impossible: one extra dip on one lap renames every
    corner after it, and the comparison silently measures T4 against T3.
    """
    assert [corner.corner for corner in composite.corners] == [
        zone["corner"] for zone in detect_corners(composite.anchor)
    ]


def test_the_anchor_keeps_a_corner_nobody_beat_it_at(session, composite):
    """Ties go to the anchor, and it scores zero against itself."""
    anchor = composite.anchor.source.stem
    kept = [c for c in composite.corners if c.source == anchor]

    assert kept, "the quickest lap should still own some of its corners"
    assert all(c.zone_time_s == 0.0 for c in kept)


def test_it_is_never_slower_than_the_lap_that_anchors_it(composite):
    """A target drawn from the best of several cannot be worse than one of them."""
    assert all(corner.zone_time_s <= 0.0 for corner in composite.corners)
    assert composite.gain_s > 0


# -- the harm it exists to fix -----------------------------------------------


def test_the_quickest_lap_stops_being_the_one_with_nothing_to_say(session, composite):
    """Four fixed technique guides, for the lap a driver most wants explained.

    Nothing quicker existed to read it against, so it fell to single-lap mode:
    no corner speeds, no brake points, no time attributed anywhere. Against the
    best each of its own corners was driven it has all three.
    """
    best = session.best_lap
    alone = build_evidence_summary(best)
    against_corners = composite_evidence_summary(best, composite)

    assert alone["analysis_mode"] == "single_lap"
    assert all(fact["time_lost_s"] is None for fact in alone["corners"])

    assert against_corners["analysis_mode"] == "composite"
    lost = [f for f in against_corners["corners"] if f["time_lost_s"] > 0]
    assert len(lost) >= 3
    assert {"min_speed", "exit_speed"} & {
        metric for _, metric in opportunity_catalog(against_corners)
    }


def test_the_time_it_asks_for_is_time_another_lap_already_set(session, composite):
    """Not a projection: every second of it was driven, at that corner, that day."""
    summary = composite_evidence_summary(session.best_lap, composite)

    assert summary["corner_delta_s"] == pytest.approx(composite.gain_s, abs=0.011)


# -- what it refuses to claim ------------------------------------------------


def test_it_reports_no_lap_time_because_it_never_ran(session, composite):
    """The sum of the best parts is not a time anybody can go and look at."""
    summary = composite_evidence_summary(session.best_lap, composite)

    assert summary["reference"]["lap_time_s"] is None
    assert summary["total_delta_s"] is None
    assert summary["corner_delta_s"] > 0


def test_the_heading_never_opens_with_a_lap_time_deficit(session, composite):
    """The two-lap heading does, and a driver would go and check it.

    They would find the composite has no time to be behind, be unable to
    reconcile the sentence with the times on their own screen, and be right.
    """
    facts = composite_corner_facts(session.best_lap, composite)
    heading = composite_summary(facts)

    assert "s off" not in heading
    assert "corners" in heading


# -- it behaves like the comparison it is ------------------------------------


def test_every_rule_that_branches_on_the_mode_treats_it_as_a_comparison(
    session, composite
):
    """The ``ref`` values are real measurements, so the same sizes mean the same."""
    summary = composite_evidence_summary(session.laps[0], composite)

    assert coachable_corners(summary)  # ranked by time lost, not technique score
    assert all(
        fact["time_lost_s"] >= 0.05 for fact in coachable_corners(summary)
    )


def test_the_corner_table_and_the_debrief_read_it_without_knowing(session, composite):
    """Same row shape as a two-lap comparison, so nothing downstream branches."""
    rows = composite_corner_table(session.best_lap, composite)
    points = composite_debrief(session.best_lap, composite)
    review = composite_review_points(session.best_lap, composite)

    assert all("delta_s" in row and "technique_flags" in row for row in rows)
    assert points and all(point.time_lost_s is not None for point in points)
    assert [p.corner for p in review] == [row["corner"] for row in rows]


def test_one_entry_point_covers_all_three_ways_a_lap_can_be_read(session, composite):
    lap, best = session.laps[0], session.best_lap

    assert evidence_for(lap, best) == build_evidence_summary(lap, best)
    assert evidence_for(lap, None) == build_evidence_summary(lap)
    assert evidence_for(lap, composite)["analysis_mode"] == "composite"


def test_a_reference_is_recorded_by_a_name_an_audit_can_be_matched_on(
    session, composite
):
    """A composite is not a file, and the number of laps is part of what it is."""
    assert reference_name(None) is None
    assert reference_name(session.best_lap) == session.best_lap.source.stem
    # Counted over the laps that contributed a corner, not the laps on offer: a
    # lap slower everywhere changes neither the targets nor the packet, so a
    # name that moved would miss a stored report for an unchanged comparison.
    assert reference_name(composite) == f"best corners of {len(composite.sources)} laps"
    assert len(composite.sources) < len(session.laps)


# -- when there is nothing to build one from ---------------------------------


def test_one_lap_has_no_best_corners_to_be_read_against(session):
    """None, not an empty composite that would quietly say everything is fine."""
    assert composite_reference([session.best_lap]) is None
    assert composite_reference([]) is None


def test_a_lap_too_short_to_share_a_grid_is_skipped_not_fatal(session, tmp_path):
    """A truncated lap must not take the whole session's reference with it."""
    from f1coach_core import load_telemetry_csv

    stub = tmp_path / "stub.csv"
    stub.write_text(FRAGMENT, encoding="utf-8")
    laps = [*session.laps, load_telemetry_csv(stub)]

    built = composite_reference(laps)

    # Two seconds of telemetry is the quickest "lap" in the session, so an
    # anchor chosen on lap time alone is a fragment with no corners on it --
    # and the whole session loses its reference to a file nobody drove.
    assert isinstance(built, CompositeReference)
    assert built.anchor is session.best_lap
    assert "stub" not in built.sources


def test_an_anchor_that_was_asked_for_is_never_quietly_swapped(session, tmp_path):
    """Corner names only mean something on the grid the caller expects."""
    from f1coach_core import load_telemetry_csv

    stub = tmp_path / "stub.csv"
    stub.write_text(FRAGMENT, encoding="utf-8")
    fragment = load_telemetry_csv(stub)

    assert composite_reference([*session.laps, fragment], anchor=fragment) is None
