from spotcool_world.transports.bacnet.bms_logic import (
    STANDBY_DELAY_S,
    HeartbeatWatchdog,
    OccupancyMode,
    ZoneStandby,
)

T0 = 198 * 86_400 + 10 * 3600  # 18 July, 10:00
OCCUPIED_SP, UNOCCUPIED_SP = 24.0, 30.0  # the schedule's cooling setpoints, degC
HEATING_SP = 20.0


def vacant_zone(at_s: float | None = T0) -> ZoneStandby:
    """A zone in scheduled occupancy whose sensor reads Unoccupied from ``at_s``."""
    zone = ZoneStandby()
    zone.schedule(OCCUPIED_SP)
    zone.sensor(False, at_s)
    return zone


def standby_zone() -> ZoneStandby:
    zone = vacant_zone()
    zone.tick(T0 + STANDBY_DELAY_S)
    assert zone.standby
    return zone


def test_standby_starts_after_5_minutes_not_after_4() -> None:
    zone = vacant_zone()
    zone.tick(T0 + 240)
    assert (zone.standby, zone.mode) == (False, OccupancyMode.OCCUPIED)
    zone.tick(T0 + 300)
    assert (zone.standby, zone.mode) == (True, OccupancyMode.STANDBY)


def test_standby_needs_scheduled_occupancy() -> None:
    zone = ZoneStandby()
    assert zone.mode is OccupancyMode.UNOCCUPIED  # no schedule yet
    zone.schedule(UNOCCUPIED_SP)  # night
    zone.sensor(False, T0)
    zone.tick(T0 + 900)
    assert (zone.standby, zone.mode) == (False, OccupancyMode.UNOCCUPIED)
    zone.schedule(OCCUPIED_SP)  # the schedule starts; the zone has been empty all along
    assert zone.mode is OccupancyMode.OCCUPIED
    zone.tick(T0 + 1800)
    assert zone.mode is OccupancyMode.STANDBY
    zone.schedule(UNOCCUPIED_SP)  # the schedule ends: so does standby
    assert (zone.standby, zone.mode) == (False, OccupancyMode.UNOCCUPIED)
    assert zone.damper(22.0, HEATING_SP, UNOCCUPIED_SP) is None


def test_an_occupied_reading_ends_standby_at_once_and_resets_the_timer() -> None:
    zone = standby_zone()
    assert zone.damper(24.0, HEATING_SP, 24.5) == 0.0
    zone.sensor(True, T0 + 400)
    assert (zone.standby, zone.mode) == (False, OccupancyMode.OCCUPIED)
    assert zone.damper(24.0, HEATING_SP, 24.5) is None  # leaving standby releases the damper
    zone.sensor(False, T0 + 500)
    zone.tick(T0 + 700)  # 200 s after the zone emptied again
    assert not zone.standby
    zone.tick(T0 + 800)
    assert zone.standby


def test_a_repeated_unoccupied_reading_keeps_the_timer_running() -> None:
    zone = vacant_zone()
    zone.sensor(False, T0 + 200)
    zone.tick(T0 + 300)
    assert zone.standby


def test_a_reading_before_the_clock_is_known_starts_the_timer_at_the_next_tick() -> None:
    zone = vacant_zone(at_s=None)
    zone.tick(T0)
    assert not zone.standby
    zone.tick(T0 + 300)
    assert zone.standby


def test_standby_raises_the_cooling_setpoint_half_a_kelvin_above_the_schedule() -> None:
    zone = vacant_zone()
    assert zone.cooling_setpoint(OCCUPIED_SP) == OCCUPIED_SP
    zone.tick(T0 + 300)
    assert zone.cooling_setpoint(OCCUPIED_SP) == OCCUPIED_SP + 0.5


def test_the_damper_shuts_inside_the_band_reopens_at_a_setpoint_and_holds_between() -> None:
    zone = standby_zone()
    cooling = 24.5  # the standby cooling setpoint; heating 20.0 is set back to 19.5
    assert zone.damper(24.2, HEATING_SP, cooling) is None  # only 0.3 K inside: stays released
    assert zone.damper(24.0, HEATING_SP, cooling) == 0.0  # 0.5 K inside both: shut off
    assert zone.damper(24.3, HEATING_SP, cooling) == 0.0  # between: the hysteresis holds
    assert zone.damper(24.5, HEATING_SP, cooling) is None  # reached the cooling setpoint
    assert zone.damper(24.3, HEATING_SP, cooling) is None  # between: stays released
    assert zone.damper(22.0, HEATING_SP, cooling) == 0.0
    assert zone.damper(19.8, HEATING_SP, cooling) == 0.0  # 0.3 K above 19.5: holds
    assert zone.damper(19.5, HEATING_SP, cooling) is None  # reached the set-back heating setpoint
    assert zone.damper(19.9, HEATING_SP, cooling) is None  # 0.4 K inside: stays released
    assert zone.damper(20.0, HEATING_SP, cooling) == 0.0  # 0.5 K inside again


def test_a_zone_held_at_the_schedule_setpoint_shuts_off_when_standby_raises_it() -> None:
    zone = standby_zone()
    held_c = (OCCUPIED_SP + 273.15 + 1e-12) - 273.15  # 24 degC, as a K -> degC float
    assert held_c != OCCUPIED_SP
    assert zone.damper(held_c, HEATING_SP, zone.cooling_setpoint(OCCUPIED_SP)) == 0.0


def test_an_agent_setpoint_widens_the_band() -> None:
    zone = standby_zone()
    assert zone.damper(27.0, HEATING_SP, 28.0) == 0.0  # the agent commanded 28 degC
    assert zone.damper(27.9, HEATING_SP, 28.0) == 0.0
    assert zone.damper(28.0, HEATING_SP, 28.0) is None


def test_out_of_standby_the_damper_is_always_released() -> None:
    zone = vacant_zone()
    assert zone.damper(22.0, HEATING_SP, OCCUPIED_SP) is None


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_the_watchdog_arms_on_the_first_heartbeat_and_expires_after_120_s() -> None:
    clock = FakeClock()
    watchdog = HeartbeatWatchdog(clock=clock)
    clock.now += 1000
    assert not watchdog.expired()  # never written, never expires
    watchdog.beat("On")
    clock.now += 119.9
    assert not watchdog.expired()
    clock.now += 0.1
    assert watchdog.expired()
    assert not watchdog.armed
    clock.now += 1000
    assert not watchdog.expired()  # disarmed until the next heartbeat
    watchdog.beat("On")
    assert watchdog.armed


def test_a_toggle_restarts_the_watchdog_and_a_repeated_value_does_not() -> None:
    clock = FakeClock()
    watchdog = HeartbeatWatchdog(clock=clock)
    watchdog.beat("On")
    clock.now += 100
    watchdog.beat("Off")  # changed: 120 s from here
    clock.now += 100
    watchdog.beat("Off")  # written again but unchanged
    assert not watchdog.expired()
    clock.now += 20
    assert watchdog.expired()
