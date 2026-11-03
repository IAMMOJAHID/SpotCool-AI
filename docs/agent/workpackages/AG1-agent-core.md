# AG1: the agent core (cameras → SAM 3.1 → the BACnet BMS), with proof that it acted

**Branch** `ag/ag1-agent-core`, from `agent/main`. **Worktree** `C:\Users\hussa\dev\wt\ag1-agent-core`. **Venv** `C:\Users\hussa\dev\venvs\ag1-agent-core`; set `UV_PROJECT_ENVIRONMENT` to it before every `uv` command. **Tool:** Claude Code, one builder. **The plan** is `C:\Users\hussa\.claude\plans\purrfect-wibbling-reef.md`, and the lane's status is `docs/agent/HANDOFF.md`.

## Goal

Build SpotCool's real agent: a new workspace package, `packages/agent` (`spotcool-agent`, import `spotcool_agent`), that runs against the world exactly as it would against a real building.
- **Each BMS step,** it looks at the five zone cameras, counts people with SAM 3.1, and drives each zone's occupancy sensor and cooling setpoint over BACnet/IP with the stub's proven policy.
- **It publishes** its perception, decisions and explanations to the dashboard, and acknowledges complaints on `/chat` and `/voice`.
- **On the world side,** the run report gains proof that an agent acted, so a run where no agent did anything can never be quoted.

This WP is the critical path to SpotCool's own quotable figure: AG2 rehearses it, and AG3 scores it on BOPTEST.

## Your lane (only these paths)

- `packages/agent/**`, which is new.
- `packages/world/src/spotcool_world/runner/report.py` and its tests, for the agent-activity section and blockers below. Also any world module those need, kept minimal: for example, recording the BMS watchdog's release (`packages/world/src/spotcool_world/transports/bacnet/bms_logic.py:191-217`) if it isn't recorded today.
- `packages/world/evals/**`, only if a shared helper must move. Prefer copying into the agent's evals.
- `pyproject.toml` (root), only if the workspace needs it; `uv.lock`.
- `CREDITS.md`: software rows for new dependencies.

**Out of the lane:** `scripts/video/**`, `video/**`, `apps/dashboard/**`, and `packages/contracts/**`. If a contract is missing, stop and say so in your report. Also `docs/status/**` and `docs/integration/**`, which are the lead's.

## Read first

1. `packages/world/AGENTS.md`: the engineering guide. Follow its code style, docstrings (Google), typing and test patterns.
2. `docs/integration/PERSON_A_HANDOFF.md` §4–§9: every interface you use, with addresses, points, priorities, the heartbeat, the event shapes and the 10 s rule.
3. `packages/world/src/spotcool_world/stub_agent/{worker.py,policy.py}`: the stub, which is a real bacpypes3 client. **Copy its BACnet helpers and policy semantics; don't import them.** That means `_command`, `_back_off`, `_beat`, `_release`, `_slots`, `_write`, `APDU_TIMEOUT_MS`, `APDU_RETRIES`, `PRECOOL_S` and `EMPTY_SETPOINT_C`.
4. `packages/contracts/src/spotcool_contracts/`: `load_point_map`, `ZONES`, priorities, `EventEnvelope` and `EventType`, `CameraCatalog`, `ScheduleInformation`, `ComplaintText`, `AudioFormat`.
5. **Person A's MVP,** read with `MSYS_NO_PATHCONV=1 git show origin/third_level_improvement:<path>`, frozen at `b653cbf`:
   - `app/spotcool/perception.py:268-387`: `_NUMBER`, `FRAME_MARKER`, `BOX_TOKEN`, `LEGACY_BOX`, `Detection`, `_normalized`, `parse_sam_output`, `_sam_image`;
   - `app/mvp_pipeline.py:76-87`: the client.

   **Never open a `.env` file.**
6. `packages/world/evals/conftest.py` (`run_world`, `free_ports`) and `packages/world/evals/test_golden.py` (`write_lags`, `saved_pct`, the 15.45 % RC band), for the eval.
7. `packages/world/tests/cameras/conftest.py:79` (`_count_people`): the colour-blob counter for synthetic frames, which becomes the agent's offline vision provider.

## Facts measured by the lead (Nov 3, AG0 probe)

