# SpotCool world: integration handoff for Person A (the agent)

**From** Person B (the world and the video), **for** Person A (the agent).

**Last updated:** 2026-11-01, 22:00 IST. **Location:** `docs/integration/PERSON_A_HANDOFF.md` on branch `world/main`.

This document replaces the earlier chat notes. Everything in them is folded in here, and the changes since are listed in §15.

---

## 1. The short version

- **Your agent sees a real building's interfaces.**
  - BACnet/IP for the building management system (BMS) and for the meeting-room VRF unit.
  - HTTP for CCTV snapshots and the room calendar.
  - Occupant complaints arrive at your own `/chat` and `/voice` endpoints.

  Nothing in the world is specific to your code: replace the world with real hardware and your agent works unchanged.
- **You control two kinds of point**, both at **priority 10**:
  - zone **cooling setpoints** (`ZN-<Z>-CLG-SP`);
  - zone **occupancy sensors** (`ZN-<Z>-OCC-SENSOR`).

  Release a point by writing NULL at 10. Toggle `SPOTCOOL-HEARTBEAT` at least every 30 s.
- **Your biggest lever is the occupancy sensor.**
  - Mark an empty zone Unoccupied, and the BMS's own occupied-standby (ASHRAE 90.1-2019 §6.5.3.8) **shuts that zone's airflow off**.
  - Raising a setpoint alone saves less in this building, because the VAV boxes stop at minimum airflow.
  - Measured with every zone empty all day: −29 % HVAC energy for the setpoint alone, and **−81 %** with the airflow shut off (§10).
- **Release everything at 19:00**, the end of the occupied day. BACnet commands persist after hours.
- **Timing:** a reading describes the **15-minute step that just ended**. Your writes take effect from the next step.
- **Contracts:** they live in `packages/contracts` (Python package `spotcool-contracts`, v0.1). The interface is unchanged. Pull `wp/wp0-core-contracts` (now `f2d849b`) or `world/main`.

## 2. What the world is

B builds everything your agent connects to, because there is no real building:

| Part | What it stands in for | Status (Nov 2, 11:00 IST) |
|---|---|---|
| **Physics** | A 5-zone office from BOPTEST (`multizone_office_simple_air`, 1,662.66 m², Chicago weather, peak cooling day), run **twice**: the SpotCool run (your writes) and the baseline run (the building on its own) | **Done** (WP1). A fast RC model replaces BOPTEST for tests and quick runs |
| **BMS** | Device `SpotCool-Office-BMS` on BACnet/IP | **Built** (WP2), merged into `world/main` |
| **VRF gateway** | One meeting-room indoor unit on BACnet/IP (fan, setpoint, louvers) | Planned (WP3); its behaviour is settled in §6 |
| **Cameras** | A CCTV recorder's catalog and snapshot API: one camera per zone, plus a live phone camera | **Built** (WP4). The zone clips are filmed on Nov 2; until then the cameras show dataset or synthetic frames |
| **Calendar** | Room bookings shaped like Microsoft Graph `getSchedule` | **Built** (WP5), served during every run |
| **Occupants** | Scripted complaints sent to your `/chat` and `/voice` | **Built** (WP5), sent during every run |
| **Telemetry and dashboard** | An event stream, the KPIs, and the judges' dashboard | Event stream, KPIs and `/ws/agent` **built** (WP6); the dashboard is planned (WP7) |
| **Assembly** | `spotcool-world run --scenario hybrid_tuesday --plant rc\|boptest-local` | **Built** (WP11), with `replay` and `report`. A stub agent stands in for yours in our test runs, labelled `stub: true` |

**Targets:**

| When (IST) | Target |
|---|---|
| Nov 2, 18:00 | Full run on the RC plant with a stub agent |
| Nov 2, 22:00 | The same on BOPTEST |
| **Nov 3, morning** | **Integration with your agent** |
| Nov 3, 21:00 | First automated video take |
| Nov 4, 21:00 | Submission deadline |

## 3. Where things live

