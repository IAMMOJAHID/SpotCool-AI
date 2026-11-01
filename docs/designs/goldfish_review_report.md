# Goldfish Annotated Review Report

**Target:** `C:\Users\hussa\.claude\plans\pasted-content-id-a80f-role-you-recursive-church.md` (full file, lines 1-95)

**Round 1.**
- **Reviewers:** `implementation_readiness` (completed), `codebase_details` (completed) and `zoom_out` (completed), each in an isolated context.
- **Standards:** `implementation-readiness-standard.md` for the first two, `zoom-out-standard.md` for zoom_out.
- **Result:**
  - 63 findings (3 Blocker, 45 Major, 15 Nit), merged into 22 issues below. The appendix lists every finding by ID.
  - Every issue is PROPOSE FIX, and three need information only you have, marked **Needs your input**.
  - No finding was contested, escalated or declined: each critique was checked against the cited files and held up.
  - The round is not clean.
- **Cap:** `CLAUDE.md` caps fix cycles at 3 rounds, which is stricter than the loop's own 5, so this loop ends at round 3. To converge, rounds 2 and 3 must both be clean.

**What the fixes do, in brief:**
- **Shrink the build:** one occupant, no SWEEP, one Muse Spark call, a setpoint-only BOPTEST sweep and one energy model.
- **Write down the contracts** both people build against: WebSocket messages, `RemoteCommand`, the Muse output schema and the Modal API.
- **Fix the constants:** fan table, jet decay, comfort inputs, camera geometry and timeouts.
- **Align with names already in the repo** (`pmv_engine.py`, `state_bus.py`, `src/digital_twin/`, `frontend/`, `tests/test_state_bus.py`).
- **Land the rule fixes on `dev` before any code is written.**

Plan link used below: [plan](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md).

## Prioritized Findings & AI Suggestions

### [Issue 1] [`codebase_details`, `zoom_out`, `implementation_readiness`] - Severity: Blocker
*   **Affected Scope:** [Lines 27-34](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L27-L34) (Decisions §5 Evidence chain, §6 Energy counter). Also L44, L46, L60, L71-73, L88, L93.
*   **Findings merged:** codebase_details-1-1 (Blocker); zoom_out-1-1, zoom_out-1-4, implementation_readiness-1-11, -1-12, -1-13, codebase_details-1-12 (Major).
*   **Discrepancy Description:**
    - The counter adds a percentage to a standby term that has no units.
    - The only BOPTEST comparison is baseline vs. setback and raise combined. That credits the setback savings to the raise, and the standby term then counts vacancy a second time.
    - The twin's watts (the rule formula minus η_spot) are a second, unsourced energy model whose constants are undefined.
    - BOPTEST's baseline is its own controller, not 24 °C.
    - The study design is left to the implementer, with no slot in the schedule: scenario, step, overrides, attendance, outputs and the go/no-go.
*   **Rationale:** `.claude/rules/digital_twin.md:11-13`. `temp/H/conversation_3.md:171, 456-461`. `resources/Building_Control_with_RL_using_BOPTEST.md:503-509` (occupancy is compiled into the emulator) and `:571-593` (baseline controller). Zero-Decision Rule. Zoom-out §2 YAGNI.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. A setpoint-only sweep isolates the one coefficient the counter uses, with no attendance seeding and no comfort recompute. A single energy model removes the double counting and traces every watt to a labelled constant.
*   ***Proposed Edit:***
    *   Location: Decisions §5-§6 (L27-34). Architecture L44 and L46. Files L60. Schedule L71-73. Verification L88. Fix list L93.
    *   Change:
        - **One energy model, in `src/digital_twin/twin.py`.** All constants are labelled "illustrative".
          - Fan power: QUIET 12 W, LOW 18 W, MED 26 W, HIGH 38 W, TURBO 50 W.
          - Compressor while on: `1200 W × max(0.15, 1 − k·(setpoint − 24.0))`.
          - OFF draws 5 W standby (`conversation_3.md:476`).
          - Baseline: the same AC at 24.0 °C and MED for the whole run, empty periods included, which is 1226 W.
        - **Counter:**
          - `state_bus.py` integrates both models every tick and publishes `saved_kwh` (kWh) and `saved_pct = 1 − E_actual/E_baseline` (%).
          - Setpoints below 24 °C produce negative savings, and the dashboard shows them that way.
          - There is no separate standby term.
        - **Where `k` comes from:** `data/boptest_kpis.json` if it exists, otherwise 0.06/°C (BEE, Issue 20). The dashboard shows which source is in use.
        - **BOPTEST sweep:**
          - **Runs:** two runs on one case, using the `peak_cool_day` scenario, 1-hour steps and a constant electricity price. Run A sets every zone cooling-setpoint override to 24.0 °C for all hours. Run B sets 26.0 °C.
          - **Coefficient:** `k = (ener_tot_A − ener_tot_B) / (2 × ener_tot_A)`. Report `tdis_tot` for both runs as is.
          - **Which case:** `multizone_office_simple_air` if the Nov 1 check passes, else `bestest_air`, else the BEE figure. The check passes when the case selects on the hosted service, `peak_cool_day` is accepted, and one 1-hour step returns in under 5 s.
          - **Override names:** record them from `GET /inputs` during that check.
          - **Output:** `data/boptest_kpis.json` = `{case, scenario, step_s, runs: {sp24: {ener_tot, tdis_tot}, sp26: {ener_tot, tdis_tot}}, k_per_c}`.
          - **Failures:** if a test ID dies, rerun that run.
          - **Label:** "k = % change in total HVAC energy per °C, applied to the twin's compressor term as an approximation."
        - **Delete:** the "Gotcha" bullet (L30), the 50% attendance and the comfort recompute.
        - **Keep:** "Comfort equivalence" (L28), as the pitch's cooling-effect number only. Fold "Why not BOPTEST live" into one line.
        - **Schedule:**
          - Oct 31: wire k = 0.06 into the counter so the energy bar never waits on BOPTEST.
          - Nov 1: the 30-minute check.
          - Nov 2: both runs, about 1 hour unattended.
          - Nov 3 AM: read the JSON.
        - **Rule file:** delete the whole ΔT/10 formula from `.claude/rules/digital_twin.md`, not just η_spot.

### [Issue 2] [`implementation_readiness`, `codebase_details`] - Severity: Blocker
*   **Affected Scope:** [Lines 23-24, 39-45](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L23-L45) (Vision path, Architecture). Also L56, L58, L61, L70-71, L86.
*   **Findings merged:** implementation_readiness-1-1 (Blocker); codebase_details-1-9 (Major), plus parts (a) and (b) of codebase_details-1-10.
*   **Discrepancy Description:**
    - None of the boundaries A and B build against in parallel has a message format: `/ws/ingest` framing, audio format, the `/ws/twin-state` envelope and fields, mask overlay data, trace and energy messages, a command/beep event, the Modal request and response, and the SAM 3.1 request and mask encoding.
    - The trust-boundary rules that `/security-review` checks are missing: `extra="forbid"` and a frame size cap.
