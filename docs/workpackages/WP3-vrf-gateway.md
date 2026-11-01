# WP3: VRF indoor-unit gateway (meeting-room AC)

**Tool:** Claude Code. **Branch:** `wp/wp3-vrf-gateway`. **Depends on:** WP0, contracts, and WP2's `priority.py`. If WP2 hasn't merged, import it from WP2's branch through the lead, or wait for it. Do not copy it.
**Directories:** `packages/world/src/spotcool_world/devices/vrf/`, `transports/bacnet/vrf.py`, `workers/vrf_worker.py`, `tests/vrf/`.

## Goal
"VRF-Gateway-MeetingRoom" (instance 2001, UDP 47810), built from `load_point_map("vrf_gateway")` and one brand profile from `load_vrf_profiles()` (`WorldSettings.vrf_profile`). It behaves like a real Mitsubishi, Daikin or LG BACnet gateway, so the agent can aim the louvers at a person.

## Design
1. **`devices/vrf/model.py`** is a pure, deterministic `VrfUnit` with an injectable clock.
   - **State:** on/off, mode, setpoint, fan, commanded louver positions, actual louver angles, room temperature, power.
   - **Louvers:**
     - a commanded position maps to the profile's `angles_deg`;
     - actual angles move toward the target at **at most 15°/s**, scaled by the measured `dt`, which is clipped to ≤ 0.1 s so a stall never causes a jump;
     - Swing oscillates across the profile's full range at 15°/s.
   - **Profile capability is enforced:**
     - a position the brand doesn't expose is rejected with a reason (for example, LG accepts Swing only, and Daikin exposes no horizontal louver);
     - a client reads the profile through `spotcool_contracts.vrf`.
   - **Room:** first order toward the setpoint while cooling; toward 30 °C ambient (the meeting room in summer) when off. τ = 120 s of **wall** time, because this part of the demo runs live.
   - **Power:** a compressor term that scales with (room − setpoint) and fan level, plus a fan term. State the constants in the docstring, marked illustrative.
2. **`transports/bacnet/vrf.py`:**
   - BACpypes3 objects from the map;
   - commandable points use WP2's `PriorityArray` and `CommandPolicy`;
   - every write becomes a `BmsWriteFrame` (device `VRF-Gateway-MeetingRoom`);
   - AIs are updated from the model.
3. **`workers/vrf_worker.py`:** a `VrfWorker` ticks the model at 20 Hz in **wall** time, independent of the simulation pipeline, and publishes `EventFrame(VRF_STATE)` to the bus at 5 Hz for the dashboard's louver animation.

## Tests
- **Slew:** 0° to −60° takes ≥ 4.0 s; no step exceeds 15°/s × dt; dt clipping works.
- **Profiles:** Mitsubishi accepts both axes; Daikin rejects a horizontal position; LG accepts only Swing.
- **Room temperature** converges toward the setpoint while on, and rises while off.
- **Loopback BACnet integration** (marked `bacnet`): write `IDU-VANE-V` = Pos4 at priority 10, and check the angle reaches −45° after slewing.

## Done when
- The README checks are green.
- The louver, fan and setpoint points can be driven from YABE, and the angles slew visibly.
