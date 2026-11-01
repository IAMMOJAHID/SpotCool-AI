# WP5: Scenarios, room calendar, scripted occupants

**Tool:** Claude Code. **Branch:** `wp/wp5-scenarios`, cut from `world/main` (`a24ec06`).
**Depends on:**
- WP0 (core) and contracts v0.1;
- WP1, for the day convention in `services/boptest/catalog.py`;
- WP2, whose BMS reads `ScenarioStateFrame` for the West door.

WP4 has **not** merged. Build the calendar as a router and test it on a bare FastAPI app; WP4 owns the shared `transports/http/server.py`.

## Goal
The film script of the demo day: who is in which zone and when, the entrance door, the meeting booking, and the complaints. It must be deterministic and replayable: the same scenario and the same ticks always give the same frames.

## Your lane (only these paths)
- `packages/world/src/spotcool_world/scenarios/`: `schema.py`, `loader.py`, `linter.py`, `compiler.py`, `library/*.yaml`.
- `processors/scenario.py`, `transports/http/calendar.py`, `workers/occupant_worker.py`.
- `runner/cli.py`: add the `lint` subcommand only. `run` belongs to WP11.
- `services/settings.py`: add `footage_dir` only (decision 11).
- `frames/frames.py`: add the `audio` field to `ComplaintCueFrame` only (decision 10).
- `packages/world/pyproject.toml`: add the `calendar` and `occupants` extras only (decision 12), then run `uv lock` and commit `uv.lock`.
- `packages/world/tests/scenarios/`, `examples/foundational/08_scenario_lint.py`, and the changelog fragment `packages/world/changelog/+wp5-scenarios.added.md`.

`packages/contracts/` is read-only. If you think a contract must change, stop and report it.

## Decisions already made (binding)

### Time
1. **`day` is BOPTEST's 0-based day index.** It's the same number as `scenario_days` in `services/boptest/catalog.py` (`peak_cool_day` = 199).
   - Simulation seconds = `day * 86400 + seconds since midnight`.
   - All three library scenarios use day 199, BOPTEST's peak cooling day, so they share weather and baseline.
   - `SimClock.day_of_year` is 1-based, so it reports 200 for this day. That's expected.
2. **Every time in a scenario is on the step grid:** a multiple of `step_s` after midnight (`WorldSettings.step_s`, default 900 s). With the default, minutes are 00, 15, 30 or 45.
   - The world ticks every 15 simulated minutes, so a change written as 10:40 would really happen at 10:45.
   - The linter therefore reports an off-grid time as an error.
   - Points in time (`at`, `from`, `booked_at`) satisfy `start <= t < end`; only a `to` may equal `end`. The world ticks while the time is before `end`, so its last tick is `end − step`.
3. **A tick at `t` starts the step `[t, t + step)`.** `TickFrame.sim_time_s` is the step's start. `state_at(t)` is the state in force from `t`, and an entry applies from its `at` (inclusive).
4. **Cues fire on the tick whose window `(previous tick, this tick]` contains them.** The first tick's window is `[start, start]`. With on-grid times, a cue fires exactly once, on the tick at its own time.

### Calendar dates
5. **A simulation time maps to a calendar date-time as `datetime(2026, 1, 1) + timedelta(seconds=t_s)`.** Keep that in one constant and one function.
   - Day 199 is therefore 2026-07-19.
   - Graph `dateTimeTimeZone` values look like `"2026-07-19T14:00:00"` with `timeZone: "UTC"`. "UTC" labels the simulation's local clock, so there's no daylight saving.
   - The ICS feed uses the same times with a `Z` suffix.
   - This matches the BMS's `SIM-CLOCK` (seconds since 1 January 00:00), so Person A can convert either way.
   - The lead has documented the rule in `spotcool_contracts/calendar.py` and in the Person A handoff.

### State and payloads
6. **`ScenarioStateData` lists all five zones on every tick, in `Zone` order.**
   - `door_open` is `True`/`False` for a zone that has a door contact in the point map (West: BI 601 `WES-ENTRANCE-DOOR`), and `None` for every other zone. WP2's BMS shows it on BI 601.
   - `clothing` comes from `clothing` events. It holds from the event's `at` until the next clothing event for that zone, and reads `None` whenever the zone has 0 people.
   - `active_events` holds the events in force, as `"door_open:<zone>"` and `"booking:<room>"` (during the booked time).
7. **People counts are the occupants visible in that zone's clip** (WP8's shot list), so camera counting can be scored against the ground truth.
   - `empty` 0, `cor_few` 1, `cor_many` 4, `nor_few` 2, `sou_meeting` 5, `sou_complaint` 5, `wes_few` 1.
   - `wes_door_open` 1: the receptionist. The two people carrying boxes pass through.
   - If the Nov 2 filming has fewer people, the lead updates the numbers.
8. **Scenes** are `empty`, `few`, `many`, `meeting`, `complaint` and `door_open`.
   - A zone's clip file is `<zone>_<scene>.mp4`, WP8's naming (for example `sou_meeting.mp4`).
   - The linter warns when `people` doesn't fit the scene: `empty` needs 0, `few` 1–3, `many` 4 or more, `meeting` 2 or more, and `complaint` 1 or more. `door_open` takes any count.
