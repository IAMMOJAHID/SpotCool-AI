"""VrfUnit: the meeting-room VRF indoor unit, its louvers, its room and its power.

Pure and deterministic: no asyncio and no clock. ``advance(dt_s)`` moves the unit on by
``dt_s`` wall seconds, and the command attributes take the commands in force (the gateway sets
them from its priority arrays). The unit starts at its gateway's relinquish defaults (On, Cool,
24 degC, fan Auto, both louvers Pos3), with its louvers at 0 deg and the room at ambient.

- **Louvers.** A position (``Pos1``-``Pos5``) is the profile's ``angles_deg`` for it; the angle
  moves toward it at ``SLEW_DEG_S`` at most. ``Swing`` sweeps the axis's whole range at the
  same rate, reversing at each end. A state the brand lacks (LG's ``Pos3``, its relinquish
  default) holds the angle with swing off. An axis the brand does not expose has no angle.
- **Off.** The louvers hold, swing stops and the room drifts toward ambient.
- **Room.** First order, time constant ``ROOM_TAU_S`` of wall time. In Cool, Dry and Auto it
  goes toward the setpoint (ambient if that is cooler), never past it: thermo-off holds the
  setpoint. In Fan mode, or off, it goes toward ``AMBIENT_C``.
- **Power.** A compressor term, ``COMPRESSOR_KW_PER_K`` per kelvin of room above the setpoint
  times the fan's level, capped at ``RATED_INPUT_KW``, plus the fan's ``FAN_KW`` while the
  unit is on. Every power constant is illustrative, and VRF power is never quoted as a saving.
"""

import math

from spotcool_contracts.events import VrfStateData
from spotcool_contracts.vrf import VaneAxis, VrfProfile

SLEW_DEG_S = 15.0
"""Fastest louver motion, degrees per second, as real louver motors."""

MAX_DT_S = 0.1
"""Longest step ``advance`` takes; a longer one (a stalled caller) is clipped, never a jump."""

SWING = "Swing"
"""The louver state that sweeps the axis."""

SWING_ONLY_RANGE_DEG = (-60.0, 0.0)
"""Sweep of an axis with swing but no positions (LG's vertical louver). Illustrative: the other
brands' vertical range."""

AMBIENT_C = 30.0
"""Where the room drifts with no cooling: the meeting room in summer. The room starts here."""

ROOM_TAU_S = 120.0
"""Time constant of the room's temperature, in wall seconds."""

COOLING_MODES = ("Cool", "Dry", "Auto")
"""Modes that cool the room; Fan only moves air."""

RATED_INPUT_KW = 2.2
"""Compressor input at full load, kW: about a 7 kW cooling unit. Illustrative."""

COMPRESSOR_KW_PER_K = 0.5
"""Compressor input per kelvin of room above the setpoint, at fan level 1. Illustrative."""

FAN_LEVELS = {"Auto": 0.8, "Low": 0.5, "Mid": 0.8, "High": 1.0}
"""How much of the compressor term each fan speed lets through; Auto runs as Mid. Illustrative."""

FAN_KW = {"Auto": 0.05, "Low": 0.03, "Mid": 0.05, "High": 0.08}
"""Indoor fan input at each speed, kW; Auto runs as Mid. Illustrative."""


class _Louver:
    """One louver axis: its angle, the target its state sets, and the direction of its sweep."""

    def __init__(self, axis: VaneAxis | None) -> None:
        self.axis = axis
        self.angle: float | None = None if axis is None else 0.0
        angles = axis.angles_deg if axis is not None else []
        self.low, self.high = (min(angles), max(angles)) if angles else SWING_ONLY_RANGE_DEG
        self.sweep = -1.0  # a sweep starts toward the low end: down, or left

    def advance(self, state: str, dt_s: float) -> None:
        """Move the louver on by ``dt_s`` seconds in ``state``."""
        if self.axis is None or self.angle is None:
            return
        step = SLEW_DEG_S * dt_s
        if state == SWING and self.axis.swing:
            self.angle = min(max(self.angle + self.sweep * step, self.low), self.high)
            if self.angle in (self.low, self.high):  # at an end: back the other way
                self.sweep = 1.0 if self.angle == self.low else -1.0
            return
        target = self.target(state)
        if target is not None:
            self.angle += min(max(target - self.angle, -step), step)

    def target(self, state: str) -> float | None:
        """The angle of a position the axis has; ``None`` for any other state (hold)."""
        assert self.axis is not None
        number = state.removeprefix("Pos")
        if state.startswith("Pos") and number.isdigit() and 1 <= int(number) <= self.axis.positions:
            return self.axis.angles_deg[int(number) - 1]
        return None


