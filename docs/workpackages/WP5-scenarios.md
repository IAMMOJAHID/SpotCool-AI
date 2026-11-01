# WP5: Scenarios, room calendar, scripted occupants

**Tool:** Antigravity. **Branch:** `wp/wp5-scenarios`. **Depends on:** WP0, contracts, and WP4's `transports/http/server.py`. If WP4 hasn't merged, build the router against a stub app.
**Directories:** `packages/world/src/spotcool_world/scenarios/`, `transports/http/calendar.py`, `workers/occupant_worker.py`, `processors/scenario.py`, `tests/scenarios/`, `examples/foundational/08_scenario_lint.py`.

## Goal
The film script of the demo day: who is in which zone and when, the entrance door, the meeting booking, and the complaints. It must be deterministic and replayable.

## DSL (`scenarios/schema.py`, Pydantic; files are YAML)
```yaml
name: hybrid_tuesday
description: Half the staff work from home; East team is fully remote today.
day: 199                 # BOPTEST day of year (peak_cool_day centre, about 18 July)
start: "07:00"
end: "19:00"
zones:                   # every zone must be listed; entries are sorted by time
  cor:
    - {at: "07:00", scene: empty, people: 0}
    - {at: "08:30", scene: many, people: 4}
  eas:
    - {at: "07:00", scene: empty, people: 0}
events:
  - {type: door_open, zone: wes, from: "10:00", to: "10:40"}
  - {type: booking, room: meeting-south, from: "14:00", to: "15:00", subject: "Quarterly review"}
  - {type: clothing, zone: sou, at: "14:25", clothing: heavy}
  - {type: complaint, zone: sou, at: "14:20", channel: voice,
     text: "It's freezing in this meeting room.", audio: "south_complaint.wav"}
```

## Components
1. **`scenarios/loader.py`, `linter.py` and `compiler.py`.**
   - The **linter** reports errors and warnings with file and line:
     - unknown zone or room;
     - times not increasing, or outside start/end;
     - overlapping door events in the same zone;
     - `people` that don't fit `scene` (warning);
     - a missing audio file (warning; it falls back to the chat channel);
     - a scene missing from the clip library, when one is given.
   - The **compiler** produces `CompiledScenario` with:
     - `state_at(t_s) -> ScenarioStateData`;
     - `cues_between(t0, t1)`;
     - `bookings()`;
     - start and end in BOPTEST seconds (`(day-1)*86400 + clock`).
2. **`processors/scenario.py`:** `ScenarioProcessor(compiled)`. On each `TickFrame` it pushes, in this order:
   - a `ScenarioStateFrame`;
   - a `CalendarFrame` on the first tick and whenever bookings change;
   - a `ComplaintCueFrame` for each cue in `(previous tick, this tick]`;
   - then the tick.
3. **`transports/http/calendar.py`:** a router on the shared HTTP app.
   - `GET /calendar/rooms/{room}/schedule?start=&end=` returns `ScheduleInformation`.
   - `GET /calendar/rooms/{room}.ics` returns hand-written ICS text (no new dependency).
   - Sim days map to calendar dates in the year 2026 (day 199 is 2026-07-18). Times are the sim's local clock, labelled `timeZone: "UTC"` to avoid daylight-saving conversions. Document this.
4. **`workers/occupant_worker.py`:** `OccupantWorker` takes `ComplaintCueFrame`s from the bus or the pipeline and delivers them to the agent at `WorldSettings.agent_base_url`.
   - **Chat:** `POST /chat` with a `ComplaintText` JSON body.
   - **Voice:** `POST /voice` as multipart, with an `audio` WAV of 16 kHz mono s16le from `footage_dir/audio/`, and `meta` holding a `ComplaintText` JSON string.
   - httpx with a 10 s timeout and 2 retries. If audio is missing, fall back to chat.
   - Publishes a `COMPLAINT_CUE` event with `delivered` and `detail`.
   - Audio bytes are read, sent and dropped; never copied or logged (Law 6).
5. **Library** (`scenarios/library/`):
   - `hybrid_tuesday.yaml`: the exact timeline in the plan's Appendix A.
   - `all_hands_day.yaml`: every zone occupied 08:30–18:00, no complaints. This is the honesty check: savings should be about 0.
   - `empty_friday.yaml`: only Core has 2 people, 09:00–15:00.

## Tests
- The linter catches each error class.
- `state_at` at boundary times (inclusive at `at`).
- Cue windows don't double-fire across ticks.
- Calendar JSON matches the contract, and the ICS parses with a minimal check (BEGIN/END pairs, DTSTART/DTEND).
- The occupant worker against a mock agent (httpx `MockTransport`): chat and voice payload shapes, retry, fallback to chat.
- Tests build their WAV fixtures with stdlib `wave`, never from real voices.

## Done when
- The README checks are green.
- `spotcool-world lint` passes all three library scenarios. Add the CLI subcommand in `runner/cli.py`, coordinating with the lead.
- `08_scenario_lint.py` prints the compiled hybrid Tuesday at 08:30, 10:15 and 14:20.