- **Repo:** `IAMMOJAHID/SpotCool-AI`.
- **Branches:**
  - `main` (release) and `dev` (A+B integration) are shared.
  - `world/main` is B's trunk, holding every finished world work package.
  - Your work is on `spotcool_agent`.
  - B's branches are `wp/<id>-<slug>`, merged into `world/main` one at a time after review, with green CI.
- **Contracts:** `packages/contracts`, Python ≥ 3.11 and Pydantic v2. In a uv workspace, depend on `spotcool-contracts` with `[tool.uv.sources] spotcool-contracts = { workspace = true }`.

| You want | Use |
|---|---|
| The BACnet point map | `load_point_map("office_bms")` and `load_point_map("vrf_gateway")`. Each `PointSpec` has `object_type`, `instance`, `name`, `units`, `min_value`, `max_value`, `resolution`, `states` and `relinquish_default`. Helpers: `.by_name(...)`, `.zone_points(Zone.SOU)`, `.commandable()` |
| Zones | `Zone` (`cor`, `nor`, `sou`, `eas`, `wes`) and `ZONES[zone]`, which holds `display_name` and `camera_id` |
| Priorities and timing | `spotcool_contracts.priorities`: `AGENT_PRIORITY = 10`, `OPERATOR_PRIORITY = 8`, `SCHEDULE_PRIORITY = 16`, `HEARTBEAT_PERIOD_S = 30`, `HEARTBEAT_TIMEOUT_S = 120`, `RECOMMENDED_MIN_WRITE_INTERVAL_S = 300` (sim seconds) |
| VRF brand capabilities | `load_vrf_profiles()` |
| HTTP models | `CameraCatalog` and `CameraInfo` (`cameras.py`); `ScheduleInformation` and `ScheduleItem` (`calendar.py`); `ComplaintText` and `VOICE_FORMAT` (`occupants.py`) |
| Dashboard envelope | `EventEnvelope`, `EventType` and `EventSource` (`events.py`) |
| JSON Schema, for non-Python code | `uv run python packages/contracts/scripts/export_schemas.py` |

- **Changing a contract** needs both of us. Ask through Person B; B's lead owns `packages/contracts`.

## 4. Every interface at a glance

| Interface | Address | Direction | Status |
|---|---|---|---|
| BMS, BACnet/IP | device **1001** `SpotCool-Office-BMS`, UDP **47808** | you read and write | built (§5) |
| VRF gateway, BACnet/IP | device **2001** `VRF-Gateway-MeetingRoom`, UDP **47810** | you read and write | planned (§6) |
| Camera catalog | `GET http://<world>:8081/cameras` | you read | built (§7) |
| Snapshot | `GET http://<world>:8081/cameras/{id}/snapshot.jpg` | you read | built (§7) |
| Room calendar | `GET http://<world>:8081/calendar/rooms/meeting-south/schedule` (also `.ics`) | you read | built (§8) |
| Complaints, text | `POST http://<agent>:8002/chat` | world calls you | built (§9) |
| Complaints, voice | `POST http://<agent>:8002/voice` | world calls you | built (§9) |
| Dashboard events | WebSocket `ws://<world>:8090/ws/agent` | you publish | built (WP6, §9) |

The world expects your agent at `http://127.0.0.1:8002` by default (`agent_base_url`; tell us if that changes).

## 5. The BMS over BACnet/IP (your main control surface)

### 5.1 Points

There are 51 points; the device object makes 52. Zone index i: cor = 1, nor = 2, sou = 3, eas = 4, wes = 5.

