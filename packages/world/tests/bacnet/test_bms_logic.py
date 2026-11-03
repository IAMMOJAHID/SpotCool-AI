from spotcool_world.transports.bacnet.bms_logic import (
    STANDBY_DELAY_S,
    HeartbeatWatchdog,
    OccupancyMode,
    ZoneStandby,
)

DAY_S = 198 * 86_400  # 18 July, 00:00
OCCUPIED_SP, UNOCCUPIED_SP = 24.0, 30.0  # the schedule's cooling setpoints, degC
HEATING_SP = 20.0


def at(hour: int, minute: int = 0, *, day: int = 0) -> float:
    return DAY_S + day * 86_400 + hour * 3600 + minute * 60


T0 = at(10)


def vacant_zone(at_s: float | None = T0) -> ZoneStandby:
    """A zone in scheduled occupancy whose sensor reads Unoccupied from ``at_s``."""
    zone = ZoneStandby()
    zone.schedule(OCCUPIED_SP, T0 if at_s is None else at_s)
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


def test_standby_starts_at_06_15_at_the_earliest() -> None:
    zone = ZoneStandby()
    assert zone.mode is OccupancyMode.UNOCCUPIED  # no schedule yet
    zone.sensor(False, at(5))  # empty since before dawn
    zone.schedule(UNOCCUPIED_SP, at(5, 45))
    zone.tick(at(5, 45))
    assert zone.mode is OccupancyMode.UNOCCUPIED
    zone.schedule(UNOCCUPIED_SP, at(6))  # the 06:00 stamp still reports the night's setpoint
    zone.tick(at(6))
    assert zone.mode is OccupancyMode.UNOCCUPIED
    zone.schedule(OCCUPIED_SP, at(6, 15))
    assert zone.mode is OccupancyMode.OCCUPIED
    zone.tick(at(6, 15))
    assert zone.mode is OccupancyMode.STANDBY


def test_standby_ends_at_19_00_though_the_baseline_still_reads_the_day_setpoint() -> None:
    zone = vacant_zone(at(18))
    zone.tick(at(18, 15))
    assert zone.standby
    zone.schedule(OCCUPIED_SP, at(19))  # the 19:00 stamp reports 18:45-19:00's setpoint
    zone.tick(at(19))
    assert (zone.standby, zone.mode) == (False, OccupancyMode.UNOCCUPIED)
    assert zone.cooling_setpoint(OCCUPIED_SP) == OCCUPIED_SP  # no standby raise
    assert zone.damper(24.0, HEATING_SP, OCCUPIED_SP) is None
    # Still empty the next morning: the vacancy timer ran on, so standby starts at once.
    zone.schedule(OCCUPIED_SP, at(6, 15, day=1))
    zone.tick(at(6, 15, day=1))
    assert zone.standby


def test_the_occupied_hours_can_be_changed() -> None:
    zone = ZoneStandby(occupied_hours=(8.0, 17.0))
    zone.sensor(False, at(7))
    zone.schedule(OCCUPIED_SP, at(7, 30))
    zone.tick(at(7, 30))
    assert zone.mode is OccupancyMode.UNOCCUPIED
    zone.tick(at(8))
    assert zone.mode is OccupancyMode.STANDBY
    zone.tick(at(17))
    assert zone.mode is OccupancyMode.UNOCCUPIED


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


def test_the_damper_shuts_within_the_setpoints_and_reopens_half_a_kelvin_beyond() -> None:
    zone = standby_zone()
    cooling = 24.5  # the standby cooling setpoint; heating 20.0 is set back to 19.5
    assert zone.damper(24.5, HEATING_SP, cooling) == 0.0  # at the setpoint: shut
    assert zone.damper(24.9, HEATING_SP, cooling) == 0.0  # warming: the hysteresis holds
    assert zone.damper(25.0, HEATING_SP, cooling) is None  # 0.5 K above: airflow back
    assert zone.damper(24.7, HEATING_SP, cooling) is None  # cooling down: stays released
    assert zone.damper(24.5, HEATING_SP, cooling) == 0.0  # back at the setpoint: shut again
    assert zone.damper(19.5, HEATING_SP, cooling) == 0.0  # at the set-back heating setpoint
    assert zone.damper(19.1, HEATING_SP, cooling) == 0.0  # holds
    assert zone.damper(19.0, HEATING_SP, cooling) is None  # 0.5 K below it: released
    assert zone.damper(19.4, HEATING_SP, cooling) is None  # still outside: stays released
    assert zone.damper(19.5, HEATING_SP, cooling) == 0.0


def test_a_zone_the_vav_holds_at_the_raised_setpoint_duty_cycles() -> None:
    zone = standby_zone()
    cooling = zone.cooling_setpoint(OCCUPIED_SP)
    held_c = (cooling + 273.15 + 1e-12) - 273.15  # held at 24.5 degC, up to float rounding
    temps = [held_c, 24.8, 25.0, held_c, held_c, 24.8, 25.0, held_c]
    assert [zone.damper(t, HEATING_SP, cooling) for t in temps] == [
        0.0,
        0.0,
        None,  # 0.5 K above: the VAV box takes the zone back to its setpoint ...
        0.0,  # ... where the damper shuts again
        0.0,
        0.0,
        None,
        0.0,
    ]


def test_a_zone_a_little_above_the_plain_schedule_shuts_at_standby_entry() -> None:
    zone = standby_zone()
    cooling = zone.cooling_setpoint(OCCUPIED_SP)
    assert zone.damper(OCCUPIED_SP + 0.02, HEATING_SP, cooling) == 0.0


def test_an_agent_setpoint_widens_the_band() -> None:
    zone = standby_zone()
    assert zone.damper(27.0, HEATING_SP, 28.0) == 0.0  # the agent commanded 28 degC
    assert zone.damper(28.4, HEATING_SP, 28.0) == 0.0
    assert zone.damper(28.5, HEATING_SP, 28.0) is None
    assert zone.damper(28.0, HEATING_SP, 28.0) == 0.0


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