*   **Rationale:** `.claude/commands/security-review.md:17`. `.claude/rules/backend.md:10`. Schedule L70 has B building against a mock WebSocket while A writes the schemas. Zero-Decision Rule (API design).
*   ***AI Suggestion:*** PROPOSE FIX. **Needs your input** for the SAM 3.1 and Muse Spark request shapes.
*   ***Justification:*** Valid. This is the main integration contract between the two people. Leaving it open guarantees rework at the Nov 1 integration.
*   ***Proposed Edit:***
    *   Location: new section "Contracts" after Architecture (after L47).
    *   Change:
        - **`/ws/ingest` (browser → backend):** binary messages only. Each is one JPEG frame, 640×480, quality 0.7, at most 200 KB. Larger frames are dropped and logged without their content. At most 5 frames per second.
        - **`/ws/audio` (browser → backend):** binary 16 kHz mono PCM16 little-endian, in 100 ms chunks (3,200 bytes). The backend re-encodes as Muse Voice Transcribe requires, then discards the buffers.
        - **`POST /complaint`:** `{"text": str}`, at most 200 characters. A typed complaint; it follows the same path as a transcript.
        - **`POST /ac-twin/command`:** a `RemoteCommand` used as a 30-second manual override (Issue 4).
        - **`/ws/twin-state` (backend → browser):** JSON `{"type", "t_ms", "data"}`, with these types:
          - `kinematic` (10 Hz): `{pan_deg, tilt_deg, pan_target_deg, tilt_target_deg, fan_rpm, occupant: {x_m, h_m, z_m} | null}`
          - `perception` (each vision result): `{status: "ok"|"empty"|"unknown", frame_w, frame_h, bbox: [x0,y0,x1,y1] | null, polygon: [[x,y],...] | null}`. The polygon has at most 64 points, in frame pixels.
          - `command` (each new composed command): `{command: RemoteCommand, beep: bool, source: "policy"|"rule"|"manual"}`
          - `thermo` (0.5 Hz): `{power, mode, setpoint_c, fan, return_air_c, pmv, watts, baseline_watts, saved_pct, saved_kwh, k_per_c, k_source: "BOPTEST"|"BEE"}`
          - `trace` (each policy decision): `{policy: "muse"|"rule", clothing, door_open, complaint, reason, latency_ms}`
        - **Modal depth:**
          - Request: `POST $MODAL_DEPTH_URL` with headers `Modal-Key` and `Modal-Secret` (proxy auth), and the raw JPEG as the body (`image/jpeg`).
          - Response: `{"w": 160, "h": 120, "depth_mm": "<base64 uint16 LE, row-major>"}`.
          - Model: Depth Anything V2 Metric-Indoor Small (`depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf`; verify on first deploy) on one T4 GPU.
        - **SAM 3.1:**
          - Text prompt `"person"`.
          - The backend converts the returned masks into a 160×120 binary mask (nearest-neighbour) for the depth median, and a polygon of at most 64 points for the overlay.
          - **Needs your input:** paste the SAM 3.1 and Muse Spark requests you tested (endpoint, model ID, request body, response and mask encoding). Nothing in the repo records them.
        - **Validation:** every inbound model uses `extra="forbid"`.

### [Issue 3] [`implementation_readiness`, `codebase_details`] - Severity: Blocker
*   **Affected Scope:** [Lines 17-19](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L17-L19) (Decisions §3). Also L28, L44, L51, L53-54, L82.
*   **Findings merged:** implementation_readiness-1-2 (Blocker); implementation_readiness-1-4, codebase_details-1-5, codebase_details-1-11 (Major).
*   **Discrepancy Description:**
    - The setpoint raise, and with it every savings number, depends on "jet air speed at their distance". Undefined:
      - fan levels, exit velocities and RPM;
      - the decay law, its floor and its cap;
      - what happens when the jet is off-axis;
      - what picks the fan;
      - the comfort inputs (RH, met, radiant temperature, default Clo, corrections);
      - the search grid, the infeasible case, how the bias is applied and any hysteresis;
      - out-of-range handling (NaN) and the library version.
    - The research holds conflicting, unsourced values (`conversation_3.md:254` vs `:451-455`) that the plan neither adopts nor rejects.
*   **Rationale:** `temp/H/conversation_3.md:168, 254, 451-455, 472`. `.claude/rules/backend.md:11`. Zero-Decision Rule ("selecting a dependency", "edge case").
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. Without these, `pmv_engine.py` cannot be written deterministically, and unstated constants would set the headline number.
*   ***Proposed Edit:***
    *   Location: replace Decisions §3 L17-18. Add the constants to Files (`pmv_engine.py`).
    *   Change:
        - **Fan table** (adopted from `conversation_3.md:451-455`, labelled illustrative), exit velocity per level: QUIET 550 RPM / 1.2 m/s, LOW 750 / 2.0, MED 950 / 2.9, HIGH 1200 / 3.8, TURBO 1450 / 4.8.
        - **Air speed at the occupant:**
          - `v = clamp(v_exit × min(1, 0.5/Z), 0.1, 0.8)` m/s. This is the research's 1/Z decay (`:168`) with a 0.5 m core.
          - The 0.8 m/s cap is ASHRAE 55's limit without occupant air-speed control; confirm it against the pinned library.
          - If the current, slewed louvers point more than 15° away from the occupant, use `v = 0.1` (still air).
        - **Comfort inputs:**
          - Air temperature = radiant temperature = the candidate setpoint (steady state).
          - RH 50%, because there is no humidity sensor. Met 1.1.
          - Clo by category: light 0.4, medium 0.6, heavy 1.0 (`:133`). Use 0.6 until the first answer and whenever the answer is unknown.
          - Raw air speed and Clo, with no relative-speed or dynamic-clothing corrections. This matches the ISO 7730 reference test.
        - **Search** (SPOT mode, every policy cycle):
          - Candidates: setpoints 16.0-30.0 in 0.5 steps × all five fan levels.
          - PMV: pythermalcomfort in ASHRAE 55 mode (`pmv_ppd(..., standard="ASHRAE")` in 2.x, `pmv_ppd_ashrae` in 3.x; confirm against the installed version and pin it). NaN means infeasible.
          - Pick: the lowest modeled power with |PMV| ≤ 0.5; on a tie, the lower fan.
          - If nothing is feasible: the smallest |PMV|, then the lower power. If every result is NaN: 26.0 °C / LOW.
        - **Bias:** `setpoint = snap_0.5(clamp(best + bias, 16, 30))`. Issue 5 defines the bias.
        - **Hysteresis:** quantize Z to 0.25 m before searching. A new (setpoint, fan) is committed only after it wins two cycles in a row.