| Object | Name | Units / states | Access | Meaning |
|---|---|---|---|---|
| AI 100+i | `ZN-<Z>-T` | °C | read | Zone air temperature |
| AV 200+i | `ZN-<Z>-CLG-SP` | °C, 16–30 on a 0.5 grid | **commandable** | Cooling setpoint. The relinquish default is the building schedule: 24 °C from 06:00 to 19:00, 30 °C otherwise |
| AV 250+i | `ZN-<Z>-HTG-SP` | °C | read | Heating setpoint (20 °C occupied, 12 °C otherwise) |
| AI 300+i | `ZN-<Z>-CO2` | ppm | read | Zone CO₂ |
| AI 350+i | `ZN-<Z>-SA-FLOW` | L/s | read | Zone supply airflow. It drops to a small leakage flow while the zone is in standby |
| MSV 400+i | `ZN-<Z>-OCC-MODE` | 1 Occupied, 2 Standby, 3 Unoccupied | read (set by the BMS) | What the BMS is doing with the zone |
| BV 450+i | `ZN-<Z>-OCC-SENSOR` | inactive = Unoccupied, active = Occupied | **commandable** | The zone's occupancy-sensor input; relinquish default Occupied. **You are the occupancy sensor** |
| AI 501–507 | `AHU-SF-PWR`, `AHU-CLG-PUMP-PWR`, `AHU-HTG-PUMP-PWR`, `CH-PWR`, `CHW-PUMP-PWR`, `HP-PWR`, `HW-PUMP-PWR` | kW | read | The 7 HVAC power meters |
| AI 510 | `HVAC-TOTAL-PWR` | kW | read | Their sum |
| AI 511 | `HVAC-ENERGY` | kWh | read | HVAC energy since the run started |
| AI 520, 521 | `OA-T`, `OA-RH` | °C, %RH | read | Outdoor air |
| AI 530, 531 | `AHU-SAT`, `AHU-SF-SPD` | °C, % | read | Air-handler supply temperature and fan speed |
| BI 601 | `WES-ENTRANCE-DOOR` | contact | read | West entrance door contact, driven by the scenario |
| BV 901 | `SPOTCOOL-HEARTBEAT` | inactive = Off, active = On | **commandable** | Your heartbeat (§5.2) |
| AV 950 | `SIM-CLOCK` | seconds | read | Simulation time, in seconds since 1 January 00:00 of the BOPTEST year. Demo instrumentation; a real BMS has its own clock |

Commandable points have a real 16-slot `priorityArray`, `relinquishDefault` and `currentCommandPriority`. **COV subscriptions work** on analog and binary points.

### 5.2 Command rules

- **Write at priority 10.** An operator at **8**, for example from the YABE explorer in the demo, always beats you. Schedules sit at 14–16.
- **NULL at 10 releases your slot.** The point then falls back to whatever is more urgent, or to the relinquish default.
- **Always send a priority.** A write without one counts as priority 16, BACnet's rule.
- **Rejected, with a BACnet error and never silently clamped:**
  - priorities 1–7: write-access-denied;
  - read-only points, properties other than present-value, and the device object: write-access-denied;
  - values out of range or off the 0.5 °C grid, and unknown states: value-out-of-range.
- **Every write is recorded,** accepted or rejected, client address included. It appears on the judges' dashboard as a `bms_write` event.
- **Heartbeat:**
  - Toggle `SPOTCOOL-HEARTBEAT` at priority 10 at least every **30 s** of wall time.
  - If it hasn't changed for **120 s**, the BMS releases **every** priority-10 slot, your `OCC-SENSOR` writes included. The building then falls back to its own control.
  - The watchdog arms on your first heartbeat write.
- **Write interval:** leave at least **300 simulated seconds** between writes to the same point (`RECOMMENDED_MIN_WRITE_INTERVAL_S`). In practice that means at most one write per point per 15-minute step.

### 5.3 What the BMS does on its own: occupied-standby (the lever to use)

The BMS follows ASHRAE 90.1-2019 §6.5.3.8 (also in Guideline 36). The standard requires that "all airflow supplied to the zone shall be shut off whenever the space temperature is between the active heating and cooling set points".

1. **Trigger.** A zone enters standby once its `OCC-SENSOR` effective value has been **Unoccupied for at least 5 simulated minutes during scheduled occupancy**.
   - Scheduled occupancy means **06:00–19:00**, and the building's schedule also says occupied.
   - With 15-minute steps, standby starts **from the step after your write**. Example: a write stamped 07:15 puts the zone in standby from the 07:30 step.
