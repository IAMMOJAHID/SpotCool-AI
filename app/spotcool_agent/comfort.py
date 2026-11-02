import math
import statistics
from typing import Optional, Sequence

from .config import Calibration, ControlLimits
from .schemas import (
    COLD_CUES,
    DRAFT_CUES,
    HOT_CUES,
    Complaint,
    ManipulatedVariables,
    OccupantState,
    RewardBreakdown,
    TurnState,
)

# ISO 7730 validity ranges for the PMV inputs.
PMV_RANGES = {"ta": (10.0, 30.0), "tr": (10.0, 40.0), "vel": (0.0, 1.0), "met": (0.8, 4.0), "clo": (0.0, 2.0)}

CUE_SENSATION = {**{c: -1.5 for c in COLD_CUES + DRAFT_CUES}, **{c: 1.5 for c in HOT_CUES}}
FAN_OUTLET_SPEED_MS = {"low": 2.0, "med": 3.0, "auto": 3.0, "high": 4.0}
FAN_ENERGY_FACTOR = {"low": 0.6, "med": 0.8, "auto": 0.8, "high": 1.0}
JET_SIGMA_DEG = 12.0
VOICE_WEIGHT, ROOM_VOICE_WEIGHT, CUE_WEIGHT, APPARENT_WEIGHT = 3.0, 1.5, 2.0, 1.0


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def pmv_ppd(ta: float, tr: float, vel: float, rh: float, met: float, clo: float, wme: float = 0.0) -> tuple[float, float]:
    """ISO 7730:2005 Annex D. vel is the relative air speed (m/s); rh in percent."""
    pa = rh * 10 * math.exp(16.6536 - 4030.183 / (ta + 235))
    icl = 0.155 * clo
    m = met * 58.15
    mw = m - wme * 58.15
    fcl = 1 + 1.29 * icl if icl <= 0.078 else 1.05 + 0.645 * icl
    hcf = 12.1 * math.sqrt(vel)
    taa, tra = ta + 273, tr + 273
    tcla = taa + (35.5 - ta) / (3.5 * icl + 0.1)
    p1 = icl * fcl
    p2, p3, p4 = p1 * 3.96, p1 * 100, p1 * taa
    p5 = 308.7 - 0.028 * mw + p2 * (tra / 100) ** 4
    xn, xf, hc = tcla / 100, tcla / 50, hcf
    iterations = 0
    while abs(xn - xf) > 0.00015:
        xf = (xf + xn) / 2
        hcn = 2.38 * abs(100 * xf - taa) ** 0.25
        hc = max(hcf, hcn)
        xn = (p5 + p4 * hc - p2 * xf**4) / (100 + p3 * hc)
        iterations += 1
        if iterations > 150:
            raise ValueError("PMV clothing-temperature iteration did not converge")
    tcl = 100 * xn - 273
    hl1 = 3.05e-3 * (5733 - 6.99 * mw - pa)
    hl2 = 0.42 * (mw - 58.15) if mw > 58.15 else 0.0
    hl3 = 1.7e-5 * m * (5867 - pa)
    hl4 = 0.0014 * m * (34 - ta)
    hl5 = 3.96 * fcl * (xn**4 - (tra / 100) ** 4)
    hl6 = fcl * hc * (tcl - ta)
    ts = 0.303 * math.exp(-0.036 * m) + 0.028
    pmv = ts * (mw - hl1 - hl2 - hl3 - hl4 - hl5 - hl6)
    return pmv, ppd_from_pmv(pmv)


def ppd_from_pmv(pmv: float) -> float:
    return 100.0 - 95.0 * math.exp(-0.03353 * pmv**4 - 0.2179 * pmv**2)


def occupant_pmv(room_temp_c: float, air_speed_ms: float, rh: float, met: float, clo: float) -> tuple[float, float]:
    """PMV at room conditions; body movement adds 0.3·(met − 1) to the relative air speed."""
    inputs = {
        "ta": room_temp_c,
        "tr": room_temp_c,
        "vel": air_speed_ms + 0.3 * max(0.0, met - 1.0),
        "met": met,
        "clo": clo,
    }
    clipped = {k: _clip(v, *PMV_RANGES[k]) for k, v in inputs.items()}
    return pmv_ppd(clipped["ta"], clipped["tr"], clipped["vel"], _clip(rh, 0.0, 100.0), clipped["met"], clipped["clo"])


def draft_rating(ta: float, v: float, turbulence_pct: float = 40.0) -> float:
    """ISO 7730 draught rating (% dissatisfied); the model is valid up to 0.5 m/s."""
    v = min(v, 0.5)
    if v <= 0.05:
        return 0.0
    rating = (34 - ta) * (v - 0.05) ** 0.62 * (0.37 * v * turbulence_pct + 3.14)
    return _clip(rating, 0.0, 100.0)