### [Issue 4] [`zoom_out`, `implementation_readiness`, `codebase_details`] - Severity: Major
*   **Affected Scope:** [Lines 19-20](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L19-L20) (Decisions §3). Also L41-43, L52-53, L56, L81, L86.
*   **Findings merged:** zoom_out-1-2, implementation_readiness-1-3, codebase_details-1-6 (Major).
*   **Discrepancy Description:**
    - Two producers own different fields of one full-state `RemoteCommand`, and they run at different rates. Nothing merges them, and the diagram never routes pan/tilt into the twin.
    - `RemoteCommand` has no field list. `mode` overlaps `power`.
    - `POST /ac-twin/command` contradicts "angles belong to the kinematic controller only", and the manual verification step would be overwritten by the next loop tick.
*   **Rationale:** `README.md:72` (Canonical JSON State Bus). `.github/CODEOWNERS:20` (`state_bus.py`, Tier 2). `.claude/commands/security-review.md:17`.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. The repo already reserves `state_bus.py` for this role.
*   ***Proposed Edit:***
    *   Location: Decisions §3 L19-20. Architecture L41-43. Files.
    *   Change:
        - **Create `src/backend/state_bus.py`.** It holds the latest kinematic target, policy decision and perception.
        - **On each input change** (at most 10 Hz), it:
          - composes a full `RemoteCommand`;
          - clamps and snaps it;
          - applies it to the twin by direct call;
          - emits a `command` event only when the frame differs from the previous one.
        - **Beep:** `beep = true` when power, mode, setpoint or fan changed. Angle-only changes never beep.
        - **Manual override:** `POST /ac-twin/command` passes the posted frame through unchanged for 30 s, then the composer resumes.
        - **`RemoteCommand` fields:** `power: bool`, `mode: "SPOT"|"ECO"`, `setpoint_c` (16-30, a multiple of 0.5), `fan: QUIET|LOW|MED|HIGH|TURBO`, `pan_deg` (−45…45), `tilt_deg` (−60…0). When `power` is false, the other fields are ignored.
        - **Diagram:** draw geometry → state_bus ← policy, then state_bus → ACTwin.

### [Issue 5] [`zoom_out`, `codebase_details`] - Severity: Major
*   **Affected Scope:** [Lines 15-16, 19](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L15-L19) (Decisions §3). Also L55, L73.
*   **Findings merged:** zoom_out-1-3, codebase_details-1-4 (Major).
*   **Discrepancy Description:**
    - Muse Spark outputs a ±3 °C comfort bias, which is a temperature offset chosen by the LLM. That contradicts the plan's own "physics decides numbers".
    - The bias also has two owners.
*   **Rationale:** `REVIEW.md:26` ("Having an LLM guess temperature or fan offsets…") and `:38` (P0). `AGENTS.md:27-28` (Law 5). `.claude/rules/backend.md:12` (in-memory ΔT_bias updated on complaints). `conversation_3.md:170, 474`.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. As written, the Nov 4 `/code-review` would raise a P0 against the plan's own design.
*   ***Proposed Edit:***
    *   Location: Decisions §3 L15-16 and L19.
    *   Change:
        - **Rename the header** to "The LLM interprets; code decides". Muse Spark outputs no numbers: only `complaint ∈ {none, too_cold, too_hot, draft}`.
        - **`src/orchestrator/policy.py` applies fixed steps:**
          - too_cold: bias += 1.5 °C, and deflect.
          - too_hot: bias −= 1.5 °C.
          - draft: deflect, and cap the fan at LOW for 120 s.
        - **Bounds:** the bias is clamped to ±3.0 °C and kept in memory for the session.
        - **Deflect:** tilt +12° (toward horizontal, clamped to ≤ 0°) for 120 s.
        - **Delete** the "±15° offset" wording.

### [Issue 6] [`implementation_readiness`, `codebase_details`, `zoom_out`] - Severity: Major
*   **Affected Scope:** [Lines 16, 21, 42](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L16-L42) (Decisions §3, Architecture). Also L55, L59, L72-73, L84.
*   **Findings merged:** implementation_readiness-1-9, codebase_details-1-3 (Major); zoom_out-1-10 (Nit).
*   **Discrepancy Description:**
    - It's unclear whether this is one Muse Spark call or three, since `semantic.py`, MusePolicy and `voice.py` all face a model.
    - Undefined: the input state, field names and enums, the Clo format, the prompts, the timeout, handling of output that is valid but inconsistent, and the voice-to-intent path and its timing.
    - The fallback's door rule depends on the very call that failed.
*   **Rationale:** `conversation_3.md:133, 473-474`. `.claude/rules/backend.md:12` (complaints apply immediately). Zoom-out §2 Minimalist Data Flow.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. One call halves slow-loop latency and cost and gives one schema.
*   ***Proposed Edit:***
    *   Location: Decisions §3 L16 and L21. Architecture L42. Files L55 and L59.
    *   Change:
        - **One call, in `MusePolicy`** (`src/orchestrator/policy.py`):
          - It runs every 5 s, and also when a new transcript or typed complaint arrives (debounced 1 s, at most one call per 2 s).
          - Input: the latest JPEG plus `{occupant_present, z_m, clothing_last, door_open_last, transcript_since_last}`.
          - Output: `MuseIntent` (`extra="forbid"`): `{clothing: light|medium|heavy|unknown, door_or_window_open: bool, complaint: none|too_cold|too_hot|draft, reason: str ≤ 120 chars}`.
        - **Failure:** a 3.0 s timeout. On a timeout, invalid JSON or a schema failure, `RulePolicy` runs that cycle with complaint `none` and the last known clothing and door values.
        - **Mode** is computed by code (Issue 8), never by the LLM.
        - **Other modules:** `voice.py` only transcribes. Delete `semantic.py` from the plan.
        - **Prompt:** a constant in `policy.py`.

### [Issue 7] [`zoom_out`, `implementation_readiness`, `codebase_details`] - Severity: Major
*   **Affected Scope:** [Lines 16-17](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L16-L17) (Decisions §3). Also L23-25, L55, L73, L76.
*   **Findings merged:** zoom_out-1-5, implementation_readiness-1-6, codebase_details-1-7 (Major).
*   **Discrepancy Description:**
    - The schema assumes multiple occupants: SWEEP, "which occupant to serve", per-person bias and per-occupant EMA. Yet the demo has one person, and multi-occupant is second on the cut list.
    - Separate per-JPEG SAM calls return no persistent IDs, and no matching step is planned.
*   **Rationale:** `conversation_1.md:287` (cross-frame tracking needs video mode). Zoom-out §2 YAGNI.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. A single-occupant contract removes identity tracking and the SWEEP comfort math entirely.
*   ***Proposed Edit:***
    *   Location: Decisions §3 L16-17. §4 L25. Files L55. Cut order L76.
    *   Change:
        - **Serve one person:** the mask with the largest pixel area, which is usually the nearest person.
        - **Modes:** SPOT, ECO and OFF, with one bias.
        - **Remove** SWEEP, occupant IDs and "which occupant to serve".
        - **Add a "Not in this build" line:** multi-occupant sweep.