- **SAM 3.1 works with the existing key,** through A's exact call (`openai` 3.42.1, `AsyncOpenAI(base_url="https://api.meta.ai/v1")`, `responses.create(model="sam-3.1", metadata={"include_confidence": "true"}, input=[{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "humans"}, {"type": "input_image", "image_url": "data:image/jpeg;base64,..."}]}])`).
  - **Latency:** p50 1.9 s, p95 about 3.2 s, with 5 calls running at once.
  - **Output** is `response.output_text`: `<0f>0<|box;x1=980;y1=126;x2=1014;y2=273;w=1920;h=1072;c=0.8358|><|mask;x=0;y=0;c=0.8358;data=...|>`, in pixel coordinates with the image size. An empty string means nobody.
  - **A real fixture** (text only) is at `C:\Users\hussa\AppData\Local\Temp\claude\c--Users-hussa-dev-SpotCool-AI\441270a4-d753-4379-ba6d-7751619bd543\scratchpad\ag0-sam-fixtures-real\meva_cafe_1280_humans.txt`. Copy it to `packages/agent/tests/fixtures/`.
- **SAM sees nobody in the world's synthetic silhouettes.** Real footage (MEVA, which the lead is preparing) is required for live runs. **Tests and CI therefore use the blob counter,** and nothing in CI calls the Meta API.
- **muse-wonder** answers in about 2.2 s (used in AG4, not here).
- **The key** is at `C:\Users\hussa\dev\secrets\spotcool-agent.env` (`META_API_KEY=...`), outside the repo. Read it only through settings, store it as `SecretStr`, and never log or print it.

## Decisions (binding)

