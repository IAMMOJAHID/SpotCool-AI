# AG7: the live demo screen, with a Meta call trace in the terminal and a four-pane layout

**Branch** `ag/ag7-demo-trace` from `agent/main` (`3db6825`). **Worktree** `C:\Users\hussa\dev\wt\ag7-demo-trace`. **Venv** `C:\Users\hussa\dev\venvs\ag7-demo-trace`. **One builder.** **Requested by the user on Nov 4:** the demo's first part shows four panes at once.

```text
+---------------------------+---------------------------+
| dashboard                 | camera dataset videos     |
| (floor plan, energy,      | (the raw MEVA feeds)      |
|  the agent's feed)        |                           |
+---------------------------+---------------------------+
| SAM 3.1 output:           | terminal: every Meta call |
| the same feeds with the   | (model, endpoint,         |
| boxes SAM found           |  request id, in and out)  |
+---------------------------+---------------------------+
```

## Facts from the lead's live probe (Nov 4)

- `POST https://api.meta.ai/v1/responses` with `sam-3.1` returns HTTP headers including `x-request-id` (a UUID), `x-ratelimit-limit-requests`, `x-ratelimit-remaining-requests` and `x-route`.
- The SDK exposes the request id as `response._request_id`. The parsed response also carries `response.id` (`resp_…`), `response.model`, and `usage.images_processed`.
- One call on a 1280x720 MEVA frame took 3.9 s.

## Binding decisions

### 1. A Meta call trace, `META` lines in the agent's log

Files: `services/meta/client.py`, `vision.py`, `stt.py`, `llm.py`.

Every Meta request logs one line when it's sent and one when it ends, at INFO, ASCII only, starting with `META`. The shapes (adjust the wording, keep the fields):

```text
META -> sam-3.1 | POST https://api.meta.ai/v1/responses | count humans on cam-cor | in: JPEG 1280x720, 52 KB + text "humans" (include_confidence)
META <- sam-3.1 | 200 in 1.87 s | request-id 2bf0123a-... | response resp_6aeb... | out: 2 boxes [(785,376)-(905,691) c=0.93, (...) c=0.41] -> kept 2 (c >= 0.30) | rate limit 58/60 left
META -> muse-voice-transcribe-1.0 | POST https://api.meta.ai/v1/asr/transcribe | voice complaint | in: WAV 5.0 s, 160 KB (comfort keywords)
META <- muse-voice-transcribe-1.0 | 200 in 4.4 s | request-id ... | out: 54 characters
META -> muse-wonder-1.0 | POST https://api.meta.ai/v1/responses | classify a complaint | in: prompt 312 characters
META <- muse-wonder-1.0 | 200 in 1.2 s | request-id ... | response resp_... | out: intent "cold"
META x  sam-3.1 | timeout after 3.5 s | count humans on cam-sou
```

- **Use the full endpoint:** `base_url` plus the path.
- **Request id:** from `response._request_id`, or from the httpx response's `x-request-id` for STT. If it's missing, write `request-id -`.
- **Rate limit:** from the headers, if they're there. Use `with_raw_response` only if `_request_id` alone can't give you the headers; keep it simple.
- **Never log** the key, image bytes, base64 data, audio, a complaint's words, the transcript, the LLM's prompt text or its reply text (privacy, AGENTS.md §1). Lengths, sizes, counts, box pixel coordinates, confidences, the intent label, the status and the ids are fine.
- **Box output:** list at most 6 boxes, then "+N more".
- **The ledger.** Add `request_id: str | None` to `Call` in the ledger.
- **A setting,** `meta_trace: bool = True` (`SPOTCOOL_AGENT_META_TRACE`), and the CLI flag `--no-meta-trace`. The fakes log no `META` lines.
- **Tests:**
  - the lines' shape, with the fake OpenAI client and transport the existing tests use;
  - no base64, key or complaint words in the captured log;
  - `request_id` lands in the ledger;
  - a timeout logs a `META x` line.

### 2. Dashboard views for the panes

Files: `apps/dashboard/src/config.ts`, `App.tsx`, `CameraWall.tsx`, `styles.css`.

- **`?panels=a,b,c`** shows only those panels, filling the window. The ids are the panels' existing `Panel id`s: `cameras`, `kpis`, `energy`, `agent`, and so on (list them in config's doc comment). Without the parameter, the layout is unchanged.
- **`?masks=off`** draws the camera tiles without SAM's boxes, for the raw feeds pane.
- **A 960x540 browser source** must read well with:
  - `panels=cameras`: the five zone tiles, large, with the count;
  - `panels=` the floor plan, the energy chart and the agent's feed.

  Check this at that size with the existing Playwright e2e (add cases), and keep the screenshots out of the repo.

### 3. No RC saving on screen

- When the run's plant is RC, the dashboard shows no energy-saved % and no kWh figures, in the KPI cards and the energy chart's numbers. The power lines stay.
- In their place, the note "RC test plant: a model for testing, no saving is quoted". RC figures are on `docs/release/numbers.md`'s never-quote list, and the live takes run on RC.
- BOPTEST and replayed runs are unchanged.
- Add unit tests.

### 4. OBS: a `Quad` scene

File: `video/obs/scene_collection.json`.

The scene has four 960x540 sources at the four corners:

| Corner | Source | URL or window |
|---|---|---|
| Top left | browser | `http://127.0.0.1:8090/?mode=video&panels=floor,energy,agent` (use the real ids) |
| Top right | browser | `...&panels=cameras&masks=off` |
| Bottom left | browser | `...&panels=cameras` |
| Bottom right | window capture | the terminal window titled `SpotCool agent` |

Keep the other scenes as they are. `storyboard_check.py` and its tests must still pass.

### 5. Runbook

Add "The live quad take" to `docs/runbooks/video-takes.md`:
1. Open a terminal titled `SpotCool agent`, with a font of at least 16 pt (Git Bash: `echo -ne "\033]0;SpotCool agent\007"`).
2. Start the agent first: `--vision meta --env-file C:/Users/hussa/dev/secrets/spotcool-agent.env --world-host 127.0.0.1`.
3. Then start the world on RC, with the MEVA footage and the loopback variables, at speed 60.
4. Start recording at sim 08:15, so the 08:30 arrivals show: the boxes appear, the zones flip, the writes go out. Record about 70 s.

Don't change `video/storyboard.yaml` or the narration. The user is still deciding the cut.

## Out of scope

- Real segmentation masks (SAM returns them, but we draw boxes).
- A contract change.
- Any change to the policy, actuation or timing.

## Checks

The full set in `CLAUDE.md` (ruff, format, pyright, pytest with `-W error::RuntimeWarning`, `pytest -m eval`, `uv lock --check`, storyboard check, and the dashboard's `npm ci`, build, lint, test and e2e if it's set up), with `UV_PROJECT_ENVIRONMENT=C:/Users/hussa/dev/venvs/ag7-demo-trace`.

**No live Meta or BOPTEST calls.** The lead runs the live check.

## Done when

- The checks are green.
- There are commits on `ag/ag7-demo-trace`: `feat(agent): META call trace`, `feat(dashboard): panels and masks views, no RC saving`, and `docs(video): the Quad scene and its runbook`. Each ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Nothing is pushed.
- The report is under 400 words.
