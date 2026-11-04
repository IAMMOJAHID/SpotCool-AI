"""Thermal comfort (ISO 7730 / ASHRAE 55): Person A's physics, near verbatim and pure.

``pmv_ppd``, ``relative_air_speed``, ``dynamic_clo``, ``occupant_pmv`` and
``comfort_temperature`` are A's (``app/spotcool/physics.py`` on branch
``third_level_improvement``), and so are the clothing and activity tables
(``app/spotcool/perception.py``). ``Occupant`` keeps the fields these functions read. What is
the agent's own: ``occupants`` (a scene's people as occupants, A's fusion rule without the
motion and sunlight terms) and ``comfort_setpoint`` (the meeting room's target).

No model call happens here: a scene reader says what people wear and do, and these functions
compute the temperature.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

from spotcool_agent.domain.scene import PersonScene

# ASHRAE 55-2020 Table 5.2.2.2 (typical ensembles) and ISO 9920 for outerwear.
CLOTHING_CLO = {
    "shorts_short_sleeve": 0.36,
    "skirt_or_dress_light": 0.54,
    "trousers_short_sleeve": 0.57,
    "trousers_long_sleeve": 0.61,
    "suit_jacket": 0.96,
    "sweater_or_layers": 1.01,
    "winter_outerwear": 1.60,
}
# ASHRAE 55-2020 Table 5.2.1.2 / ISO 8996.
ACTIVITY_MET = {
    "seated_quiet": 1.0,
    "seated_typing": 1.1,
    "standing_relaxed": 1.2,
    "standing_working": 1.4,
    "walking": 1.7,
    "carrying_or_lifting": 2.1,
    "exercising": 3.0,
}
CHAIR_CLO = 0.1
"""What an office chair adds to a seated person's insulation (A's)."""
STILL_AIR_MPS = 0.1
"""The air speed at an occupant without a modelled jet: A's floor in ``occupant_air_speed``."""
DEFAULT_RH_PCT = 50.0
"""The room's relative humidity when nothing measures it (the VRF gateway has no RH point)."""


@dataclass(frozen=True)
class Occupant:
    """One person for the comfort model (A's ``Occupant``, without its floor position)."""

    weight: float
    met: float
    clo: float
    radiant_delta_c: float = 0.0


@lru_cache(maxsize=65536)
def _pmv_ppd_cached(
    ta: float, tr: float, vel: float, rh: float, met: float, clo: float
) -> tuple[float, float]:
    pa = rh * 10 * math.exp(16.6536 - 4030.183 / (ta + 235))
    icl = 0.155 * clo
    m = met * 58.15
    mw = m
    fcl = 1 + 1.29 * icl if icl <= 0.078 else 1.05 + 0.645 * icl
    hcf = 12.1 * math.sqrt(vel)
    taa = ta + 273
    tra = tr + 273
    tcla = taa + (35.5 - ta) / (3.5 * icl + 0.1)
    p1 = icl * fcl
    p2 = p1 * 3.96
    p3 = p1 * 100
    p4 = p1 * taa
    p5 = 308.7 - 0.028 * mw + p2 * (tra / 100) ** 4
    xn = tcla / 100
    xf = tcla / 50
    iterations = 0
    hc = hcf
    while abs(xn - xf) > 0.00015:
        xf = (xf + xn) / 2
        hc = max(hcf, 2.38 * abs(100 * xf - taa) ** 0.25)
        xn = (p5 + p4 * hc - p2 * xf**4) / (100 + p3 * hc)
        iterations += 1
        if iterations > 150:
            break
    tcl = 100 * xn - 273
    hl1 = 3.05e-3 * (5733 - 6.99 * mw - pa)
    hl2 = 0.42 * (mw - 58.15) if mw > 58.15 else 0.0
    hl3 = 1.7e-5 * m * (5867 - pa)
    hl4 = 0.0014 * m * (34 - ta)
    hl5 = 3.96 * fcl * (xn**4 - (tra / 100) ** 4)
    hl6 = fcl * hc * (tcl - ta)
    ts = 0.303 * math.exp(-0.036 * m) + 0.028
    pmv = ts * (mw - hl1 - hl2 - hl3 - hl4 - hl5 - hl6)
    ppd = 100.0 - 95.0 * math.exp(-0.03353 * pmv**4 - 0.2179 * pmv**2)
    return pmv, ppd


def pmv_ppd(
    ta: float, tr: float, vel: float, rh: float, met: float, clo: float
) -> tuple[float, float]:
    """ISO 7730 PMV/PPD. `vel` is the relative air speed in m/s."""
    return _pmv_ppd_cached(
        round(ta, 2),
        round(tr, 2),
        round(max(vel, 0.0), 3),
        round(rh, 1),
        round(met, 2),
        round(clo, 2),
    )


def relative_air_speed(air_speed: float, met: float) -> float:
    """ASHRAE 55: body movement adds 0.3 (met - 1) m/s above 1 met."""
    return air_speed + 0.3 * (met - 1.0) if met > 1.0 else air_speed


def dynamic_clo(clo: float, met: float) -> float:
    """ASHRAE 55: clothing insulation drops with activity above 1.2 met."""
    return clo * (0.6 + 0.4 / met) if met > 1.2 else clo


def occupant_pmv(
    ta: float, rh: float, air_speed: float, met: float, clo: float, radiant_delta_c: float = 0.0
) -> tuple[float, float]:
    """One occupant's PMV and PPD at an air temperature, the radiant temperature offset."""
    return pmv_ppd(
        ta, ta + radiant_delta_c, relative_air_speed(air_speed, met), rh, met, dynamic_clo(clo, met)
    )


def comfort_temperature(
    rh: float, occupants: Sequence[Occupant], speeds: Sequence[float], target_pmv: float
) -> float:
    """Air temperature at which the occupant-weighted mean PMV equals the target (bisection)."""
    total_weight = sum(o.weight for o in occupants) or 1.0

    def mean_pmv(ta: float) -> float:
        return (
            sum(
                o.weight * occupant_pmv(ta, rh, v, o.met, o.clo, o.radiant_delta_c)[0]
                for o, v in zip(occupants, speeds, strict=False)
            )
            / total_weight
        )

    low, high = 14.0, 34.0
    for _ in range(32):
        mid = (low + high) / 2
        if mean_pmv(mid) > target_pmv:
            high = mid
        else:
            low = mid
    return (low + high) / 2


def occupants(people: Sequence[PersonScene]) -> list[Occupant]:
    """A scene's people as occupants: the ensemble's clo (plus the chair's, seated) and met."""
    return [
        Occupant(
            weight=1.0,
            met=ACTIVITY_MET[p.activity],
            clo=CLOTHING_CLO[p.clothing_ensemble] + (CHAIR_CLO if p.posture == "seated" else 0.0),
        )
        for p in people
    ]


def comfort_setpoint(
    people: Sequence[PersonScene], rh_pct: float = DEFAULT_RH_PCT, target_pmv: float = 0.0
) -> float:
    """The air temperature at which these people's mean PMV is ``target_pmv``, in still air.

    Raises:
        ValueError: Without people: there is nobody to be comfortable.
    """
    if not people:
        raise ValueError("no people to be comfortable")
    found = occupants(people)
    return comfort_temperature(rh_pct, found, [STILL_AIR_MPS] * len(found), target_pmv)