9. **Bookings carry `booked_at`**, the time the booking appears in the calendar (default: the scenario's start). Before that time, the room's schedule doesn't list the booking. The hybrid Tuesday's 14:00 meeting appears at 13:45.
10. **A voice cue's audio file travels on the world-internal frame, not in the contract payload.** Add `audio: str | None = None` to `ComplaintCueFrame`: a file name under `footage_dir/audio/`. The dashboard payload `ComplaintCueData` stays as it is.
11. **`footage_dir`:** add `footage_dir: Path | None = None` to `WorldSettings`.
    - Env `SPOTCOOL_WORLD_FOOTAGE_DIR`; the usual location is `C:/Users/hussa/dev/footage`, outside the repo.
    - WP4's clip library will reuse it.
    - WP8's `scripts/footage/extract_audio.py` already writes the complaint as `<footage_dir>/audio/south_complaint.wav` in `VOICE_FORMAT`.
12. **Extras:** `calendar = ["fastapi>=0.115,<1"]` and `occupants = ["httpx>=0.27,<1"]`.
    - The scenario engine, linter and processor need only the core dependencies (pydantic, PyYAML).
    - Importing `spotcool_world` and `spotcool_world.scenarios` must work without either extra. Follow the pattern that `tests/plants/test_optional_extras.py` checks.

## DSL (`scenarios/schema.py`, Pydantic; files are YAML)
```yaml
name: hybrid_tuesday
description: Half the staff work from home; the East team is fully remote today.
day: 199                 # BOPTEST day index (peak_cool_day's peak day)
start: "07:00"
end: "19:00"
zones:                   # every zone must be listed; entries in time order
  cor:
    - {at: "07:00", scene: empty, people: 0}
    - {at: "08:30", scene: many, people: 4}
  eas:
    - {at: "07:00", scene: empty, people: 0}
events:
  - {type: door_open, zone: wes, from: "10:00", to: "10:45"}
  - {type: booking, room: meeting-south, from: "14:00", to: "15:00",
     subject: "Quarterly review", booked_at: "13:45"}
  - {type: complaint, zone: sou, at: "14:15", channel: voice,
     text: "It's freezing in this meeting room.", audio: "south_complaint.wav"}
  - {type: clothing, zone: sou, at: "14:30", clothing: heavy}
```

## Components
1. **`scenarios/loader.py`, `linter.py` and `compiler.py`.**
   - The **loader** keeps each item's line number (from PyYAML node marks) for the linter's messages.
   - The **linter** reports findings as `file:line: level: message`.
     - **Errors:**
       - an unknown zone or room, or a missing zone;
       - times not increasing within a zone, or outside `start`–`end`;
       - an off-grid time (decision 2);
       - `from` not before `to`;
       - overlapping door events in one zone, or a door event in a zone without a door contact;
       - `booked_at` after the booking's `from`.
     - **Warnings:**
       - `people` that doesn't fit the scene (decision 8);
       - a voice cue whose audio file is missing, or `footage_dir` unset (it falls back to chat);
       - with a clip directory given, a `<zone>_<scene>.mp4` that isn't there.
   - The **compiler** returns a `CompiledScenario` with:
     - `start_s` and `end_s`;
     - `state_at(t_s) -> ScenarioStateData`;
     - `cues_between(t0_s, t1_s)`;
     - `bookings_at(t_s)`: per room, the visible bookings as `ScheduleItem`s with `status: busy`;
     - the booking change times.
2. **`processors/scenario.py`:** `ScenarioProcessor(compiled)`. On each `TickFrame` it pushes these frames, in this order, each stamped with the tick's `sim_time_s`:
   - a `ScenarioStateFrame`;
   - a `CalendarFrame` per room, on the first tick and whenever that room's visible bookings change;
   - a `ComplaintCueFrame` for each cue in the tick's window (`delivered=False`, with `audio` set for voice cues);
   - then the tick itself.

   Every other frame passes through unchanged.
3. **`transports/http/calendar.py`:**
   - **A small store** holds the latest `CalendarData` per room and the latest simulation time.
     - It's fed with `CalendarFrame`s, either directly or from a subscription to `WORLD_FRAMES_TOPIC`, where `FramePublisher` publishes them. (`BusPublisher` carries only dashboard envelopes.)
     - WP11 does the wiring.
   - **`calendar_router(store) -> fastapi.APIRouter`:**
     - `GET /calendar/rooms/{room}/schedule?start=&end=` returns `ScheduleInformation` for the items that overlap the window. `start` and `end` are ISO date-times on the simulation calendar (decision 5). Without them, the window is the current simulation day.
     - `GET /calendar/rooms/{room}.ics` returns hand-written ICS text (no new dependency).
     - An unknown room returns 404.
4. **`workers/occupant_worker.py`:** `OccupantWorker`, a `BaseWorker`. It reads `ComplaintCueFrame`s from a bus subscription and delivers each one to `WorldSettings.agent_base_url`.
   - **Chat:** `POST /chat` (`AGENT_CHAT_PATH`) with a `ComplaintText` JSON body:
     - `source: occupant-sim`;
     - `room_hint`: the room in that zone from `ROOMS`, else `None`;
     - the cue's `sim_time_s`.
   - **Voice:** `POST /voice` (`AGENT_VOICE_PATH`) as multipart, with `audio` (the WAV, `audio/wav`) and `meta` (the `ComplaintText` as a JSON string).
     - Before sending, check the WAV header against `VOICE_FORMAT` with stdlib `wave`.
     - If the file is missing, unreadable or in another format, fall back to chat and say why in `detail`.
   - **Transport:** httpx with a 10 s timeout and 2 retries on connection errors and 5xx.
   - **Input and result:** it reads its cues from `WORLD_FRAMES_TOPIC`. After each delivery, it publishes a `complaint_cue` dashboard envelope with `delivered` and `detail` on the `"events"` topic.
   - **Privacy:** audio bytes are read, sent and dropped. They're never copied, cached or logged (AGENTS.md Law 6).
5. **Library** (`scenarios/library/`). All three run on day 199, 07:00–19:00.
   - **`hybrid_tuesday.yaml`**, matching `docs/footage/shot_list.md`:

     | Zone | Timeline (`at` scene people) |
     |---|---|
     | cor | 07:00 empty 0; 08:30 many 4; 12:30 few 1; 13:30 many 4; 17:30 few 1; 18:00 empty 0 |
     | nor | 07:00 empty 0; 08:30 few 2; 12:30 empty 0; 13:30 few 2; 18:00 empty 0 |
     | sou | 07:00 empty 0; 14:00 meeting 5; 14:15 complaint 5 (to the meeting's end: the clip ends with the jacket on); 15:00 empty 0 |
     | eas | 07:00 empty 0 |
     | wes | 07:00 empty 0; 08:30 few 1; 10:00 door_open 1; 10:45 few 1; 18:00 empty 0 |

     Events:
     - the West door is open 10:00–10:45;
     - the `meeting-south` booking 14:00–15:00, "Quarterly review", `booked_at` 13:45;
     - the South voice complaint at 14:15, "It's freezing in this meeting room.", audio `south_complaint.wav`;
     - South clothing `heavy` at 14:30 (the jacket goes on in the complaint clip).
   - **`all_hands_day.yaml`**, the honesty check (savings should be about 0):
     - every zone is empty at 07:00;
     - at 08:30: cor many 4, nor few 2, sou meeting 5, eas few 2, wes few 1;
     - every zone is empty at 18:00;
     - no events.

     WP8 filmed no occupied East clip, so the clip check warns about `eas_few.mp4`. WP4 decides the fallback.
   - **`empty_friday.yaml`:** only Core is occupied, few 1 from 09:00 to 15:00. Every other zone is empty all day, and there are no events.
6. **`runner/cli.py`:** `spotcool-world lint [SCENARIO ...] [--clips DIR] [--step-s S]`.
   - A scenario is a library name or a path to a YAML file. With none given, it lints the whole library.
   - It prints every finding, and exits 1 if there's any error, otherwise 0.
7. **`examples/foundational/08_scenario_lint.py`:** lints the library and prints the compiled hybrid Tuesday at 08:30, 10:15 and 14:15. Each snapshot shows the zones, the door, the active events, the cues in that tick's window, and the visible bookings.

## Tests (`packages/world/tests/scenarios/`)
- **Linter:** catches each error and warning class, with the right line number.
- **Day and grid:** `start_s` for day 199 at 07:00 is `199 * 86400 + 25200`; an off-grid time is an error.
- **State:** `state_at` at boundaries (inclusive at `at`); the `door_open` and `clothing` rules; all five zones every time.
- **Cues:** fire exactly once over a run of ticks, including a cue at the start time.
- **Processor:** the frame order on a tick; for the hybrid Tuesday, a `CalendarFrame` only on the first tick and at 13:45.
- **Calendar:**
  - the JSON matches `ScheduleInformation`;
  - the booking is absent before 13:45 and present from 13:45;
  - the date mapping (day 199 → `2026-07-19T14:00:00`);
  - 404 for an unknown room;
  - the ICS passes a minimal check (BEGIN/END pairs, DTSTART/DTEND).
- **Occupant worker,** against a mock agent (`httpx.MockTransport`): the chat and voice payload shapes, retries, fallback to chat for missing and wrong-format audio, and the published result frame.
- **Fixtures:** WAV files are generated with stdlib `wave` (silence or a tone), never real voices.
- **Extras:** importing the core works without the `calendar` and `occupants` extras.

## Done when
- **The four checks are green** with all extras installed:
  - `uv run ruff check packages`
  - `uv run ruff format --check packages`
  - `uv run pyright packages`
  - `uv run pytest packages`
- `uv run spotcool-world lint` exits 0 for the three library scenarios (warnings allowed, no errors).
- `08_scenario_lint.py` runs and prints the three snapshots.
- The changelog fragment is written, and nothing outside your lane changed except `uv.lock`.
