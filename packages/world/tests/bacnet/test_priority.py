import math
import subprocess
import sys

import pytest

from spotcool_contracts import load_point_map
from spotcool_contracts.priorities import AGENT_PRIORITY, OPERATOR_PRIORITY
from spotcool_world.transports.bacnet.priority import (
    PRIORITY_LEVELS,
    CommandPolicy,
    PointValue,
    PriorityArray,
)

MAP = load_point_map("office_bms")
SETPOINT = MAP.by_name("ZN-COR-CLG-SP")  # analog, 16..30 on a 0.5 grid
SENSOR = MAP.by_name("ZN-COR-OCC-SENSOR")  # binary, [Unoccupied, Occupied]
TEMP = MAP.by_name("ZN-COR-T")  # read-only
POLICY = CommandPolicy()
PRIORITIES = range(1, PRIORITY_LEVELS + 1)


def test_an_empty_array_gives_the_relinquish_default() -> None:
    array = PriorityArray()
    assert array.slots == (None,) * 16
    assert array.effective(24.0) == (24.0, None)
    assert array.effective("Occupied") == ("Occupied", None)


@pytest.mark.parametrize("priority", PRIORITIES)
def test_one_slot_alone_wins_at_its_priority(priority: int) -> None:
    array = PriorityArray()
    array.write(26.0, priority)
    assert array.effective(24.0) == (26.0, priority)
    assert [i + 1 for i, slot in enumerate(array.slots) if slot is not None] == [priority]


@pytest.mark.parametrize(
    ("urgent", "lax"), [(a, b) for a in PRIORITIES for b in PRIORITIES if a < b]
)
def test_the_most_urgent_slot_wins_whatever_the_write_order(urgent: int, lax: int) -> None:
    for order in ((urgent, lax), (lax, urgent)):
        array = PriorityArray()
        for priority in order:
            array.write(float(priority), priority)
        assert array.effective(0.0) == (float(urgent), urgent)


def test_a_release_empties_only_its_own_slot() -> None:
    array = PriorityArray()
    array.write(27.0, OPERATOR_PRIORITY)
    array.write(26.5, AGENT_PRIORITY)
    array.release(AGENT_PRIORITY)
    assert array.effective(24.0) == (27.0, OPERATOR_PRIORITY)
    array.write(26.5, AGENT_PRIORITY)
    array.release(OPERATOR_PRIORITY)
    assert array.effective(24.0) == (26.5, AGENT_PRIORITY)
    array.release(AGENT_PRIORITY)
    array.release(AGENT_PRIORITY)  # releasing an empty slot changes nothing
    assert array.effective(24.0) == (24.0, None)


def test_an_operator_at_8_beats_the_agent_at_10_which_cannot_displace_it() -> None:
    array = PriorityArray()
    array.write(27.0, OPERATOR_PRIORITY)
    array.write(26.5, AGENT_PRIORITY)  # stored in slot 10, not in control
    assert array.effective(24.0) == (27.0, OPERATOR_PRIORITY)
    assert (array.slots[7], array.slots[9]) == (27.0, 26.5)
    array.release(OPERATOR_PRIORITY)  # the operator lets go: the agent's value applies
    assert array.effective(24.0) == (26.5, AGENT_PRIORITY)


@pytest.mark.parametrize("priority", [0, 17])
def test_slots_outside_1_to_16_do_not_exist(priority: int) -> None:
    with pytest.raises(ValueError, match="1-16"):
        PriorityArray().write(1.0, priority)
    with pytest.raises(ValueError, match="1-16"):
        PriorityArray().release(priority)


@pytest.mark.parametrize("priority", range(1, OPERATOR_PRIORITY))
def test_slots_1_to_7_are_refused_over_the_network(priority: int) -> None:
    for value in (26.0, None):  # a write and a release alike
        rejection = POLICY.check(SETPOINT, value, priority)
        assert rejection is not None
        assert rejection.error_code == "write-access-denied"
        assert f"priority {priority} is reserved" in rejection.reason


@pytest.mark.parametrize("priority", range(OPERATOR_PRIORITY, PRIORITY_LEVELS + 1))
def test_operators_the_agent_and_schedules_may_command(priority: int) -> None:
    assert POLICY.check(SETPOINT, 26.0, priority) is None
    assert POLICY.check(SETPOINT, None, priority) is None


@pytest.mark.parametrize("priority", [0, 17, -3])
def test_a_priority_outside_1_to_16_is_refused(priority: int) -> None:
    rejection = POLICY.check(SETPOINT, 26.0, priority)
    assert rejection is not None and "is not 1-16" in rejection.reason


def test_read_only_points_refuse_every_command() -> None:
    assert POLICY.check(TEMP, 21.0, AGENT_PRIORITY) == (
        "ZN-COR-T is read-only",
        "write-access-denied",
    )
    assert POLICY.check(TEMP, None, OPERATOR_PRIORITY) is not None


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (15.5, "outside ZN-COR-CLG-SP's range 16.0..30.0"),
        (30.5, "outside"),
        (-40.0, "outside"),
        (26.3, "not a multiple of 0.5"),
        (26.25, "not a multiple of 0.5"),
        (math.nan, "not a finite number"),
        (math.inf, "not a finite number"),
        ("26", "not a finite number"),
    ],
)
def test_analog_values_off_range_or_off_grid_are_rejected_not_clamped(
    value: PointValue, reason: str
) -> None:
    rejection = POLICY.check(SETPOINT, value, AGENT_PRIORITY)
    assert rejection is not None
    assert reason in rejection.reason
    assert rejection.error_code == "value-out-of-range"


@pytest.mark.parametrize("value", [16.0, 16.5, 24.0, 26.5, 30.0])
def test_analog_values_on_the_grid_and_in_range_are_accepted(value: float) -> None:
    assert POLICY.check(SETPOINT, value, AGENT_PRIORITY) is None


def test_binary_values_must_be_one_of_the_state_texts() -> None:
    assert POLICY.check(SENSOR, "Unoccupied", AGENT_PRIORITY) is None
    assert POLICY.check(SENSOR, "Occupied", OPERATOR_PRIORITY) is None
    for value in ("Away", 1.0):
        rejection = POLICY.check(SENSOR, value, AGENT_PRIORITY)
        assert rejection is not None and "is not a state of ZN-COR-OCC-SENSOR" in rejection.reason


def test_the_pure_modules_import_without_the_bacnet_extra() -> None:
    script = (
        "import sys; sys.modules['bacpypes3'] = None; "
        "import spotcool_world.transports.bacnet.priority, spotcool_world.transports.bacnet.bms_logic"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
