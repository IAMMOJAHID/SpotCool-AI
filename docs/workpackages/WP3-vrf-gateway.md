# WP3: VRF indoor-unit gateway (meeting-room AC)

**Tool:** Claude Code. **Branch:** `wp/wp3-vrf-gateway`, cut from `world/main`. The lead syncs the trunk in before the build starts.
**Depends on:** WP0 (contracts: the `vrf_gateway` point map, `load_vrf_profiles()`, `VrfStateData`), WP2 (`transports/bacnet/`: `priority.py` and the BACnet/IP server code in `bms.py`) and WP11 (`World`, which marks where WP3 plugs in).

## Goal
"VRF-Gateway-MeetingRoom" (device 2001, UDP 47810) behaves like a real Mitsubishi, Daikin or LG BACnet gateway for one indoor unit in the South meeting room. Person A's agent aims its louvers at a person it sees on the live phone camera (`cam-meeting-live`), and the dashboard's AC panel (WP7) animates the unit from `vrf_state` events. This is the video's "louvers following a person on the phone camera" segment.

## Your lane (only these paths)
- **New:** `packages/world/src/spotcool_world/devices/vrf/model.py`, `transports/bacnet/vrf.py`, `transports/bacnet/server.py`, `workers/vrf_worker.py`, and `packages/world/tests/vrf/`.
- `transports/bacnet/bms.py`: only to move the BACnet/IP server code both devices need into `server.py` (decision 2).
- `runner/assembly.py`: the places marked `WP3`, `urls()`, and `run()`'s start and stop (decision 6). The matching tests go in `tests/assembly/`.
- `packages/world/AGENTS.md`: a "VRF gateway" section with its YABE steps, and the status cells.
- The changelog fragment `packages/world/changelog/+vrf-gateway.added.md`.

`packages/contracts/` is read-only. If you think a contract must change, stop and report it. The lead has already updated the Person A handoff's §6 to match these decisions.

## Decisions already made (binding)
1. **The VRF lives in wall time, outside the simulation pipeline.**
   - It's a live device for the phone-camera segment: a write takes effect at once, and the louvers move at 15°/s whatever `--speed` is.
   - It adds no processor to the pipeline, and its power never enters the plants' energy or the KPIs.
   - Delete the pipeline's `WP3` comment in `World.__init__`, and keep the workers' one.
2. **Reuse WP2; never copy it.**
   - Commands go through WP2's `PriorityArray` and `CommandPolicy`, unchanged. Network commands use priorities 8–16, the agent writes at 10, the operator at 8 wins, NULL releases, and out-of-range values are rejected, never clamped.
   - Move the BACnet/IP server code both devices need from `bms.py` into `transports/bacnet/server.py`: the guarded object classes, the application's WriteProperty and WritePropertyMultiple handlers, encode and decode, the command properties and the local-address helper. `bms.py` keeps everything specific to the BMS.
   - The move must not change behaviour. The BMS's tests pass unchanged.
