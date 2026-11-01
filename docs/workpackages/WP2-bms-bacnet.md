# WP2: Virtual BMS over BACnet/IP

**Tool:** Claude Code. **Branch:** `wp/wp2-bms-bacnet`. **Depends on:** WP0 and contracts.
**Directories:** `packages/world/src/spotcool_world/transports/bacnet/` (except `vrf.py`), `tests/bacnet/`, `examples/foundational/06_bacnet_device.py`.

## Goal
A real BACnet/IP device, "SpotCool-Office-BMS" (instance 1001, UDP 47808), built **entirely from** `spotcool_contracts.load_point_map("office_bms")`.
- The agent sees what it would see on a real building network.
- YABE, a free BACnet explorer, can browse and write it.

## Design
1. **`priority.py`** is pure logic with no sockets, and is unit-tested exhaustively.
   - `PriorityArray`:
     - 16 slots;
     - `write(value, priority)` and `release(priority)` (a NULL write);
     - `effective(relinquish_default)` returns the value and the winning priority.
   - `CommandPolicy` holds the rules:
     - the agent writes at 10;
     - a slot at priority 8 or more urgent is never overwritten by a less urgent writer;
     - slots 1–7 are never writable over the network (reject them with a reason);
     - analog values outside `min_value`..`max_value`, or off the `resolution` grid, are **rejected**, never silently clamped.
2. **`bms.py`** is `BacnetBmsTransport(BaseTransport)`, using BACpypes3 (the `bacnet` extra).
   - It creates AI, AV, BI, BV and MSV objects from the map:
     - names, units and states come from the map;
     - commandable objects get a real `priorityArray`, `relinquishDefault` and COV support.
   - Every incoming write or release becomes a `BmsWriteFrame` (`BmsWriteData`, accepted or rejected with a reason), published downstream for the dashboard and recorder.
   - **`input()` processor:** on each `TickFrame`, before the plants step, it emits `SetpointCommandFrame(run="spotcool", overrides)`:
     - for each `*-CLG-SP` whose effective value comes from an active slot: `boptest.write` mapped to the value in K;
     - when only the relinquish default applies: `None`, which releases the input so BOPTEST's own controller acts.
   - **`output()` processor:**
     - on `PlantMeasurementsFrame(run="spotcool")`, update every `read` point, converting units with `spotcool_world.adapters.units.to_bms`;
     - on `PlantMeasurementsFrame(run="baseline")`, update each setpoint's **relinquish default** from `relinquish_from_baseline`, which is the value the building's own schedule applies at this moment;
     - compute `HVAC-TOTAL-PWR` (the sum of 501–507) and `HVAC-ENERGY` (the integral since the run started);
     - set `SIM-CLOCK` from the measurement frame's `sim_time_s`, which is the end of the step the values describe;
     - on `ScenarioStateFrame`, set `WES-ENTRANCE-DOOR` from the West zone's `door_open`.
