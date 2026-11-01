import math

import pytest

from spotcool_contracts import load_point_map
from spotcool_contracts.points import Conversion
from spotcool_world.adapters.units import to_bms, to_boptest


@pytest.mark.parametrize(
    ("conversion", "boptest", "bms"),
    [
        (Conversion.NONE, 412.0, 412.0),
        (Conversion.KELVIN_TO_CELSIUS, 297.15, 24.0),
        (Conversion.WATT_TO_KILOWATT, 1500.0, 1.5),
        (Conversion.M3S_TO_LPS, 0.25, 250.0),
        (Conversion.FRACTION_TO_PERCENT, 0.42, 42.0),
    ],
)
def test_known_values(conversion: Conversion, boptest: float, bms: float) -> None:
    assert math.isclose(to_bms(boptest, conversion), bms)
    assert math.isclose(to_boptest(bms, conversion), boptest)


@pytest.mark.parametrize("conversion", list(Conversion))
@pytest.mark.parametrize("value", [0.0, 1.0, 296.65, 12_345.6])
def test_round_trip(conversion: Conversion, value: float) -> None:
    assert math.isclose(to_boptest(to_bms(value, conversion), conversion), value, abs_tol=1e-9)


def test_every_bms_point_conversion_is_supported() -> None:
    for point in load_point_map("office_bms").points:
        if point.boptest is not None:
            to_bms(1.0, point.boptest.conversion)