3. **The gateway enforces the brand profile,** after `CommandPolicy` accepts a command.
   - **Refusals:**
     - a write to an axis the brand doesn't expose (`IDU-VANE-H` on Daikin and LG) is refused with `write-access-denied`;
     - a state the brand lacks (Pos1–Pos5 on LG) is refused with `value-out-of-range`;
     - the reason names the brand, e.g. "Daikin's gateway exposes no horizontal louver".
   - **A release (NULL) is always accepted.** If the value left in force is a state the brand lacks (LG's relinquish default, Pos3), the louver holds its angle with swing off.
   - **An axis the brand doesn't expose has no angle.** Its angle AI reports Reliability `no-sensor`, and `vrf_state` carries `None` for it.
4. **`devices/vrf/model.py` is a pure, deterministic `VrfUnit`.** It has no asyncio and no clock: `advance(dt_s)` moves it on, and setters take the commands in force.
   - **Louvers:**
     - a position maps to the profile's `angles_deg`;
     - the actual angle moves toward the target at **at most 15°/s × dt**, with dt clipped to ≤ 0.1 s, so a stall never makes a jump;
     - **Swing** sweeps the axis's whole range at 15°/s, reversing at each end. For an axis with no positions (LG), the range is 0° to −60°: a world constant, documented as illustrative and taken from the other brands' vertical range.
   - **Off:** the louvers hold, swing stops, and the room drifts toward ambient.
   - **Room:**
     - a first-order model with τ = 120 s of wall time;
     - ambient is 30 °C (the meeting room in summer), and the room starts at ambient;
     - in Cool, Dry and Auto it cools toward the setpoint but never below it (thermo-off holds the setpoint);
     - in Fan mode, or when the unit is off, it drifts toward ambient.
   - **Power:**
     - a compressor term that grows with (room − setpoint) and the fan level, capped at a rated input, plus a fan term while the unit is on;
     - state the constants in the docstring and mark them illustrative;
     - VRF power is never quoted as a saving.
   - **The relinquish defaults start the unit** On, in Cool, at 24 °C, fan Auto, vertical Pos3 and horizontal Pos3.
5. **`transports/bacnet/vrf.py`: `BacnetVrfGateway`.**
   - It's a BACnet/IP server built on `server.py`, from `load_point_map("vrf_gateway")` and the profile named by `settings.vrf_profile`, with `start()` and `stop()`.
   - It isn't a `BaseTransport`: it has no pipeline input or output (decision 1).
   - It binds the BMS's address (`settings.bms_address`, resolved as the BMS resolves it) on `settings.vrf_port`.
   - **Writes:** each network write is checked (decision 3) and applied to its priority array. The value left in force goes to the `VrfUnit` at once.
   - **Each write, accepted or refused, is published at once** as a `bms_write` event with `device: "VRF-Gateway-MeetingRoom"`.
     - It goes through `frame_events(BmsWriteFrame(...))` on `EVENTS_TOPIC`, the same path `World._publish_error` uses, so the recorder and the dashboard get the envelope the emitter would make.
     - It's stamped with the shared `SimClock`'s current time.
   - The AIs show the model's room temperature, power and louver angles. `VrfWorker` refreshes them.
   - Everything runs on the event loop: no threads.
6. **`workers/vrf_worker.py`: `VrfWorker`**, one of `World.workers`.
   - It ticks the model at **20 Hz in wall time**. dt is measured on a monotonic clock that tests can inject, so tests never wait in real time.
   - It refreshes the gateway's AIs on every tick.
   - **`vrf_state` events** (`frame_events(VrfStateFrame(...))` on `EVENTS_TOPIC`):
     - up to **5 Hz** while the published payload changes, compared after the rounding it's published with (angles to 0.1°, temperatures to 0.01 °C, power to 0.001 kW);
     - at least every 5 s otherwise, so a dashboard that connects late catches up within 5 s.
   - **In `World`:**
     - `__init__` builds the gateway;
     - `run()` starts it right after the BMS, raising the same kind of `OSError` message when the port is in use, and puts its stop on the exit stack;
     - the worker joins `self.workers` at the marked place;
     - `urls()` gains `"VRF": "<ip>:<vrf_port> (BACnet/IP)"`.
7. **No new run mode.**
   - For the phone-camera take, run the world at any speed, e.g. `spotcool-world run --plant rc --speed 1` with `SPOTCOOL_WORLD_LIVE_CAMERA_URL` set, and stop it after the take.
   - The stub agent doesn't drive the VRF. WP10 may revisit that.
8. **Recording and replay.**
   - Every `vrf_state` and VRF `bms_write` lands in the run's recording through the events topic.
   - A replay plays them back per step, not at their wall-time rate. The video films this segment live, so that's enough; document it as a known limit in AGENTS.md.

## Review outcome (lead)
- **The independent review approved it.** The BMS answered a 60-operation BACnet session byte-identically before and after the move to `server.py`.
- **Two low findings were fixed in the fix commit:**
  - the BACnet objects are attached before the socket binds;
  - a failing VRF tick is logged and the worker carries on, instead of ending the run.
- **Person A's §6 now says:**
  - write only on change, about once a second per point at most;
  - the general refusals come before the brand's;
  - `IDU-VANE-H` exists on every brand;
  - what LG's release does;
  - the start-up state.

## Tests
- **Model:**
  - a slew from 0° to −60° takes ≥ 4.0 s, no step exceeds 15°/s × dt, and dt clipping works;
  - swing covers the range and reverses;
  - the room converges to the setpoint while cooling and never goes below it;
  - the room rises toward ambient when the unit is off or in Fan mode;
  - power is 0 when off, and grows with (room − setpoint).
- **Profiles:**
  - Mitsubishi accepts both axes;
  - Daikin refuses `IDU-VANE-H` with `write-access-denied`;
  - LG accepts only Swing, and refuses Pos1–Pos5 with `value-out-of-range`;
  - a release is always accepted;
  - LG's relinquish default holds the angle.
- **Loopback BACnet integration** (marked `bacnet`, on free ports):
  - writing `IDU-VANE-V` = Pos4 at priority 10 brings `IDU-VANE-V-ANGLE` to −45° after slewing (with the worker's injected clock, or bounded wall time);
  - the operator's priority 8 beats 10;
  - a refused write returns the right BACnet error;
  - each write puts one `bms_write` event on the bus, with the VRF's device name.
- **Worker:** with a fake clock, the 20 Hz tick count, the 5 Hz publish limit and the 5 s keep-alive.
- **World:**
  - a `--speed 0` RC run starts and stops the VRF and records at least one `vrf_state`;
  - UDP 47810 already taken gives the clear `OSError`;
  - `urls()` lists the VRF.
- **BMS:** its existing BACnet tests pass unchanged after the move to `server.py`.

## Done when
- **The checks are green** with all extras installed: `uv run ruff check packages`, `uv run ruff format --check packages`, `uv run pyright packages` and `uv run pytest packages -W error::RuntimeWarning`.
- **AGENTS.md has YABE steps** for the user's manual check. YABE adds the device with Add Remote Bacnet/IP node, `2001 - <ip>:47810`, as the BMS loopback workaround does. It drives the louver, fan and setpoint points, and the angles slew visibly.
- The changelog fragment is written.
