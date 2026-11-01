"""BACnet command priorities: the 16-slot priority array and the BMS's network command policy.

Pure logic, no sockets. A commandable point holds 16 priority slots; the most urgent non-empty
slot gives its present value, and with every slot empty the relinquish default applies
(ASHRAE 135 clause 19.2). Values are in BMS units: a number for analog points, a state text
for binary and multi-state ones.
"""

import math
from typing import NamedTuple

from spotcool_contracts.points import ObjectType, PointSpec
from spotcool_contracts.priorities import OPERATOR_PRIORITY

PointValue = float | str
"""A point's value in BMS units: a number (analog) or a state text (binary, multi-state)."""

PRIORITY_LEVELS = 16
"""Slots in a priority array; 1 is the most urgent."""

ANALOG_TYPES = (ObjectType.ANALOG_INPUT, ObjectType.ANALOG_VALUE)
"""Object types whose values are numbers; every other type holds a state text."""


class PriorityArray:
    """The 16 command slots of one commandable point.

    A write or release at priority ``p`` changes slot ``p`` only. So a less urgent writer
    never overwrites a more urgent slot: an agent writing at 10 while an operator holds 8
    fills slot 10, which takes effect once slot 8 is released.
    """

    def __init__(self) -> None:
        self._slots: list[PointValue | None] = [None] * PRIORITY_LEVELS

    @property
    def slots(self) -> tuple[PointValue | None, ...]:
        """Every slot, priority 1 first; ``None`` is an empty (NULL) slot."""
        return tuple(self._slots)

    def write(self, value: PointValue, priority: int) -> None:
        """Command ``value`` at ``priority``.

        Args:
            value: The commanded value.
            priority: Slot to write, 1 (most urgent) to 16.

        Raises:
            ValueError: If ``priority`` is not 1-16.
        """
        self._slots[_index(priority)] = value

    def release(self, priority: int) -> None:
        """Empty the slot at ``priority`` (a NULL write).

        Args:
            priority: Slot to release, 1 to 16.

        Raises:
            ValueError: If ``priority`` is not 1-16.
        """
        self._slots[_index(priority)] = None

    def effective(
        self, relinquish_default: PointValue | None
    ) -> tuple[PointValue | None, int | None]:
        """The present value and the priority it comes from.

        Args:
            relinquish_default: The value that applies when every slot is empty.

        Returns:
            ``(value, priority)`` from the most urgent non-empty slot, or
            ``(relinquish_default, None)`` when every slot is empty.
        """
        for index, value in enumerate(self._slots):
            if value is not None:
                return value, index + 1
        return relinquish_default, None


class Rejection(NamedTuple):
    """Why the BMS refused a network command.

    Parameters:
        reason: Plain-language reason, for the ``BmsWriteFrame`` and the logs.
        error_code: The BACnet error code to answer with (``write-access-denied`` or
            ``value-out-of-range``).
    """

    reason: str
    error_code: str


class CommandPolicy:
    """What the BMS accepts over the network, as a BACnet write or release of a present value.

    - Priorities 1-7 (life safety, critical equipment, minimum on/off) are reserved for the
      building's own safety logic: network commands use 8 (operators) to 16. SpotCool's agent
      writes at 10 (``spotcool_contracts.priorities``).
    - Read-only points refuse every command.
    - Analog values must be finite, within the point's ``min_value``..``max_value`` and on its
      ``resolution`` grid. Anything else is rejected, never clamped.
    - Binary and multi-state values must be one of the point's state texts.

    A command only ever touches its own slot (``PriorityArray``), so no writer displaces a
    more urgent one.
    """

    def check(self, point: PointSpec, value: PointValue | None, priority: int) -> Rejection | None:
        """Decide whether to accept a command.

        Args:
            point: The target point.
            value: The commanded value in BMS units, or ``None`` for a release (NULL).
            priority: The command's priority.

        Returns:
            ``None`` if the command is accepted, otherwise why it is rejected.
        """
        if not 1 <= priority <= PRIORITY_LEVELS:
            # BACnet's WriteProperty priority is any integer. The BMS answers VALUE_OUT_OF_RANGE;
            # its BmsWriteFrame clamps the priority to 1-16, and this reason names the real one.
            return Rejection(f"priority {priority} is not 1-16", "value-out-of-range")
        if priority < OPERATOR_PRIORITY:
            return Rejection(
                f"priority {priority} is reserved for life safety and critical equipment; "
                f"network commands use {OPERATOR_PRIORITY}-{PRIORITY_LEVELS}",
                "write-access-denied",
            )
        if not point.is_commandable:
            return Rejection(f"{point.name} is read-only", "write-access-denied")
        if value is None:
            return None
        if point.object_type in ANALOG_TYPES:
            return _check_analog(point, value)
        if value not in (point.states or []):
            return Rejection(
                f"{value!r} is not a state of {point.name} ({point.states})", "value-out-of-range"
            )
        return None


def _check_analog(point: PointSpec, value: PointValue) -> Rejection | None:
    if isinstance(value, str) or not math.isfinite(value):
        return Rejection(f"{value!r} is not a finite number", "value-out-of-range")
    low, high = point.min_value, point.max_value
    if (low is not None and value < low) or (high is not None and value > high):
        return Rejection(
            f"{value:g} is outside {point.name}'s range {low}..{high}", "value-out-of-range"
        )
    step = point.resolution
    # The tolerance absorbs float32 rounding only: BACnet sends analog values as REAL.
    if step and not math.isclose(value, round(value / step) * step, rel_tol=1e-6, abs_tol=1e-9):
        return Rejection(f"{value:g} is not a multiple of {step:g}", "value-out-of-range")
    return None


def _index(priority: int) -> int:
    if not 1 <= priority <= PRIORITY_LEVELS:
        raise ValueError(f"priority must be 1-{PRIORITY_LEVELS}, got {priority}")
    return priority - 1