### [Issue 8] [`implementation_readiness`, `codebase_details`] - Severity: Major
*   **Affected Scope:** [Lines 16, 21](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L16-L21) (Decisions §3). Also L73, L76.
*   **Findings merged:** implementation_readiness-1-5 (Major), plus the mode-mapping part of codebase_details-1-6.
*   **Discrepancy Description:**
    - Only SPOT has a defined setpoint. ECO's setpoint and fan, and OFF's fields and flap position, are undefined.
    - The research disagrees with itself: 26 °C vs 26.5 °C vs 28 °C.
    - Demo beat 4 depends on ECO.
*   **Rationale:** `conversation_3.md:149, 475-476`.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: Decisions §3 L21.
    *   Change: code decides the mode, with precedence OFF > ECO > SPOT:
        - **OFF:** successful vision results show nobody for more than 10 s. `power=false`, the fan stops, the louvers slew to park (0°, 0°), and the unit draws 5 W.
        - **ECO:** a door or window has been reported open continuously for more than 30 s. 26.0 °C, fan LOW, louvers hold their last target.
        - **SPOT:** every other case. The setpoint comes from the Issue 3 search plus the bias, and the louvers track.
        - Each timer resets when its condition clears.

### [Issue 9] [`implementation_readiness`, `codebase_details`, `zoom_out`] - Severity: Major
*   **Affected Scope:** [Lines 21-26](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L21-L26) (Failure handling, Vision path). Also L42, L56-59.
*   **Findings merged:** implementation_readiness-1-8, codebase_details-1-8 (Major); zoom_out-1-14 (Nit).
*   **Discrepancy Description:**
    - Undefined:
      - queue vs. drop for frames that arrive faster than inference;
      - timeouts and failure handling for SAM 3.1, Modal (including cold starts) and voice;
      - rule precedence.
    - A vision outage looks like an empty room, so the AC can switch OFF mid-demo.
*   **Rationale:** `REVIEW.md:39` (unhandled exceptions on the WebSocket bus are P0). `.claude/commands/code-review.md:28-29`.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. This protects "never cut the tracking".
*   ***Proposed Edit:***
    *   Location: Decisions §4, a new "Failure handling" bullet.
    *   Change:
        - **Backpressure:** one vision request in flight. The newest frame wins, and older frames are dropped.
        - **Timeouts:** SAM 3.1 and Modal get 2.0 s each. If either fails or times out, the result is `unknown`, not empty:
          - hold the last target;
          - after 10 s of `unknown`, go to ECO and show "vision degraded" (never OFF on an error).
        - **Empty room:** only a successful SAM response with zero person masks counts as empty.
        - **Modal:** keep one container warm (`min_containers=1`) only while demoing or recording; otherwise it scales to zero.
        - **Voice:** on failure, drop the utterance and show "voice unavailable". The typed complaint box keeps working.
        - **Loops:** each iteration catches and logs its own exceptions, and the loop keeps running.

### [Issue 10] [`implementation_readiness`, `codebase_details`] - Severity: Major
*   **Affected Scope:** [Lines 24-25](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L24-L25) (Vision path). Also L57, L83, L94.
*   **Findings merged:** implementation_readiness-1-7, codebase_details-1-10 (Major).
*   **Discrepancy Description:**
    - The camera-to-mount transform can't be coded: camera height, pitch and mount offset are missing.
    - Also undefined:
      - the aim point (mask centroid, or a fixed chest height?);
      - whether the FOV is horizontal;
      - the frame resolution;
      - mask-to-depth resampling;
      - the EMA α and its order relative to the deadband;
      - where the constants live.
    - No replacement tilt formula is given.
*   **Rationale:** `.claude/rules/perception.md:9-13`. `README.md:33`. `conversation_3.md:248`.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. The L83 test only holds under unstated assumptions.
*   ***Proposed Edit:***
    *   Location: Decisions §4 L25. Files (`geometry.py`).
    *   Change:
        - **Intrinsics:**
          - Frame 640×480. Horizontal FOV 70°, so `fx = fy = 320/tan(35°) ≈ 457.0 px`. `cx = 320`, `cy = 240`.
          - Shortcut: these assume a typical laptop webcam; calibrate only if the angles look wrong.
        - **Camera pose:** height 1.0 m, pitch 0°. The virtual AC mount sits directly above it at 2.2 m.
        - **Aim point:** the centroid (u, v) of the served mask.
        - **Distance Z:** the median depth of the mask pixels on the 160×120 grid. The mask is resized with nearest-neighbour; fewer than 50 mask pixels means `unknown`.
        - **Back-projection:** `X = (u−cx)·Z/fx` (right is positive). `h = 1.0 − (v−cy)·Z/fy` (height above the floor).
        - **Angles:** `pan = atan2(X, Z)` (right is positive). `tilt = atan2(h − 2.2, Z)` (negative points down).
        - **Smoothing:** EMA with α = 0.3 on (X, h, Z) for each result, then compute the angles, then apply a 2° per-axis deadband against the current target.
        - **Constants** live in `src/backend/settings.py` and can be overridden from the environment.

### [Issue 11] [`implementation_readiness`, `codebase_details`] - Severity: Major
*   **Affected Scope:** [Lines 33, 44, 52-53](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L33-L53). Also L81.
*   **Findings merged:** implementation_readiness-1-10 (Major), plus parts (i) and (ii) of codebase_details-1-11 and the clamp question in implementation_readiness-1-3.
*   **Discrepancy Description:**
    - `ACTwin.reset()/step()` has no signatures or time-step semantics. A fixed 0.75° per tick equals 15°/s only if ticks arrive exactly on time.
    - The choice between clamping and rejecting is undecided.
    - The return-air model has no constants, and whether it feeds the policy is unstated.
*   **Rationale:** `.claude/rules/digital_twin.md:9`. `AGENTS.md:19-20`. `REVIEW.md:27`.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: Decisions §5 L33. Files (`twin.py`, `schemas.py`). Verification L81.
    *   Change:
        - **Interface:** `ACTwin.apply(cmd: RemoteCommand) -> None` sets targets. `ACTwin.step(dt_s: float) -> TwinState` advances the twin.
        - **Time step:** use measured elapsed time, with dt clipped to ≤ 0.1 s. Louvers move at most 15°/s × dt. RPM ramps at 400 RPM/s. The loop aims for 20 Hz.
        - **Clamp vs. reject:**
          - The HTTP boundary rejects out-of-range or off-grid values with a 422.
          - The state bus clamps and snaps before it builds a command.
          - Test both.
        - **Return-air temperature:** display-only; it feeds the dashboard's PMV and never the policy.
          - First-order model: it moves toward the setpoint while on and toward 30.0 °C ambient while off.
          - τ = 60 s (demo-accelerated), starting at 30.0 °C, labelled "model".

