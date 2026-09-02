"""Apex's team-matched assignment describes the same race as theirs.

The other half of this project keeps its practice race in its own repository,
and every session it has stored was driven under it. Apex's ``apexibmf1.xml`` is
a port of that file, so the only thing that makes "matched" true is a check that
reads both and compares them. A comment saying they agree is not one.

The copy this reads from is vendored at ``integrations/torcs-1.3.9/reference/``
so the check runs in CI without their repository; point ``IBMF1_PRACTICE_XML``
at a live file to check against a working copy instead.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from racecoach.telemetry.human_capture import study_presets

REPO_ROOT = Path(__file__).resolve().parents[1]
APEX_RACE = REPO_ROOT / "integrations/torcs-1.3.9/overlay/src/raceman/apexibmf1.xml"
REFERENCE = REPO_ROOT / "integrations/torcs-1.3.9/reference"
TEAM_HUMAN = REFERENCE / "ibmf1-human.xml"

# Both files name the race section for the race, and they name it the same
# thing. Kept as a constant so that a rename upstream fails loudly here rather
# than quietly comparing one empty section against another.
RACE_SECTION = "Practice"


def team_race() -> Path:
    override = os.environ.get("IBMF1_PRACTICE_XML")
    return Path(override) if override else REFERENCE / "ibmf1-practice.xml"


def _sections(element: ET.Element) -> dict[str, ET.Element]:
    return {child.get("name", ""): child for child in element if child.tag == "section"}


def _section(root: ET.Element, *names: str) -> ET.Element:
    current = root
    for name in names:
        found = _sections(current).get(name)
        if found is None:
            raise AssertionError(f"no section {'/'.join(names)} in this race file")
        current = found
    return current


def _attrs(element: ET.Element) -> dict[str, str]:
    """Every attnum/attstr directly on this section, by name.

    Values that parse as numbers are normalised, so a reformat upstream -- ``1``
    becoming ``1.0`` -- does not read as a change to the race. Anything else is
    compared as written.
    """
    out: dict[str, str] = {}
    for child in element:
        if child.tag not in {"attnum", "attstr"}:
            continue
        name = child.get("name")
        value = child.get("val")
        if name is None or value is None:
            continue
        try:
            out[name] = format(float(value), ".6g")
        except ValueError:
            out[name] = value
    return out


def _drivers(root: ET.Element) -> list[tuple[str, str]]:
    """The grid, in the order the file lists it.

    Order is not decoration: both races use ``starting order = drivers list``,
    so the list is the grid, and the participant being first is what puts them
    on pole.
    """
    drivers = _section(root, "Drivers")
    grid = []
    for _name, section in sorted(
        _sections(drivers).items(), key=lambda item: int(item[0])
    ):
        attrs = _attrs(section)
        grid.append((attrs.get("module", ""), attrs.get("idx", "")))
    return grid


@pytest.fixture(scope="module")
def ours() -> ET.Element:
    return ET.parse(APEX_RACE).getroot()


@pytest.fixture(scope="module")
def theirs() -> ET.Element:
    return ET.parse(team_race()).getroot()


def test_same_track(ours: ET.Element, theirs: ET.Element) -> None:
    assert _attrs(_section(ours, "Tracks", "1")) == _attrs(
        _section(theirs, "Tracks", "1")
    )


# Everything that decides what the participant experiences. Listed rather than
# compared wholesale because the two files legitimately differ elsewhere -- ours
# carries no menu configuration, since Apex enters the race directly -- and a
# whole-file comparison would have to be weakened until it stopped failing.
RACE_KEYS = (
    "laps",
    "type",
    "starting order",
    "restart",
    "display mode",
    "display results",
    "distance",
    "fuel consumption factor",
    "damage factor",
    "tire factor",
)


@pytest.mark.parametrize("key", RACE_KEYS)
def test_same_race_conditions(ours: ET.Element, theirs: ET.Element, key: str) -> None:
    mine = _attrs(_section(ours, RACE_SECTION))
    yours = _attrs(_section(theirs, RACE_SECTION))
    assert key in yours, f"upstream no longer sets {key!r}"
    assert mine.get(key) == yours[key]


def test_same_starting_grid(ours: ET.Element, theirs: ET.Element) -> None:
    assert _attrs(_section(ours, RACE_SECTION, "Starting Grid")) == _attrs(
        _section(theirs, RACE_SECTION, "Starting Grid")
    )


def test_same_cars_on_the_grid(ours: ET.Element, theirs: ET.Element) -> None:
    assert _drivers(ours) == _drivers(theirs)


def test_participant_starts_from_pole(ours: ET.Element) -> None:
    assert _drivers(ours)[0][0] == "human"


def test_same_focused_driver(ours: ET.Element, theirs: ET.Element) -> None:
    mine = _attrs(_section(ours, "Drivers"))
    yours = _attrs(_section(theirs, "Drivers"))
    assert mine.get("focused module") == yours.get("focused module")
    assert mine.get("focused idx") == yours.get("focused idx")


def test_neither_retires_a_car_for_damage(ours: ET.Element, theirs: ET.Element) -> None:
    """Apex's own presets disable retirement; this one deliberately does not.

    Their race leaves the setting alone, so TORCS's own limit applies to their
    participants. Carrying Apex's safeguard in here would be a fifth difference
    between the two races, and the point of this assignment is that there are
    none. The assertion is two-sided so the match stays deliberate: if they ever
    set it, this fails and we set the same thing.
    """
    assert "maximum dammage" not in _attrs(_section(theirs, RACE_SECTION))
    assert "maximum dammage" not in _attrs(_section(ours, RACE_SECTION))


def test_preset_reports_the_race_it_opens(ours: ET.Element) -> None:
    """The preset object and the race file have to agree.

    Different things read them -- the manifest and the study exports come from
    the preset, TORCS reads the file -- so a disagreement would label a capture
    with conditions it was not driven under.
    """
    preset = study_presets("wtorcs.exe")[0]
    race = _attrs(_section(ours, RACE_SECTION))
    track = _attrs(_section(ours, "Tracks", "1"))
    assert preset.preset_id == "ibmf1-practice-v5"
    assert preset.race_config.name == APEX_RACE.name
    assert format(float(preset.laps), ".6g") == race["laps"]
    assert preset.track_id == track["name"]
    assert preset.track_category == track["category"]
    assert list(preset.opponents) == [
        module for module, _ in _drivers(ours) if module != "human"
    ]


def test_preset_names_the_car_they_drive() -> None:
    """TORCS takes the car from the driver profile, not from the race file.

    So this is the one condition that cannot be checked by comparing the two
    race files: theirs lives in their human.xml and ours lives in the preset,
    which writes it into the profile at launch.
    """
    theirs = ET.parse(TEAM_HUMAN).getroot()
    wanted = _attrs(_section(theirs, "Preferences"))["car name"]
    assert study_presets("wtorcs.exe")[0].car_id == wanted


def test_the_apex_presets_are_untouched() -> None:
    """Adding a third assignment must not have moved the other two.

    Sessions already collected name them, and ``study_preset_by_id`` refuses an
    id this build does not have. A preset that quietly changed conditions under
    the same id would relabel data that is already on disk.
    """
    by_id = {preset.preset_id: preset for preset in study_presets("wtorcs.exe")}
    for preset_id, track in (
        ("apex-study-v1", "aalborg"),
        ("apex-study-speedway-v1", "g-track-1"),
    ):
        preset = by_id[preset_id]
        assert preset.track_id == track
        assert preset.laps == 3
        assert preset.car_id == "car7-trb1"
        assert preset.opponents == ()
