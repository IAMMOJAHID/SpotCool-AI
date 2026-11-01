The virtual BMS on BACnet/IP (WP2): `BacnetBmsTransport` serves SpotCool-Office-BMS (device 1001) from the `office_bms` point map, with real priority arrays, relinquish defaults and COV.

- `input()` pushes a `BmsWriteFrame` for every network write, then the SpotCool run's `SetpointCommandFrame` for all ten managed inputs on every tick. `output()` shows the SpotCool run's measurements in BMS units and takes the schedule from the baseline run.
- `CommandPolicy` refuses priorities 1-7, read-only points, other properties, array indexes, and analog values off range or off grid (never clamped); a write without a priority is at 16, and WritePropertyMultiple follows the same rules.
- The BMS's own logic: occupied-standby within BOPTEST's occupied hours (setpoint up 0.5 K, `OCC-MODE`, and the zone's airflow shut off while it sits within its setpoints, back on 0.5 K beyond them) and the heartbeat watchdog, which releases every priority-10 command after 120 s.
- A point with no value yet reads reliability COMMUNICATION_FAILURE (status flag fault); each `StartFrame` starts the run's measurements and energy meter afresh.
- Binds the primary IPv4 address with /24 on UDP 47808 by default (`WorldSettings.bms_address` and `bms_port` override it), and fails at once on a taken port. BACpypes3 is pinned to 0.0.110.
- Example `06_bacnet_device.py`, the `bacnet` pytest marker, and the YABE steps in `AGENTS.md`.