class VrfUnit:
    """One VRF indoor unit and its room (see the module docstring).

    Set the command attributes to the commands in force; ``advance`` moves the unit on.

    Args:
        profile: The brand's gateway profile: which louver axes and positions exist.

    Raises:
        ValueError: If the profile has no vertical louver (``VrfStateData`` needs one).
    """

    def __init__(self, profile: VrfProfile) -> None:
        if profile.vane_vertical is None:
            raise ValueError(f"{profile.vendor}: a VRF profile needs a vertical louver")
        self.profile = profile
        """The brand's gateway profile."""
        self.power_on = True
        """Command: the unit is on."""
        self.mode = "Cool"
        """Command: ``Cool``, ``Dry``, ``Fan`` or ``Auto``."""
        self.setpoint_c = 24.0
        """Command: the cooling setpoint, degC."""
        self.fan = "Auto"
        """Command: ``Auto``, ``Low``, ``Mid`` or ``High``."""
        self.vane_v = "Pos3"
        """Command: the vertical louver's state, ``Pos1``-``Pos5`` or ``Swing``."""
        self.vane_h = "Pos3"
        """Command: the horizontal louver's state, ``Pos1``-``Pos5`` or ``Swing``."""
        self.room_c = AMBIENT_C
        """The room's temperature at the unit's return-air sensor, degC."""
        self._v = _Louver(profile.vane_vertical)
        self._h = _Louver(profile.vane_horizontal)

    @property
    def vane_v_angle_deg(self) -> float:
        """The vertical louver's angle: 0 is horizontal, negative is downward."""
        assert self._v.angle is not None  # the profile has a vertical louver
        return self._v.angle

    @property
    def vane_h_angle_deg(self) -> float | None:
        """The horizontal louver's angle (negative is left), or ``None`` if the brand has none."""
        return self._h.angle

    @property
    def cooling(self) -> bool:
        """Whether the unit is on in a mode that cools."""
        return self.power_on and self.mode in COOLING_MODES

    @property
    def power_kw(self) -> float:
        """Electric input of the indoor and outdoor unit for this indoor unit, kW (illustrative)."""
        if not self.power_on:
            return 0.0
        compressor = 0.0
        if self.cooling:
            above_k = max(0.0, self.room_c - self.setpoint_c)
            compressor = min(RATED_INPUT_KW, COMPRESSOR_KW_PER_K * FAN_LEVELS[self.fan] * above_k)
        return compressor + FAN_KW[self.fan]

    def advance(self, dt_s: float) -> None:
        """Move the unit on by ``dt_s`` wall seconds, clipped to ``MAX_DT_S``.

        Args:
            dt_s: Wall seconds since the last call.

        Raises:
            ValueError: If ``dt_s`` is negative.
        """
        if dt_s < 0:
            raise ValueError(f"dt_s must not be negative, got {dt_s}")
        dt_s = min(dt_s, MAX_DT_S)
        if self.power_on:  # off, the louvers hold
            self._v.advance(self.vane_v, dt_s)
            self._h.advance(self.vane_h, dt_s)
        target = min(self.setpoint_c, AMBIENT_C) if self.cooling else AMBIENT_C
        self.room_c += (target - self.room_c) * -math.expm1(-dt_s / ROOM_TAU_S)

    def state(self) -> VrfStateData:
        """The unit's state as the dashboard gets it, rounded as it is published.

        Angles to 0.1 deg, temperatures to 0.01 degC and power to 0.001 kW. An axis the brand
        does not expose has no state and no angle.
        """
        h = self.vane_h_angle_deg
        return VrfStateData(
            profile=self.profile.vendor,
            power_on=self.power_on,
            mode=self.mode,
            setpoint_c=self.setpoint_c,
            fan=self.fan,
            vane_v_pos=self.vane_v,
            vane_h_pos=None if h is None else self.vane_h,
            vane_v_angle_deg=_rounded(self.vane_v_angle_deg, 1),
            vane_h_angle_deg=None if h is None else _rounded(h, 1),
            room_temp_c=_rounded(self.room_c, 2),
            power_kw=_rounded(self.power_kw, 3),
        )


def _rounded(value: float, digits: int) -> float:
    return round(value, digits) + 0.0  # + 0.0 turns -0.0 into 0.0