### Package
1. **`packages/agent/pyproject.toml`:**
   - Name `spotcool-agent`, Python 3.11 or newer. A workspace member (the root's `packages/*` glob picks it up), with a `spotcool-agent = "spotcool_agent.runner.cli:main"` script.
   - **Runtime dependencies:** `spotcool-contracts` (workspace), `bacpypes3==0.0.110` (the world's pin), `httpx`, `fastapi`, `uvicorn[standard]`, `python-multipart`, `websockets`, `openai>=3.42,<4`, `pydantic-settings`, `loguru`, `numpy`, `pillow`. Use the world's version ranges where they exist.
   - **No extras;** the agent is one deployable.
   - **Development only:** `spotcool-world` with the extras the eval needs (`rc`, `bacnet`, `cameras`, `dashboard`, `calendar`, `occupants`).
2. **`spotcool_agent` never imports `spotcool_world`.** A test imports every agent module in a subprocess and asserts that `spotcool_world` is absent from `sys.modules`.
3. **Layout,** in pipecat's shape: base classes plus providers, transports, small pure processors.

   ```
   spotcool_agent/
     domain/geometry.py     Detection, parse_sam_output (A's, near-verbatim; credit A in the docstring)
     services/vision.py     VisionService base: async detect(jpeg: bytes, concept: str, timeout_s: float) -> Detections
     services/meta/client.py  one AsyncOpenAI per process (created inside the running loop), a global asyncio
                            limiter (5), a call ledger (model, purpose, latency, ok; never pixels), a per-run call cap
     services/meta/vision.py  SamVision (A's request; min_confidence default 0.3; no retries except one on 429)
     services/fake/blob.py    BlobVision (the world's colour-blob counter, ported), for tests, CI and synthetic frames
     services/fake/scripted.py  ScriptedVision (fixed counts per camera), for unit tests
     transports/bacnet.py   BacnetClient: own UDP port, devices addressed directly (no Who-Is), read, priority array,
                            write at priority 10, release (NULL); StepClock polls SIM-CLOCK (AV 950) at 4 Hz
     transports/world_http.py  camera catalog, snapshot bytes (memory only), room calendar (contracts models)
     transports/dashboard.py   /ws/agent publisher: EventEnvelope(source="agent"), at most 64 KB, at most 20/s
                            (token bucket), reconnect with backoff, bounded queue that drops the oldest, never blocks
     transports/server.py   FastAPI on 127.0.0.1:8002: POST /chat, POST /voice (multipart), GET /healthz, CORS per
                            A-handoff §9. Validate the body, queue it, return 202 at once. AG1's handler only publishes a
                            template chat reply and logs it (AG4 adds transcription and LLM replies)
     processors/occupancy.py  counts to occupied, empty or unknown per zone (unknown when a call failed or timed out)
     processors/policy.py   the stub's policy on perceived occupancy (decision 4)
     processors/explain.py  a template sentence per decision (zone display names, point, value, reason)
     actuator.py            write only on a change; back off from slots 1-9; never release a slot that isn't ours;
                            heartbeat task (BV 901, every HEARTBEAT_PERIOD_S); release everything at 19:00, on stop(),
                            on a crash, and in finally
     loops/building.py      the per-step loop (decision 5)
     settings.py            AgentSettings (pydantic-settings, env prefix SPOTCOOL_AGENT_; env_file from --env-file)
     observers/metrics.py   per-stage latency (p50/p95) and the ledger, logged as ASCII
     runner/cli.py          spotcool-agent run | probe | version
   ```
4. **The policy is the stub's, constants unchanged.**
   - **Empty zone:** `ZN-<Z>-OCC-SENSOR` Unoccupied and `ZN-<Z>-CLG-SP` `EMPTY_SETPOINT_C`, both at priority 10.
   - **Occupied zone:** `OCC-SENSOR` Occupied and `CLG-SP` released.
   - **A booked room** (from the calendar) is held as occupied from `PRECOOL_S` before the booking to its end.
   - **From 19:00,** everything is released.
   - **Unknown** keeps the zone's current command for 1 step, then counts as occupied.
   - **If SAM is down for a whole step** (every zone unknown), or the call cap is reached, release everything. Comfort comes first, and the figure honestly drops.
   - Back-off and release behave exactly as the stub's do.
5. **Timing of the building loop**, matching the stub's lag. The world never waits for the agent.
   1. `StepClock` sees `SIM-CLOCK` change to S, ignoring faulted or unreliable values.
   2. Wait `settle_s` (default 1.0; the eval may use less), so the camera shows the new scene.
   3. Fetch the 5 zone snapshots in parallel.
   4. Make one `detect(jpeg, "humans")` call per zone, all at once. Each call's timeout is `min(8 s, deadline - now)`, where `deadline = detection_time + step_wall_s - 2.5 s`. `step_wall_s` comes from the measured interval between `SIM-CLOCK` changes, starting at 15 s. Results after the deadline are discarded.
   5. Run the policy, then the actuator writes.
   6. Publish the events.

   Stamp the events exactly as the stub stamps its own: read how `write_lags` pairs a `decision` with its `bms_write`, and match it. The target is every write taking effect from the next step. Use `time.perf_counter` for every duration.
6. **Events** (`EventEnvelope`, `source: "agent"`): **the stub's shapes, without `stub`.**
   - `perception`: `people: {zone: count}`, plus `masks: {camera_id: [[[x, y] x4]]}` (each box as a rectangle polygon, in the snapshot's pixels);
   - `decision`: `zone`, `point`, `value` (`null` for a release), `priority`, `reason`;
   - `explanation`: `text`;
   - `chat`: `text`.

   Keep each envelope under 64 KB.
7. **Privacy and ground truth.**
   - Snapshots and audio exist only in memory. Nothing in `spotcool_agent` writes a file; the run log goes to stderr.
   - The agent connects only to `/ws/agent`, never to `/ws/events`, and reads no `scenario_state` or `camera_scene`.
   - Tests prove both. For example, an audit hook on `open` scoped to agent code, and a check of the URLs the dashboard transport dials.
8. **Windows and ops.**
   - ASCII logs (`degC`, `->`).
   - The agent's own BACnet device instance and UDP port: the stub uses device 1900 on the BMS port plus one, so pick another and say which.
   - Ctrl+C releases every slot and stops the heartbeat after the release (as the stub's `cancel_tasks` ordering does).
   - The sim epoch the agent uses (for 19:00 and bookings) is its own constant, with a test asserting it equals the world's.
9. **The CLI.**
   - **`spotcool-agent run`** takes `--world-host`, `--bms-port 47808`, `--http-port 8081`, `--dashboard-port 8090`, `--listen 127.0.0.1:8002`, `--vision meta|blob|scripted`, `--env-file`, `--sam-call-cap`, `--settle-s` and `--dry-run`. The dry run prints the resolved settings with the key masked.
   - **`spotcool-agent probe`** snapshots every zone camera once, runs the chosen vision provider, and prints the counts per camera and the latency. The lead runs it live.
   - **`spotcool-agent version`.**

### The world side: proof that an agent acted
10. **`report.py` gains an "Agent activity" section,** from the recorded run (the BMS's `bms_write` events carry `client`, `priority` and `accepted`):
    - accepted priority-10 writes to zone points (`ZN-*-OCC-SENSOR`, `ZN-*-CLG-SP`) on the SpotCool run, per client;
    - heartbeat-timeout releases. If the BMS doesn't record them today, record them minimally, for example as a `bms_write`-like event with a recognisable client or reason, and document it;
    - occupancy agreement: the agent's `perception` people per zone against the recorded scenario truth, as the share of zone-steps where occupied/empty agrees, aligned by sim time. The report computes it after the run; the agent never sees the truth.
11. **Two new quote blockers:**
    - no accepted priority-10 zone write on the SpotCool run;
    - any heartbeat-timeout release.

    Existing stub runs keep their stub blocker, and their writes count as activity. Update the report's tests and anything that asserted "Quotable" on fixtures.

### Tests and eval
12. **Unit tests:**
    - the parser: the real fixture, multi-frame and repeated ids, the legacy format, empty text;
    - `SamVision`, with the client faked: timeout, 429 once, the cap;
    - occupancy and the policy: a table of cases, including pre-cool edges, 19:00, unknown for 1 and 2 steps, and SAM down;
    - the actuator against a fake BACnet seam: write on change, back-off from 8, never releasing a slot that isn't ours, release ordering on stop;
    - the dashboard transport: rate limit, 64 KB, dropping;
    - `server.py`: 202 within the deadline, CORS preflight, validation;
    - settings: the key is never in `repr` or `--dry-run`;
    - no world import; no file writes.
13. **Integration tests on loopback against the world's real servers** (start them in-process, as the world's own tests do): BMS reads and writes with the priority array, back-off when a test client writes 23 °C at priority 8, the heartbeat, the camera catalog and snapshots, the calendar, `/ws/agent` publishing.
14. **The end-to-end eval** (`packages/agent/evals/`, marker `eval`, so it runs in CI's evals job):
    - The world in process on RC, `hybrid_tuesday`, **without** the stub, paced at speed 300 (or the fastest speed at which the agent's writes still land one step later; say which).
    - The agent in process, with `--vision blob` on the synthetic frames.

    Assert:
    - (a) the saving is within 0.5 pp of the stub's RC result under the same write timing. Compare with a stub run in the same eval, or with the golden 15.45 % if the lags match; say which;
    - (b) at least 98 % of the agent's writes take effect exactly one step after their decision (`write_lags`);
    - (c) the report's new agent-activity section shows its writes, and occupancy agreement is at least 98 % (the blob counter is exact on synthetic frames);
    - (d) no heartbeat-timeout release;
    - (e) everything is released by the 19:15 stamp.

    The eval must finish within the CI evals job's 15-minute timeout, together with the world's evals.
15. **The checks:**
    - `uv run ruff check packages scripts`;
    - `uv run ruff format --check packages scripts`;
    - `uv run pyright packages`;
    - `uv run pytest -W error::RuntimeWarning`;
    - `uv run pytest -m eval`;
    - `uv lock --check`.

## Docs

- **`packages/agent/AGENTS.md`:** the agent's engineering guide, in the shape of `packages/world/AGENTS.md`: overview, commands, architecture (the loops and their timing), services and providers, transports, rules (privacy, ground truth, actuation), testing, known limitations. Record the **deviation from pipecat:** loops instead of a frame pipeline, because each step is one bounded request and response.
- **A short `packages/agent/README.md`:** install, run, probe.

## Done when

- Every check above is green, including the end-to-end eval.
- `uv run spotcool-agent run --vision blob --dry-run` prints the settings with no key.
- The agent package imports no world module.
- The report prints "Agent activity" and refuses a run with no agent writes.
- **Commits on `ag/ag1-agent-core`:** `feat(agent): ...` for the package, `feat(report): ...` for the world side, `docs(agent): ...`, with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. **Don't push or merge.**

## Report back (under 700 words)

- what you built, file by file in one line each;
- the eval's numbers (saving, the stub comparison, lags, agreement, duration);
- every deviation from this brief, and why;
- what you couldn't verify;
- the check results.