2. **In standby:**
   - **Setpoints:** the zone's cooling setpoint default goes **up 0.5 K** (24 → 24.5 °C), and its heating setpoint down 0.5 K.
   - **Airflow:** the zone's **airflow is shut off** (damper closed) while the zone is at or below the active cooling setpoint.
   - **Hysteresis:** cooling resumes only once the zone is **0.5 K above** that setpoint, and the damper shuts again when the VAV box has brought it back. An empty zone therefore **floats from the active cooling setpoint to about 0.5–0.9 K above it**, because the VAV box catches it within a 15-minute step. Its airflow is off roughly half the time.
     - Measured on the RC plant, standby alone, South empty from 07:00: the damper was shut on 26 of 47 standby steps, mean airflow was 343 L/s against the baseline's 487 L/s (−30 %), and the peak was 25.4 °C.
3. **A raised setpoint widens the band.** If you also write `CLG-SP` = 28 °C for the empty zone, the airflow stays off while the zone warms from about 24 °C up to 28 °C. That is the −81 % case.
4. **Leaving standby.**
   - Any Occupied value, or your NULL release (the default is Occupied), ends standby **at once**: the airflow and the setpoint come back in the next step.
   - Standby also ends at 19:00.
   - A zone still marked Unoccupied the next morning enters standby at the first scheduled step.
5. **What you see:** `OCC-MODE` reads Standby. While the damper is shut, `SA-FLOW` drops (South: about 300 L/s → about 26 L/s), and `HVAC-TOTAL-PWR` falls.
6. **You never command dampers.** The BMS does it, as a real code-compliant BMS would once it has zone occupancy sensors. That is the product story: occupancy sensors are rarely installed because of their cost, and SpotCool turns the building's existing CCTV into them.
7. **Fairness:** the baseline run is the same building without occupancy sensors, so it never enters standby.

### 5.4 Time and timing