### [Issue 12] [`zoom_out`, `codebase_details`, `implementation_readiness`] - Severity: Major
*   **Affected Scope:** [Lines 49-62](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L49-L62) (Files). Also L86, L93.
*   **Findings merged:** zoom_out-1-6, codebase_details-1-13, codebase_details-1-19, codebase_details-1-22 (Major); implementation_readiness-1-18 (Nit).
*   **Discrepancy Description:**
    - **Rule paths:** `src/digital_twin/` is the React UI, so `digital_twin.md` (slew, power) steers the UI, while the Python twin gets no rule.
    - **CODEOWNERS:** Tier 2 lists `pmv_engine.py`, `state_bus.py` and `src/digital_twin/`. The plan creates `comfort.py`, no `state_bus.py`, and makes `src/digital_twin/` the UI.
    - **Tests:** the documented `tests/test_state_bus.py -k test_louver_angle` is never created.
    - **Package layout:** `src/acoustic/` sits outside every rule path.
    - **Markers:** no Create/Modify/Delete markers.
    - **Frontend:** the `package.json` location and the frontend components (including the required airflow streamlines) are unstated.
*   **Rationale:** `.github/CODEOWNERS:12-21`. `.claude/rules/digital_twin.md:2-5`. `.claude/rules/perception.md:2-4`. `.claude/rules/backend.md:11`. `CLAUDE.md:24, 32`. `AGENTS.md:69`. `README.md:89`. `.github/pull_request_template.md:15`. Zoom-out §3-§4.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. No code exists yet, so adopting the repo's names costs nothing.
*   ***Proposed Edit:***
    *   Location: replace the Files section (L49-62).
    *   Change:
        - **Create, project setup:**
          - `pyproject.toml`: pytest `pythonpath = ["."]`, `testpaths = ["tests"]`, plus ruff and mypy config.
          - `requirements.txt` and `.env.example`.
          - A root `package.json` whose `dev`/`build` scripts run `frontend/`, so `npm run dev` from the repo root keeps working.
          - `src/**/__init__.py`.
        - **Create, backend:**
          - `src/backend/`: `settings.py`, `schemas.py`, `state_bus.py` (Tier 2), `pmv_engine.py` (Tier 2), `main.py`.
          - `src/digital_twin/twin.py` (Tier 2): ACTwin and the energy model.
          - `src/orchestrator/policy.py` (Tier 1).
          - `src/perception/`: `geometry.py`, `vision.py`, `modal_depth.py`, `voice.py` (Tier 1).
          - `src/validation/boptest_study.py`, which writes `data/boptest_kpis.json`.
        - **Create, frontend** (`frontend/`, Vite + React + TypeScript), under `src/`:
          - `App.tsx` (the 4 quadrants);
          - `ws.ts`;
          - `Capture.tsx` (camera or replay, frame upload, mask overlay);
          - `AcUnit.tsx` (7 slats, the flap, and the airflow streamlines required by `digital_twin.md:14`);
          - `Remote.tsx` (the beep);
          - `Trace.tsx` and `EnergyBar.tsx`.
        - **Create, tests:** `tests/test_state_bus.py` (including `test_louver_angle`), `test_twin.py`, `test_pmv_engine.py`, `test_policy.py`, `test_geometry.py`, `fakes.py`.
        - **Create, docs:** `docs/designs/2026-10-31_spotcool_build_plan.md` (Issue 16).
        - **Modify:** the rule and doc files in Issue 15, `.gitignore` (Issue 21) and `docs/designs/SITEMAP.md`.
        - **Delete:** nothing on disk. `semantic.py`, `comfort.py` and `src/acoustic/` leave the plan only.

### [Issue 13] [`implementation_readiness`, `codebase_details`] - Severity: Major
*   **Affected Scope:** [Lines 51, 58, 64](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L51-L64) (Files).
*   **Findings merged:** implementation_readiness-1-15, codebase_details-1-14 (Major).
*   **Discrepancy Description:**
    - Plain `uvicorn` installs no WebSocket support, so both `/ws/*` endpoints would refuse connections.
    - Missing: numpy (needed for `np.median`), anything that loads `.env`, version pins, dev dependencies (mypy), environment variable names, the frontend toolchain, and the Modal image versions and checkpoint.
*   **Rationale:** `.claude/commands/security-review.md:13-14, 25` (pydantic-settings, pinned versions). `AGENTS.md:21-22`. `CLAUDE.md:21, 29`. `.gitignore:32, 39`.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. As listed, the first run fails, and `/security-review` would flag the rest.
*   ***Proposed Edit:***
    *   Location: Files, the `requirements.txt` bullet (L51) and the keys sentence (L64).
    *   Change:
        - **`requirements.txt`:** fastapi, `uvicorn[standard]`, pydantic, pydantic-settings, httpx, numpy, pythermalcomfort, modal, pytest, ruff, mypy. Pin each to the exact version installed in a fresh environment on Oct 31.
        - **`.env.example`** (repo root): `META_API_KEY`, `META_API_BASE_URL`, `MODAL_DEPTH_URL`, `MODAL_KEY`, `MODAL_SECRET`, `BOPTEST_URL=https://api.boptest.net`. `settings.py` loads `.env` from the repo root through pydantic-settings.
        - **Frontend:** Node 20 LTS, with versions locked by `package-lock.json`.
        - **Modal:** `modal_depth.py` pins torch and transformers in its image.

### [Issue 14] [`implementation_readiness`, `codebase_details`] - Severity: Major
*   **Affected Scope:** [Lines 62, 80-88](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L62-L88) (Files: tests, Verification). Also L70.
*   **Findings merged:** implementation_readiness-1-14, codebase_details-1-17, codebase_details-1-18 (Major).
*   **Discrepancy Description:**
    - **Untested:** the PMV search, RulePolicy thresholds and precedence, bias clamps, Law 3 mask-only median, EMA and deadband, idempotency, the energy math and the 0.5 °C grid.
    - **Not assertable as written:** "Clamped or rejected", the "≈" without a tolerance, and "drive the full loop" without pass criteria.
    - **Infrastructure:** no fakes and no offline/async decision. `pytest tests/` can't import `src`, so the Oct 31 "tests green" gate fails on configuration alone.
*   **Rationale:** `REVIEW.md:28, 44`. `evals/code_review_evals.json:19-28`. `.claude/commands/code-review.md:30`. Standard: Test Plan.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: replace Verification L80-84.
    *   Change:
        - **`test_twin.py`:**
          - A 45° move at dt = 0.05 reaches its target within 0.01° after 60 steps.
          - No step moves more than 0.75°.
          - dt is clipped to 0.1 s.
          - OFF draws 5 W.
          - Power at 26 °C with k = 0.06 equals 1200 × 0.88 W plus the fan.
        - **`test_state_bus.py`:**
          - `test_louver_angle`: the composed angles equal the kinematic target, clamped.
          - The same intent twice produces no new `command` event.
          - Only power, mode, setpoint or fan changes beep.
          - A POST override holds for 30 s.
          - An HTTP command at 40 °C gets a 422, while the composer clamps 31 to 30.
        - **`test_pmv_engine.py`:**
          - ISO 7730 reference case: −0.75 ± 0.05.
          - At Z = 2.5 m and Clo 0.6, the result has |PMV| ≤ 0.5, sits on the 0.5 grid, and has the lowest power among feasible candidates.
          - The infeasible case returns the candidate with the smallest |PMV|.
          - A bias of +1.5 is applied and clamped to 30.
        - **`test_policy.py`:**
          - Door open for 31 s gives ECO. Empty for 11 s gives OFF. Both at once give OFF. Timers reset.
          - MusePolicy falls back when a fake sleeps 4 s, and when it returns `{oops`.
          - The bias goes +1.5, then +3.0, then stays at +3.0.
          - A vision `unknown` is never treated as empty.
        - **`test_geometry.py`:**
          - Law 3 trap: person at 2.0 m inside the mask, wall at 5.0 m inside the bbox, so Z = 2.0.
          - Pan is positive to the right.
          - Tilt = −21.80° ± 0.05 at h = 1.2 m, Z = 2.5 m.
          - A 1.5° change leaves the target unchanged; a 2.5° change updates it.
        - **`fakes.py`:** async fakes for the Meta, Modal and BOPTEST clients. Tests run offline, and async code is tested with `asyncio.run`.
        - **Replay pass criteria:** with the camera off, the recorded clip produces `perception: ok` results, louver tracking, and at least one `command` event.