3. **BMS logic** (`bms_logic.py`, pure and unit-tested). This is what a real BMS does on its own:
   - **Occupied-standby (ASHRAE 90.1-2019 §6.5.3.8; G36).** A zone is in standby once its `OCC-SENSOR` effective value has been Unoccupied for 5 sim-minutes or more during scheduled occupancy.
     - **Scheduled occupancy** for the step that starts at tick time t needs **both** of these:
       - t is within the occupied hours [06:00, 19:00);
       - the baseline cooling setpoint is below 26 °C.

       The setpoint alone isn't enough. BOPTEST, like the RC plant, reports the setpoint that was in force during the step that just ended, so a schedule edge shows up one stamp late: the core still reads 24 °C at 19:00 and only 30 °C from 19:15. On the setpoint alone, standby would run until 19:15 and SpotCool would cool an empty building after hours.
     - **Setpoints:** raise the cooling setpoint's relinquish default (the schedule) by 0.5 K, and lower the heating setpoint by 0.5 K. An agent-commanded setpoint still wins, because a priority slot beats the relinquish default.
     - **Airflow shut-off** (the standard requires "all airflow supplied to the zone shall be shut off whenever the space temperature is between the active heating and cooling set points"):
       - In the **SpotCool run's** `SetpointCommandFrame`, emit `hvac_oveZonAct<Z>_yDam_u = 0.0` (zone damper closed).
       - Close the damper while the zone temperature is at or inside both active setpoints (1e-6 K tolerance).
       - Release it (`None`) once the temperature is 0.5 K **beyond** either setpoint, so the VAV box conditions the zone normally.
       - Between those thresholds, keep the last state (hysteresis).
       - Leaving standby releases it at once.
       - **Why the hysteresis sits above the cooling setpoint:** when released, the VAV box holds the zone exactly at the active setpoint. A re-close threshold below the setpoint is therefore never reached, so the shut-off would work once at most. The reviewer saw exactly that on RC: shut for 3 of 48 occupied ticks. With the hysteresis above the setpoint, the damper duty-cycles, and the zone floats between the setpoint and setpoint + 0.5 K.
     - **Never on the baseline run.** The baseline is the same BMS without occupancy sensors, which is the fair comparison: SpotCool's claim is that it turns existing CCTV into the zone occupancy sensors a code-compliant BMS can use.
     - **Measured on BOPTEST** (README "Facts"), with every zone empty all day:
       - setpoint 28 °C alone saves 29 %, because the VAV boxes stop at minimum airflow and the zones float only to about 26 °C;
       - adding the airflow shut-off saves 81 %.

       The shut-off is the main lever.
     - **Set `OCC-MODE`** to Occupied, Standby or Unoccupied; schedule-unoccupied is when the baseline setpoint is ≥ 26 °C.
   - **Heartbeat watchdog.** If `SPOTCOOL-HEARTBEAT`'s effective value hasn't changed for `HEARTBEAT_TIMEOUT_S` (120 wall seconds) after it was first written, release every priority-10 slot and publish a `BmsWriteFrame` with the reason "heartbeat timeout".
4. **Network.** BACnet/IP needs a bound IP address.
   - Default: the machine's primary IPv4 address with mask /24, port 47808.
   - Allow `--address` from settings.
   - Document the Windows Firewall rule (allow UDP 47808–47810) and the loopback workaround in AGENTS.md's Windows notes.
5. **Reference adapter** (optional, last): `boptest_proxy.md`, a runbook for starting BOPTEST's own `bacnet/BopTestProxy.py` (legacy bacpypes, Python 3.11) against a test id, for comparison. Write no code for it.

## Tests
- **`priority.py`:** every slot ordering; NULL releases; an operator at 8 beats the agent at 10; slots 1–7 are rejected; range and grid rejects.
- **`bms_logic.py`:**
  - standby starts after 5 minutes, not after 4;
  - in standby the damper override is 0.0 at or inside the band, released at cooling setpoint + 0.5 K, and the hysteresis holds;
  - a zone held at the raised setpoint re-closes (the damper duty-cycles);
  - at the 19:00 tick standby ends even though the latest baseline still reads 24 °C;
  - leaving standby releases the damper;
  - the baseline run never gets a damper override;
  - heartbeat timeout releases only slot 10.
- **Integration test on loopback** (mark it `bacnet`; skip if a socket can't bind), with the device and a BACpypes3 client on another port:
  - read `ZN-COR-T`;
  - write `ZN-COR-CLG-SP` = 26.5 at priority 10, and check the next `SetpointCommandFrame` has 299.65 K;
  - an operator write at priority 8 wins;
  - NULL at 10 and 8 releases the input to `None`;
  - a COV subscription receives a change.

## Done when
- The README checks are green.
- `06_bacnet_device.py` starts the device against the RC plant.
- **Manually in YABE** (record the steps in AGENTS.md):
  - all 51 objects are visible;
  - writing at 8, then 10, then NULL shows the priority array behaving correctly.
