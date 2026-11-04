"""Person A's comfort physics, ported: characterized against A's own functions.

The reference values were computed by running A's ``app/spotcool/physics.py`` (branch
``third_level_improvement``, extracted with ``git show`` and imported from a temporary
directory) on the same inputs; the port gave the same floats, bit for bit.
"""

import pytest

from spotcool_agent.domain.comfort import (
    Occupant,
    comfort_setpoint,
    comfort_temperature,
    dynamic_clo,
    occupant_pmv,
    occupants,
    pmv_ppd,
    relative_air_speed,
)
from spotcool_agent.domain.scene import PersonScene
from spotcool_agent.services.fake.scene import OFFICE_WORKER

EXACT = 1e-12


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((22.0, 22.0, 0.1, 50.0, 1.2, 0.5), (-0.8111525044953543, 18.875120274663573)),
        ((26.0, 26.0, 0.1, 50.0, 1.1, 0.71), (0.5405380797536351, 11.11458167644814)),
        ((30.0, 31.5, 0.3, 60.0, 1.0, 0.36), (1.3892223873135696, 44.93825254870191)),
    ],
)
def test_pmv_ppd_matches_person_as(args: tuple[float, ...], expected: tuple[float, float]) -> None:
    assert pmv_ppd(*args) == pytest.approx(expected, abs=EXACT)


def test_body_movement_and_dynamic_clothing_match_person_as() -> None:
    assert relative_air_speed(0.1, 1.7) == pytest.approx(0.31, abs=EXACT)
    assert relative_air_speed(0.1, 1.0) == 0.1  # no movement at or below 1 met
    assert dynamic_clo(1.01, 2.1) == pytest.approx(0.7983809523809523, abs=EXACT)
    assert dynamic_clo(0.61, 1.1) == 0.61  # unchanged at or below 1.2 met


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((25.0, 50.0, 0.1, 1.7, 0.61, 0.0), (0.5008521161530979, 10.242991811548833)),
        ((24.0, 40.0, 0.2, 1.1, 0.96, 2.0), (0.2680441471723035, 6.491887180061127)),
    ],
)
def test_occupant_pmv_matches_person_as(
    args: tuple[float, ...], expected: tuple[float, float]
) -> None:
    assert occupant_pmv(*args) == pytest.approx(expected, abs=EXACT)


@pytest.mark.parametrize(
    ("people", "rh", "target", "expected"),
    [
        ([(1.0, 1.1, 0.71)], 50.0, 0.0, 24.415000000502914),
        ([(1.0, 1.0, 0.71), (1.0, 1.4, 1.06)], 50.0, 0.0, 23.444999999832362),
        ([(1.0, 1.1, 0.67)], 60.0, -0.5, 22.775000001769513),
    ],
)
def test_comfort_temperature_matches_person_as(
    people: list[tuple[float, float, float]], rh: float, target: float, expected: float
) -> None:
    found = [Occupant(weight, met, clo) for weight, met, clo in people]
    assert comfort_temperature(rh, found, [0.1] * len(found), target) == pytest.approx(
        expected, abs=EXACT
    )


def test_a_scene_becomes_occupants_with_a_s_tables_and_the_chair() -> None:
    standing = PersonScene(
        clothing_ensemble="suit_jacket",
        activity="standing_working",
        posture="standing",
        discomfort_cue="none",
    )
    assert occupants([OFFICE_WORKER, standing]) == [
        Occupant(weight=1.0, met=1.1, clo=pytest.approx(0.71)),  # type: ignore[arg-type]
        Occupant(weight=1.0, met=1.4, clo=0.96),
    ]
    # one seated office worker, 50 % RH, still air: A's own 24.415 degC
    assert comfort_setpoint([OFFICE_WORKER]) == pytest.approx(24.415000000502914, abs=EXACT)
    with pytest.raises(ValueError, match="no people"):
        comfort_setpoint([])
