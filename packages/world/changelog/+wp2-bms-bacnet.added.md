The virtual BMS on BACnet/IP (WP2): `BacnetBmsTransport` serves SpotCool-Office-BMS (device 1001) from the `office_bms` point map, with real priority arrays, relinquish defaults and COV.

- `input()` pushes a `BmsWriteFrame` for every network write, then the SpotCool run's `SetpointCommandFrame` for all ten managed inputs on every tick. `output()` shows the SpotCool run's measurements in BMS units and takes the schedule from the baseline run.
- `CommandPolicy` refuses priorities 1-7, read-only points, other properties, and analog values off range or off grid (never clamped); WritePropertyMultiple follows the same rules.
- The BMS's own logic: occupied-standby (setpoint up 0.5 K, zone airflow shut off with hysteresis, `OCC-MODE`) and the heartbeat watchdog, which releases every priority-10 command after 120 s.
- Binds the primary IPv4 address with /24 on UDP 47808 by default, and fails at once on a taken port.
- Example `06_bacnet_device.py`, the `bacnet` pytest marker, and the YABE steps in `AGENTS.md`.