### [Issue 15] [`zoom_out`, `implementation_readiness`, `codebase_details`] - Severity: Major
*   **Affected Scope:** [Lines 13, 74, 90-95](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L90-L95) (Decisions §1, Nov 4 row, Fix along the way).
*   **Findings merged:** zoom_out-1-7, implementation_readiness-1-16, implementation_readiness-1-17, codebase_details-1-20, codebase_details-1-21 (Major); codebase_details-1-28 (Nit).
*   **Discrepancy Description:**
    - **Timing:** the fix list exists so stale rules stop steering the code, yet it is scheduled for Nov 4, after the code is written.
    - **Coverage:** it misses REVIEW.md, code-review.md, the CI prompt, security-review.md, backend.md, GEMINI.md, CLAUDE.md:52, the workflow guides, the README tilt and the evals fixture.
    - **Wording:** the reworded loop rule would still forbid the plan's own networked vision loop.
    - **Base branch:** review rules are read from the base branch, so fixes on the feature branch never reach the review.
*   **Rationale:** `REVIEW.md:4, 28-29, 38`. `.claude/commands/code-review.md:25`. `.github/workflows/claude-code-review.yml:22-29, 43-44`. `.claude/commands/security-review.md:22`. `.claude/rules/backend.md:11`. `.claude/rules/perception.md:13-14`. `GEMINI.md:16-17`. `CLAUDE.md:52`. `README.md:33`. `docs/workflow/CLAUDE_WORKFLOW_GUIDE.md:117-119, 130, 144, 189`. `docs/workflow/AGENT_COLLABORATION_GUIDE.md:54`. `evals/code_review_evals.json:59`. All were confirmed in the repo.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. The Elephant re-checked every cited line.
*   ***Proposed Edit:***
    *   Location: Fix along the way (L90-95). Schedule rows Oct 31 and Nov 4.
    *   Change:
        - **When:** make every rule and doc fix in one Oct 31 morning commit, before any code, and merge it to `dev` the same day.
        - **Files:** all the files listed under Rationale. `temp/H/whiteboard_and_rubric_analysis.md:178` also gets the fix, but it is git-ignored, so the change stays local.
        - **New loop rule:** "The 20 Hz twin tick and the vision loop never await Muse Spark or voice calls. Vision runs as fast as its two parallel calls return (2-5 Hz)."
        - **New tilt rule:** `tilt = atan2(h − 2.2, Z)`.
        - **New power rule:** the Issue 1 model.
        - **Evals:** update the tilt range in the fixture. Skip the benchmark re-run during the freeze.
        - **Nov 4:** remove "model-name/rule fixes" from that row.

### [Issue 16] [`zoom_out`, `codebase_details`] - Severity: Major
*   **Affected Scope:** [Line 13](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L13) (Decisions §1).
*   **Findings merged:** zoom_out-1-8, codebase_details-1-2 (Major), plus the spec part of implementation_readiness-1-17.
*   **Discrepancy Description:** "This plan is the spec", but the plan lives in a per-user folder outside the repo. Teammate B, Antigravity, the PR template and the conformance checks in `/code-review` and CI can't read it. `SITEMAP.md` is empty.
*   **Rationale:** `docs/designs/SITEMAP.md:5-6`. `REVIEW.md:74-76`. `.claude/commands/code-review.md:14`. `.github/pull_request_template.md:7`. `.github/workflows/claude-code-review.yml:46-47`.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: Decisions §1 (L13). Schedule, Oct 31.
    *   Change: once approved, copy the plan to `docs/designs/2026-10-31_spotcool_build_plan.md`, add a SITEMAP row, and include both in the Oct 31 rules commit. PRs link it in their Design section.

### [Issue 17] [`codebase_details`, `implementation_readiness`] - Severity: Major
*   **Affected Scope:** [Lines 66-74](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L66-L74) (Schedule). Also L5.
*   **Findings merged:** codebase_details-1-15 (Major); implementation_readiness-1-21, codebase_details-1-26 (Nit).
*   **Discrepancy Description:**
    - Only Oct 31 has an exit gate. The frontend is never built or checked.
    - The Nov 1 work is unsequenced: B's overlay depends on A's SAM path.
    - There is no integration checkpoint.
    - Schedule times have no timezone.
*   **Rationale:** Standard: Phasing & Checkpoints. `CLAUDE.md:42-43`. `AGENTS.md:36-37`.
*   ***AI Suggestion:*** PROPOSE FIX. **Needs your input:** confirm the team's timezone.
*   ***Justification:*** Valid. The proposed times assume the team works in IST; that fact isn't in any file.
*   ***Proposed Edit:***
    *   Location: Schedule table and the note under it.
    *   Change:
        - **Daily gate:** every day ends with `pytest`, `ruff check src/`, `mypy src/` and `npm run build` passing on `feature/spotcool-dev`, pushed.
        - **Checkpoints:** Nov 1 18:00, the dashboard renders live `twin-state` with mocked perception. Nov 2 18:00, end to end with live vision.
        - **Sequencing:** B builds the overlay against the `perception` message contract (Issue 2) with a mock until A's SAM path lands.
        - **Timezone:** all times IST, if confirmed. The deadline is 07:30 IST Nov 5 = 21:00 ET Nov 4.

### [Issue 18] [`codebase_details`, `zoom_out`] - Severity: Major
*   **Affected Scope:** [Lines 70, 74](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L70-L74) (Schedule).
*   **Findings merged:** codebase_details-1-16 (Major); zoom_out-1-12 (Nit).
*   **Discrepancy Description:**
    - There is no PR or merge step, and no decision about which branch to submit. Judges opening the default branch would see only scaffolding.
    - With one code owner, a required code-owner review can block merging your own PR.
*   **Rationale:** `CLAUDE.md:42-43`. `AGENTS.md:36-37`. `.github/CODEOWNERS:5`. Branches `dev`, `main` and `feature/spotcool-dev` all exist on origin.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: Schedule rows Oct 31 and Nov 4.
    *   Change:
        - **Oct 31:** check the branch protection on `dev` and `main`. If it requires code-owner review, decide between teammate approval and an admin bypass.
        - **Nov 4:** merge `feature/spotcool-dev` → `dev` → `main` by 21:00 IST, tag `submission-2026-11-04`, and submit the `main` URL by 23:00 IST. That leaves 8.5 hours of buffer.

