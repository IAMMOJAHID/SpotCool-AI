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
     - set `SIM-CLOCK`;
     - on `ScenarioStateFrame`, set `WES-ENTRANCE-DOOR` from the West zone's `door_open`.
3. **BMS logic** (`bms_logic.py`, pure and unit-tested). This is what a real BMS does on its own:
   - **ASHRAE G36 occupied-standby.** When a zone's `OCC-SENSOR` effective value is Unoccupied for 5 sim-minutes or more during scheduled occupancy, add +0.5 °C to that zone's scheduled cooling setpoint, i.e. the relinquish default.
     - Set `OCC-MODE` to Occupied, Standby or Unoccupied; schedule-unoccupied is when the baseline setpoint is ≥ 26 °C.
     - An agent-commanded setpoint still wins, because a priority slot beats the relinquish default.
   - **Heartbeat watchdog.** If `SPOTCOOL-HEARTBEAT`'s effective value hasn't changed for `HEARTBEAT_TIMEOUT_S` (120 wall seconds) after it was first written, release every priority-10 slot and publish a `BmsWriteFrame` with the reason "heartbeat timeout".
4. **Network.** BACnet/IP needs a bound IP address.
   - Default: the machine's primary IPv4 address with mask /24, port 47808.
   - Allow `--address` from settings.
   - Document the Windows Firewall rule (allow UDP 47808–47810) and the loopback workaround in AGENTS.md's Windows notes.
5. **Reference adapter** (optional, last): `boptest_proxy.md`, a runbook for starting BOPTEST's own `bacnet/BopTestProxy.py` (legacy bacpypes, Python 3.11) against a test id, for comparison. Write no code for it.

## Tests
- **`priority.py`:** every slot ordering; NULL releases; an operator at 8 beats the agent at 10; slots 1–7 are rejected; range and grid rejects.
- **`bms_logic.py`:** standby after 5 minutes, not after 4; heartbeat timeout releases only slot 10.
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
