"""Unit conversion between BOPTEST values and the BMS (BACnet) values the agent sees.

BOPTEST speaks SI base units (K, W, m3/s, fractions); the BMS speaks building units (degC,
kW, L/s, %). Each point in the contract's point map names its ``Conversion``.
"""

from typing import assert_never

from spotcool_contracts.points import Conversion

KELVIN_OFFSET = 273.15


def to_bms(value: float, conversion: Conversion) -> float:
    """Convert a BOPTEST value to the BMS unit.

    Args:
        value: Value in BOPTEST units.
        conversion: The point's conversion.

    Returns:
        The value in BMS units.
    """
    match conversion:
        case Conversion.NONE:
            return value
        case Conversion.KELVIN_TO_CELSIUS:
            return value - KELVIN_OFFSET
        case Conversion.WATT_TO_KILOWATT:
            return value / 1000
        case Conversion.M3S_TO_LPS:
            return value * 1000
        case Conversion.FRACTION_TO_PERCENT:
            return value * 100
        case _:
            assert_never(conversion)


def to_boptest(value: float, conversion: Conversion) -> float:
    """Convert a BMS value back to the BOPTEST unit (the inverse of ``to_bms``).

    Args:
        value: Value in BMS units.
        conversion: The point's conversion.

    Returns:
        The value in BOPTEST units.
    """
    match conversion:
        case Conversion.NONE:
            return value
        case Conversion.KELVIN_TO_CELSIUS:
            return value + KELVIN_OFFSET
        case Conversion.WATT_TO_KILOWATT:
            return value * 1000
        case Conversion.M3S_TO_LPS:
            return value / 1000
        case Conversion.FRACTION_TO_PERCENT:
            return value / 100
        case _:
            assert_never(conversion)