### [Issue 19] [`zoom_out`, `codebase_details`] - Severity: Major
*   **Affected Scope:** [Line 76](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L76) (Cut order).
*   **Findings merged:** zoom_out-1-9 (Major); codebase_details-1-27 (Nit).
*   **Discrepancy Description:**
    - Voice is cut first, even though it uses a Meta model and carries a demo beat. The offline BOPTEST study, which already has fallbacks, is cut third.
    - Cut features leave demo beats with no substitute trigger.
*   **Rationale:** The rubric weights Meta tooling and Demo at 25% each, against Technical Depth at 20% (`temp/H/assets/meta_hackathon_judging_rubric.jpeg`). `conversation_1.md:289`.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid.
*   ***Proposed Edit:***
    *   Location: L76.
    *   Change: cut in this order:
        1. The BOPTEST sweep (falls back to BEE k).
        2. Live voice capture (the beat survives through the typed complaint box).
        3. Clothing and door detection (falls back to dashboard toggles for "jacket on" and "door open").
        - Never cut the twin, the tracking or the energy bar.
        - Multi-occupant is already out of scope (Issue 7).

### [Issue 20] [`implementation_readiness`, `codebase_details`] - Severity: Nit
*   **Affected Scope:** [Lines 31, 34](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L31-L34) (Decisions §5-§6). Also L74.
*   **Findings merged:** implementation_readiness-1-20, codebase_details-1-23, codebase_details-1-24 (Nit).
*   **Discrepancy Description:**
    - "BEE 6%/°C" and "BEE's 24 °C default" carry no citation; the research has 6% only as an unsourced rule of thumb.
    - The repo's "up to 60% waste" headline confuses HVAC's share of electricity (40-60%) with waste.