- **Steps:** the world advances in **15-minute simulated steps**. At the default speed (300), one step takes about **3 s of wall time**, so the occupied day 07:00–19:00 runs in about 2.5 minutes. The demo day is BOPTEST's peak cooling day (day 199), starting at 07:00.
- **Readings describe the step that just ended.** A value stamped 10:15 is the state at 10:15, after the 10:00–10:15 step. `SIM-CLOCK` shows that stamp. Schedule edges therefore show one stamp late: the building's own setpoint still reads 24 °C at 19:00 and 30 °C only from 19:15.
- **Your writes apply from the next step.** Write between steps. A write arriving during a step is applied at the next one.
- **Points without data yet.** Until the plant's first reading arrives, which only happens while the simulator starts, points report `statusFlags` fault and `reliability` = communication-failure. **Don't act on a point with the fault flag set.** (This lands in WP2's final fixes.)

### 5.5 Running the BMS on your machine today

Once WP2 is on `world/main` (shortly), you can run the real device against the fast RC building:

```bash
git fetch origin && git switch world/main      # or: git worktree add ../spotcool-world origin/world/main
uv sync --all-packages --extra bacnet --extra rc
uv run python packages/world/examples/foundational/06_bacnet_device.py --ticks 48
#   options: --speed 300 (0 = as fast as possible)  --address <ip>  --port <udp>
```

- **Same-PC networking:**
  - The device binds this machine's primary IPv4 address on UDP 47808.
  - If your agent runs on the **same PC**, give your BACnet client a different local UDP port (for example 47820), and **address the devices directly by IP and port**: device 1001 at `<ip>:47808`, later device 2001 at `<ip>:47810`. Discovery by broadcast Who-Is won't reach a client on another port.
  - On Windows, allow UDP 47808–47810 in Windows Firewall for Python.
- **Tools:** Python clients BACpypes3 or BAC0, and YABE (a free Windows BACnet explorer) for browsing by hand.

## 6. VRF gateway in the meeting room (planned, WP3)

Device **2001** `VRF-Gateway-MeetingRoom`, UDP **47810**. Same rules as the BMS: priority 10, NULL releases, the operator at 8 wins.

| Object | Name | Units / states | Access |
|---|---|---|---|
| BV 1 | `IDU-ONOFF` | Off, On | commandable |
| AV 2 | `IDU-SP` | °C, 16–30 on a 0.5 grid | commandable |
| MSV 3 | `IDU-MODE` | 1 Cool, 2 Dry, 3 Fan, 4 Auto | commandable |
| MSV 4 | `IDU-FAN` | 1 Auto, 2 Low, 3 Mid, 4 High | commandable |
| MSV 5 | `IDU-VANE-V` | 1–5 Pos1–Pos5, 6 Swing | commandable |
| MSV 6 | `IDU-VANE-H` | 1–5 Pos1–Pos5, 6 Swing | commandable |
| AI 7 | `IDU-ROOM-T` | °C | read |
| AI 8 | `IDU-PWR` | kW | read |
| AI 9, 10 | `IDU-VANE-V-ANGLE`, `IDU-VANE-H-ANGLE` | degrees | read |

- **Brand profiles** (`load_vrf_profiles()`):
  - Mitsubishi has both vane axes (vertical 0, −15, −30, −45, −60°; horizontal −40 to 40°);
  - Daikin has vertical only;
  - LG has swing only.

  Read the capability flags and handle the differences.
- **Refusals by brand:**
  - a write to an axis the brand doesn't expose (`IDU-VANE-H` on Daikin and LG) is refused with `write-access-denied`;
  - a state the brand lacks (Pos1–Pos5 on LG) is refused with `value-out-of-range`;
  - a NULL release is always accepted;
  - the angle point of an axis the brand doesn't expose has Reliability `no-sensor`.
- **Slew:** the louvers move at 15°/s between positions, as real ones do. The angle points lag the command: 0° to −60° takes 4 s.
- **Real time:** the unit runs on the wall clock, whatever the simulation's speed. A write takes effect at once.
- **Address:** the BMS's IP, on UDP 47810. A Who-Is sent to 47808 won't reach it, so address it directly.
- **Events:**
  - `vrf_state` up to 5 times a second while the unit changes, and every 5 s otherwise;
  - each write to it also appears as a `bms_write` event with `device: "VRF-Gateway-MeetingRoom"`.
- **Demo moment:** on the live phone camera, a person sits far away, walks up and says "The AC is blowing right on my face." Your agent re-aims the vanes.

## 7. Cameras (built, WP4)

- **Catalog:** `GET /cameras` returns a `CameraCatalog` of `CameraInfo` entries. Each entry has `id`, `name`, `zone`, `snapshot_url`, optionally `stream_url`, `width`, `height`, `fps_hint` and `source` (`scenario`, `dataset` or `live`).
- **Ids:** `cam-cor`, `cam-nor`, `cam-sou`, `cam-eas`, `cam-wes`, plus the live phone camera `cam-meeting-live`.
- **Snapshots:** `GET /cameras/{id}/snapshot.jpg` works like ONVIF GetSnapshotUri. The **scene follows simulated time**, and the frames inside a clip advance in wall time, so the feed looks live. Poll snapshots and count people (SAM 3.1).
- **Privacy rule (both of us):** frames are processed in memory and never stored; only counts leave. The catalog carries this notice.

## 8. Room calendar (built, WP5)

- **Endpoint:** `GET /calendar/rooms/meeting-south/schedule` returns `ScheduleInformation` (`scheduleId`, `scheduleItems`), shaped like Microsoft Graph `getSchedule`.
- **Items:** each `ScheduleItem` has `status` (`free`, `tentative`, `busy`, `oof`, `workingElsewhere` or `unknown`), `subject`, `location`, and `start`/`end` (Graph `dateTimeTimeZone`).
- **Other forms:** an `.ics` feed at `/calendar/rooms/meeting-south.ics`.
- **Dates:** the calendar puts simulation time on 2026 dates: a date-time is 2026-01-01 00:00 plus `SIM-CLOCK` seconds, labelled `timeZone: "UTC"` (the simulation's local clock; no daylight saving). The demo day, BOPTEST day 199, is 2026-07-19.
- **Rooms:** `meeting-south` is the South zone.
- **Use:** pre-cool before a booking (§11).

## 9. Complaints (built, WP5) and dashboard events (built, WP6; the dashboard is WP7)

- **`POST /chat`:** a `ComplaintText` JSON body: `text` (1–280 characters), `source` (`occupant-sim` or `occupant`), an optional `room_hint`, and an optional `sim_time_s`.
- **`POST /voice`:** `multipart/form-data` with an `audio` file in `VOICE_FORMAT` (WAV, 16 kHz, mono, s16le) and an optional `meta` field holding a `ComplaintText` JSON string. Its `text` is the script line, for logging only. **Transcribe the audio yourself.**
- **Reply within 10 s** to `/chat` and `/voice`: accept the complaint and answer, then process it (transcription included) in the background. The world waits 10 s and doesn't retry a request that timed out, because you may already have it. It retries only connection failures and 5xx responses, twice.
- **Dashboard WebSocket (port 8090):** every message is an `EventEnvelope` `{v: 1, type, source, t_ms, sim_time_s, data}`.
  - **Publish on `ws://<world>:8090/ws/agent`.** Send one envelope per message, with `source: "agent"` and one of your types below, at most 64 KB each and 20 per second. An invalid message gets `{"error": "..."}` back. That socket never sends you world events, so ground truth stays out of your agent. The dashboard itself reads `/ws/events`.
  - **World types:** `tick`, `run_status`, `scenario_state`, `plant_measurements`, `bms_write`, `kpi`, `vrf_state`, `camera_scene`, `calendar`, `complaint_cue`, `error`, `metrics`.
  - **Your types, reserved for you:** `perception`, `decision`, `explanation`, `chat`, with `source: "agent"`. Tell us your `data` fields so the dashboard can render them.
- **Don't read ground truth.** `scenario_state` and `camera_scene` carry the true people counts (`people`), the scene, the door state and clothing. They exist for the dashboard and the scoring. **Your agent must not read them**; it has to perceive from the cameras. Use the stream only to publish.

## 10. The building and what we measured (BOPTEST)

- **Baseline schedule:** cooling 24 °C from 06:00 to 19:00 and 30 °C otherwise; heating 20 °C and 12 °C. HVAC power peaks at about 18 kW (14:00) and is about 0 at night. The test day uses about 195 kWh of HVAC energy.
- **Savings levers**, with every zone treated as empty all day. These are upper bounds; the realistic `hybrid_tuesday` number comes later from our run.

| What | HVAC energy for the day | Saving |
|---|---|---|
| Baseline | 195.3 kWh | — |
| Cooling setpoint 28 °C in every zone | 138.3 kWh | −29 % |
| The same, plus airflow shut off while below 27.5 °C (standby with a raised setpoint) | 37.3 kWh | **−81 %** |

- **Why the setpoint alone is weak:** with only a raised setpoint, the VAV boxes drop to minimum airflow (the core alone at about 880 L/s), and the zones float only to 24.7–26.1 °C.
  - The **core zone** barely responds: a one-hour 28 °C override moved it 0.00 °C.
  - The perimeter zones (South and West in the afternoon) respond most.
- **Standby on its own** (no raised setpoint) is a smaller lever. On the RC plant it cut an empty South zone's airflow by about 30 %, and the zone stayed between 24.5 and 25.4 °C. We will measure the BOPTEST number in the full scenario. **Standby plus a raised setpoint** is the big one (−81 % above).

## 11. A suggested policy (yours to decide)

- **Zone empty** (cameras count 0 for a while, during 06:00–19:00): write `OCC-SENSOR` = Unoccupied. Optionally also raise `CLG-SP` (26–28 °C) to widen the standby band.
- **People present:** write NULL to both, so the building's own setpoint applies (24 °C). The comparison is only fair if SpotCool keeps the building's comfort where people are. The savings then come only from empty zones.
- **Before a booking:** watch the calendar, and release the room's points ahead of the meeting so it recovers. We'll measure the recovery time in the `hybrid_tuesday` run and send it to you.
- **Complaints:** "It's freezing in this meeting room" means the occupants are too cold, so relax cooling there. "The AC is blowing right on my face" means re-aim the VRF vanes. Explain what you did; Muse Spark is used for the explanations.
- **West door open** (`WES-ENTRANCE-DOOR`, BI 601): cooling a zone with its door propped open wastes energy. Your policy may relax that zone while the door is open.
- **19:00:** release every priority-10 write. Otherwise you cool an empty building after hours while the baseline floats to 30 °C, which costs SpotCool score.
- **Debounce your detections.** The BMS already waits 5 minutes before standby, and any Occupied ends it at once, so a missed person fails towards comfort. Still, avoid toggling a zone every step.

## 12. The demo scenario: `hybrid_tuesday` (built, WP5)

This is the story the video tells. **Don't hardcode it**: the point is that your agent perceives it. The people counts may change if fewer people are available for filming on Nov 2.

| Sim time | Core | North | South (meeting room) | East | West (entrance) |
|---|---|---|---|---|---|
| 07:00 | empty | empty | empty | empty | empty |
| 08:30 | 4 people | 2 | empty | empty (team works from home) | 1 at reception |
| 10:00–10:45 | 4 | 2 | empty | empty | **door propped open**, boxes carried in |
| 12:30–13:30, lunch | 1 | empty | empty | empty | 1 |
| 13:45 | | | **calendar shows a 14:00–15:00 booking** | | |
| 14:00–15:00 | 4 | 2 | **meeting, 5 people** | empty | 1 |
| 14:15 | | | **complaint**, voice and chat: "It's freezing in this meeting room." | | |
| 17:30 | 1 | 2 | empty | empty | 1 |
| 18:00 | empty | empty | empty | empty | empty |

Separately, a **live phone camera** segment shows the VRF louver demo (§6).

## 13. Fairness and honesty (what we will say in the video)

- **The baseline** is the same building and the same BMS, without occupancy sensors and without SpotCool.
- **BOPTEST's occupant heat follows its own fixed schedule,** not our cameras. So savings in zones the cameras show empty can be somewhat overstated, by up to the heat of the occupants who aren't there. We bracket this with the RC plant, whose people heat follows the scenario, and with an `all_hands_day` scenario where savings should be about 0.
- **Comfort is judged where people actually were** ("comfort where people were"). BOPTEST's own discomfort KPIs count discomfort on its fixed schedule, so they also penalize empty zones.
- **Every number** on screen will have a source. Please don't claim more than the scored run shows.

## 14. Shared rules

- **Credits and spend:** Person A owns the Meta credits. B spends nothing and uses only free, open-source and free-tier tools.
- **Pull requests:** opening one on this repo triggers two Claude review workflows that spend the repo's `ANTHROPIC_API_KEY`. B merges its own branches locally. The `world/main` → `dev` integration PR is planned together.
- **API keys** stay in the backend `.env`, never in the browser or the repo.
- **Camera frames and audio** are never stored on disk.

## 15. Changes since the earlier notes

| What changed | Effect on you |
|---|---|
| `wp/wp0-core-contracts` fast-forwarded to `f2d849b` | None to the interface. The fixes are world-side: BOPTEST read keys, and an exact `OCC-SENSOR` description |
| Standby hysteresis now sits **above** the active setpoint (after a review) | The airflow shut-off keeps working all day instead of only once. An empty zone floats from the setpoint to about 0.5–0.9 K above it |
| Standby also requires 06:00–19:00 | Standby ends exactly at 19:00. BOPTEST reports values one stamp late, so the setpoint alone would have run standby to 19:15 |
| Points without data carry fault flags | Check `statusFlags` and `reliability` |
| Release at 19:00; readings describe the step that ended | Covered in §5.4 and §11 |

## 16. What B needs from you

1. **The policy:** confirm that `OCC-SENSOR` plus `CLG-SP` at priority 10 works for you, with the heartbeat and the 19:00 release.
2. **Your endpoints:** the host and port (default `http://127.0.0.1:8002`) and the `/chat` and `/voice` handling as described in §9. Tell us whether you run on the same PC as the world (§5.5).
3. **Your dashboard events:** the `data` fields of `perception` (counts and masks per camera), `decision` and `explanation`, so WP7 can draw them.
4. **More read points?** Anything else you need from the BMS or the VRF unit. That's a contract change, so ask early.
5. **Integration slot:** a time on Nov 3 morning (IST) to run your agent against the world.