def jet_speed(fan: str, angle_deg: Optional[float], distance_m: float, swing: bool, room_air_speed: float) -> float:
    """Approximate air speed at an occupant from the AC jet (used to score A_{t-1} only)."""
    if angle_deg is None:
        return room_air_speed
    outlet = FAN_OUTLET_SPEED_MS.get(fan, 3.0) * (0.5 if swing else 1.0)
    speed = outlet * min(1.0, 1.0 / max(distance_m, 0.1)) * math.exp(-(angle_deg**2) / (2 * JET_SIGMA_DEG**2))
    return max(room_air_speed, speed)


def update_room_temp(previous: float, action: ManipulatedVariables, dt_s: float, cal: Calibration) -> float:
    """First-order lag toward the active setpoint (or toward ambient with the compressor off)."""
    target = cal.ambient_temp_c if action.compressor_mode == "fan_only" else action.target_temp_c
    return previous + (1 - math.exp(-max(dt_s, 0.0) / cal.room_tau_s)) * (target - previous)


def fuse_sensation(occupant: OccupantState, complaints: Sequence[Complaint]) -> tuple[float, bool]:
    """Observed thermal sensation in [-3, 3]: attributed voice w3, room-level voice w1.5,
    visual cues w2, Spark's apparent sensation w1. Returns (0, False) with nothing observed."""
    total = weight = 0.0
    for complaint in complaints:
        if complaint.sensation is None:
            continue
        if occupant.occupant_key in complaint.attributed_to:
            w = VOICE_WEIGHT
        elif not complaint.attributed_to:
            w = ROOM_VOICE_WEIGHT
        else:
            continue
        total += w * complaint.sensation
        weight += w
    cue_values = [CUE_SENSATION[c] for c in occupant.cues if c in CUE_SENSATION]
    if cue_values:
        total += CUE_WEIGHT * statistics.fmean(cue_values)
        weight += CUE_WEIGHT
    if occupant.apparent_sensation is not None:
        total += APPARENT_WEIGHT * occupant.apparent_sensation
        weight += APPARENT_WEIGHT
    if weight == 0:
        return 0.0, False
    return _clip(total / weight, -3.0, 3.0), True


def room_discomfort(occupants: Sequence[OccupantState], complaints: Sequence[Complaint]) -> float:
    """D_t: mean |s_i| over occupants; with nobody in view, the complaints themselves."""
    if occupants:
        return statistics.fmean(abs(o.observed_sensation) for o in occupants)
    voiced = [abs(c.sensation) for c in complaints if c.sensation is not None]
    return statistics.fmean(voiced) if voiced else 0.0


def energy_proxy(action: ManipulatedVariables) -> float:
    fan = FAN_ENERGY_FACTOR.get(action.fan_speed, 0.8)
    if action.compressor_mode == "fan_only":
        return 0.1 * fan
    cooling = _clip((27.0 - action.target_temp_c) / 5.0, 0.1, 1.0)
    return (1.0 if action.compressor_mode == "cool" else 0.4) * cooling * fan


def grounded_reward(
    state: TurnState, previous: Optional[TurnState], previous_action: ManipulatedVariables
) -> RewardBreakdown:
    """R_det from observed comfort only, so the controller cannot reward its own setpoint."""
    discomfort = state.discomfort
    delta, matched = 0.0, 0
    if previous is not None:
        before = {o.occupant_key: abs(o.observed_sensation) for o in previous.occupants}
        pairs = [(before[o.occupant_key], abs(o.observed_sensation)) for o in state.occupants if o.occupant_key in before]
        matched = len(pairs)
        if pairs:
            delta = statistics.fmean(b - a for b, a in pairs)
    energy = energy_proxy(previous_action)
    complaint_penalty = 0.3 if state.complaints else 0.0
    score = delta / 3 - 0.5 * discomfort / 3 - energy * (0.2 if state.occupied else 0.6) - complaint_penalty
    return RewardBreakdown(
        score=round(_clip(score, -1.0, 1.0), 4),
        delta_discomfort=round(delta, 4),
        discomfort=round(discomfort, 4),
        energy=round(energy, 4),
        complaint_penalty=complaint_penalty,
        matched_occupants=matched,
        occupied=state.occupied,
    )


def pmv_neutral_setpoint(
    occupants: Sequence[OccupantState], cal: Calibration, limits: ControlLimits, low: float = 22.0, high: float = 28.0
) -> float:
    """Setpoint on the grid whose steady-state mean |PMV| over occupants is smallest."""
    step = limits.temp_grid_c
    best_temp, best_cost = 24.0, math.inf
    temp = low
    while temp <= high + 1e-9:
        cost = statistics.fmean(
            abs(occupant_pmv(temp, cal.room_air_speed_ms, cal.rh_pct, o.activity_met, o.clothing_clo)[0])
            for o in occupants
        )
        if cost < best_cost - 1e-9:
            best_temp, best_cost = temp, cost
        temp += step
    return best_temp