*   **Rationale:** `conversation_3.md:171`. `gemini_task.md:26, 36, 85`. `README.md:4, 10`. `AGENTS.md:5`. `REVIEW.md:24` ("It lies").
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid, and it matters for judges. The Elephant knows the BEE figures from its training (the Bureau of Energy Efficiency's 2018 advisory on a 24 °C default for AC temperature), but that isn't evidence until it's saved in the repo.
*   ***Proposed Edit:***
    *   Location: L31, L34 and the fix list.
    *   Change:
        - **BEE citation:** "Bureau of Energy Efficiency (India), 2018 advisory: AC default 24 °C; each 1 °C higher saves about 6% of electricity."
        - **Oct 31 task:** save the source page to `resources/bee_ac_default_temperature.md`. If it can't be found, label 0.06 "rule of thumb (unsourced)".
        - **Headline:** in the rules commit, replace "up to 60%" in `README.md` and `AGENTS.md` with "HVAC is 38-50% of commercial electricity (EIA CBECS Table E5)", plus the model's own saved % and its k source.

### [Issue 21] [`zoom_out`, `implementation_readiness`, `codebase_details`] - Severity: Nit
*   **Affected Scope:** [Lines 23, 64, 87](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L23-L87) (Capture, keys and privacy, replay verification). Also L39, L71.
*   **Findings merged:** zoom_out-1-13, implementation_readiness-1-19, codebase_details-1-25 (Nit).
*   **Discrepancy Description:**
    - The plan cites Law 6 while requiring a recorded mp4 of the room.
    - Recording method, storage location and audio are unstated.
    - `.gitignore` has no video patterns, so the clip could be committed and published.
*   **Rationale:** `AGENTS.md:29-30` (Law 6). `.claude/commands/security-review.md:21`. `.gitignore:1-63`.
*   ***AI Suggestion:*** PROPOSE FIX. **Needs your decision:** it adds an exception to a project invariant.
*   ***Justification:*** Valid. Only you can approve a Law 6 exception.
*   ***Proposed Edit:***
    *   Location: Decisions §4 L23. The privacy sentence on L64.
    *   Change:
        - **Recording:** the team records one consented, video-only clip with the Windows Camera app and keeps it outside the repo.
        - **Loading:** the dashboard loads the clip through a file picker (an object URL).
        - **`.gitignore`:** add `*.mp4`, `*.webm` and `*.mov`.
        - **Law 6 exception, stated in the plan:** "This consented test clip is the only exception to AGENTS.md Law 6. Live frames and audio are never written to disk."
        - **In replay,** the voice beats use the typed complaint box.

### [Issue 22] [`zoom_out`] - Severity: Nit
*   **Affected Scope:** [Lines 8-9, 32, 82](file:///C:/Users/hussa/.claude/plans/pasted-content-id-a80f-role-you-recursive-church.md#L8-L9) (Context, §5, Verification).
*   **Findings merged:** zoom_out-1-11 (Nit).
*   **Discrepancy Description:** "your key", "Changes after 'is there a better alternative?'", "your original pick" and "I checked this by hand" refer to a conversation that the plan's future readers won't have.
*   **Rationale:** Zoom-out §0. `agents/zoom_out.md:12`.
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Justification:*** Valid. Once the plan becomes the shared spec (Issue 16), human readers need these restated.
*   ***Proposed Edit:***
    *   Location: L8, L9, L32, L82.
    *   Change:
        - L8: "confirmed working with your key" → "verified by the team with the hackathon API key".
        - L9: replace the bullet with "Where this plan departs from the research: …".
        - L32: "your original pick" → "running BOPTEST live".
        - L82: delete "I checked this by hand".

## Appendix: all round-1 findings (stable IDs)

| ID | Severity | Target lines | Summary | Issue |
|---|---|---|---|---|
| zoom_out-1-1 | Major | 27-32, 34, 46, 60, 71-73 | BOPTEST office study confounded, double counted, unscheduled | 1 |
| zoom_out-1-2 | Major | 19-20, 41-43, 56, 86 | Nothing merges angles and intent into RemoteCommand | 4 |
| zoom_out-1-3 | Major | 15-16, 19, 55, 73 | LLM chooses a °C bias (Law 5, REVIEW.md P0) | 5 |
| zoom_out-1-4 | Major | 33-34, 44, 53, 93 | Two energy models; ΔT/10 formula and T_ambient unsourced | 1 |
| zoom_out-1-5 | Major | 16-17, 23-24, 55, 73, 76 | Multi-occupant schema for a one-person demo | 7 |
| zoom_out-1-6 | Major | 53-62, 93 | File layout ignores the repo's names, rule paths and CODEOWNERS tiers | 12 |
| zoom_out-1-7 | Major | 13, 74, 90-95 | Review charter grades the old design; fixes late and incomplete | 15 |
| zoom_out-1-8 | Major | 13 | Spec lives outside the repo | 16 |
| zoom_out-1-9 | Major | 76 | Cut order drops voice before the offline study | 19 |
| zoom_out-1-10 | Nit | 21, 42, 55, 59 | Three model modules for one call; fallback depends on the failed call | 6 |
| zoom_out-1-11 | Nit | 8-9, 32, 82 | Wording depends on the authoring conversation | 22 |
| zoom_out-1-12 | Nit | 70-74 | No merge or release step; self-merge may be blocked | 18 |
| zoom_out-1-13 | Nit | 23, 39, 64, 71, 87 | Replay mp4 vs Law 6; no video pattern in .gitignore | 21 |
| zoom_out-1-14 | Nit | 23, 26 | No backpressure rule for frames | 9 |
| implementation_readiness-1-1 | Blocker | 23-24, 39-45, 56, 58, 61, 70-71, 86 | No message contracts for WS, ingest, Modal, SAM | 2 |
| implementation_readiness-1-2 | Blocker | 17, 19, 28, 44, 53-54 | Jet speed, fan table, decay, fan selection, limits undefined | 3 |
| implementation_readiness-1-3 | Major | 16, 19-20, 52-53, 81 | RemoteCommand fields, two writers, clamp vs reject | 4 |
| implementation_readiness-1-4 | Major | 15-18, 33, 51, 54-55, 82, 86 | Setpoint search inputs, infeasible case, bias, hysteresis, version | 3 |
| implementation_readiness-1-5 | Major | 16, 21, 73, 76 | ECO, OFF and SWEEP undefined | 8 |
| implementation_readiness-1-6 | Major | 16, 19, 21, 23, 25, 55, 76 | Occupant identity across frames undefined | 7 |
| implementation_readiness-1-7 | Major | 24-25, 57, 83, 94 | Camera-to-mount transform and smoothing undefined | 10 |
| implementation_readiness-1-8 | Major | 21, 23-26, 56-58 | Queueing, timeouts, cold start, failure vs empty | 9 |
| implementation_readiness-1-9 | Major | 16, 19, 21, 42, 55, 59, 70, 72-73, 84 | Muse call count, schema, timeout, voice path | 6 |
| implementation_readiness-1-10 | Major | 33, 44, 53 | ACTwin signatures and return-air model undefined | 11 |
| implementation_readiness-1-11 | Major | 34, 46, 53, 61, 73, 93 | Counter reference, units, owner; two energy figures | 1 |
| implementation_readiness-1-12 | Major | 29-31, 46, 60, 71-73, 88 | boptest_study.py not codeable | 1 |
| implementation_readiness-1-13 | Major | 29, 34, 46, 72, 88 | Combined run inflates %/°C; vacancy counted twice | 1 |
| implementation_readiness-1-14 | Major | 62, 70, 80-88 | Missing tests; weak assertions; POST overwritten | 14 |
| implementation_readiness-1-15 | Major | 24, 51, 58, 61, 64 | uvicorn WS extras, numpy, .env, pins, versions | 13 |
| implementation_readiness-1-16 | Major | 74, 90-95 | Rule fixes after the code; gaps; loop-rule conflict | 15 |
| implementation_readiness-1-17 | Major | 13, 74, 90-95 | Review grades old base-branch rules; spec not in docs/designs | 15 |
| implementation_readiness-1-18 | Nit | 49-62, 64, 70, 80 | pytest path, .env.example, names, components, streamlines | 12 |
| implementation_readiness-1-19 | Nit | 23, 39, 64, 71, 87 | Replay recording, storage, audio, Law 6 | 21 |
| implementation_readiness-1-20 | Nit | 31, 34 | BEE figures uncited | 20 |
| implementation_readiness-1-21 | Nit | 68-76 | Exit criteria, integration checkpoint, branch plan, voice fallback | 17 |
| codebase_details-1-1 | Blocker | 27-34, 46, 72-73, 76 | Savings math wrong; confounded k; two energy models | 1 |
| codebase_details-1-2 | Major | 13, 74 | Spec outside repo; conformance checks can't find it | 16 |
| codebase_details-1-3 | Major | 16, 19, 21, 42, 55, 59, 73 | Muse output schema, call count, Clo format, timeout | 6 |
| codebase_details-1-4 | Major | 13, 16, 55 | LLM °C bias vs REVIEW.md P0 and Law 5 | 5 |
| codebase_details-1-5 | Major | 17-19, 51, 54, 82 | PMV search under-specified | 3 |
| codebase_details-1-6 | Major | 16, 19-20, 41-43, 56, 86 | No merger for the full-state frame; mode mapping | 4 |
| codebase_details-1-7 | Major | 16-17, 23-24, 55, 59, 76 | Occupant IDs undefined | 7 |
| codebase_details-1-8 | Major | 21, 23-26, 42, 58-59 | Only Muse has a fallback; failure vs empty; precedence | 9 |
| codebase_details-1-9 | Major | 23, 39-45, 56, 61, 70 | No ingest or twin-state contract; forbid extras; size cap | 2 |
| codebase_details-1-10 | Major | 8, 24-25, 57-58, 83 | SAM/depth contracts; aim point, pose, FOV, EMA | 10 |
| codebase_details-1-11 | Major | 33, 44, 52-54, 81 | Physics constants; clamp vs reject; tick timing | 3 |
| codebase_details-1-12 | Major | 29-31, 46, 60, 71-72, 88 | BOPTEST study design left open | 1 |
| codebase_details-1-13 | Major | 49-62, 86, 90-95 | No Create/Modify/Delete markers; package.json location | 12 |
| codebase_details-1-14 | Major | 51, 57-58, 64 | Dependency and environment setup incomplete | 13 |
| codebase_details-1-15 | Major | 66-74, 78-88 | No daily gates; frontend unchecked; Nov 1 sequencing | 17 |
| codebase_details-1-16 | Major | 70, 74 | No PR, merge or submitted-branch decision | 18 |
| codebase_details-1-17 | Major | 62, 80-84 | Missing test cases | 14 |
| codebase_details-1-18 | Major | 62, 80, 84 | No fakes; offline/async undecided; pytest import path | 14 |
| codebase_details-1-19 | Major | 53, 61, 90-95 | digital_twin.md scoped to the UI, not the Python twin | 12 |
| codebase_details-1-20 | Major | 13, 90-95 | Stale rules missing from the fix list | 15 |
| codebase_details-1-21 | Major | 74, 90-95 | Fixes scheduled after the code they steer | 15 |
| codebase_details-1-22 | Major | 54, 56, 62, 85, 90-95 | Plan names don't match wired names and tiers; mypy optional | 12 |
| codebase_details-1-23 | Nit | 31, 34 | BEE figures uncited | 20 |
| codebase_details-1-24 | Nit | 34, 74 | "Up to 60%" headline unsourced | 20 |
| codebase_details-1-25 | Nit | 23, 64, 87 | Replay clip storage vs Law 6 | 21 |
| codebase_details-1-26 | Nit | 5, 66-74 | Schedule has no timezone | 17 |
| codebase_details-1-27 | Nit | 16, 73, 76 | Cut order leaves beats without substitutes | 19 |
| codebase_details-1-28 | Nit | 13, 52, 90-95 | Evals tilt fixture; benchmark re-run skipped | 15 |
