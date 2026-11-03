# WP9: The demo video

**Tool:** Claude Code. This follows the user's rule; the earlier brief named Antigravity. **Branch:** `wp/wp9-video`, cut from `world/main` at `c1ef1c7`.
**Depends on:**
- every merged WP;
- the live eval run `eval-live-hybrid-20261103-122941`;
- `docs/release/numbers.md`;
- the take runbooks in `docs/runbooks/`.

## Goal
One video **under 3:00** (target 2:50), at 1080p30 in H.264 with AAC audio, captions burned in, and a separate `.srt`.

It's built reproducibly from sources in git, and the user supplies only what code can't:
- the filmed clips;
- the phone and VRF take;
- the narration voice;
- one music track;
- the upload.

Until those exist, a complete **draft cut** builds from placeholders, and every rebuild is one command.

## Your lane (only these paths)
- `video/`: sources in git. `video/raw/`, `video/takes/` and `video/renders/` are git-ignored.
- `scripts/video/` and its tests.
- The root `.gitignore`: the `video/` ignores only.
- `CREDITS.md`: the video tools' rows.
- `docs/runbooks/video-takes.md`: point it at the new scripts.

`packages/` and `apps/` are frozen. If the video needs a world or dashboard change, stop and report it.

## Decisions already made (binding)
1. **The storyboard is the single source of truth.** `video/storyboard.yaml` holds beats, each with:
   - `id`, `start` and `end`;
   - `type`: `title`, `motion`, `screen` or `phone`;
   - `source`: a composition, a take or a clip;
   - `narration_id` and `on_screen_text`;
   - `sources`: ids in `video/sources.yaml`.

   `scripts/video/storyboard_check.py` fails when:
   - the total runs past 2:58;
   - beats overlap or leave gaps;
   - a beat's narration would need more than 150 words a minute;
   - an on-screen number has no source id.
2. **The beats** follow plan §7:
   1. **the hook:** fewer people come in, but the cooling keeps its schedule, with the published figures worded as `docs/release/numbers.md` words them (lead amendment, Nov 3);
   2. **how SpotCool plugs in:** CCTV, the BMS and complaints;
   3. **the BACnet BMS in YABE:** an operator override at priority 8, and the agent backing off (WP10);
   4. **the hybrid Tuesday on the dashboard:** a replay of the live eval run;
   5. **the louvers following a person,** on the phone camera and the VRF, filmed live;
   6. **the results:** BOPTEST, SpotCool against the baseline;
   7. **rollout and privacy;**
   8. **the close.**
3. **Every number comes from `docs/release/numbers.md`,** with its source id.
   - **The results card is generated:** `scripts/video/results_card.py <run_id>` reads the saved run through `runner/report.py` (`load_run`, `check_energy`, `quote_blockers`). Nobody types its numbers.
   - **Its label comes from the verdict:**
     - a stub-agent run says "Upper bound with perfect perception (stub agent, in simulation)";
     - only a run whose verdict is "Quotable" may say "SpotCool saved".
   - The default run is `eval-live-hybrid-20261103-122941`, at 19.94 %. **Never 21.14 %.**
4. **The narration** is `video/narration.md`.
   - About 140 words a minute, one paragraph per beat, plain and calm.
   - It states the limitation once: BOPTEST's occupant heat follows its schedule, and the result is an upper bound from a stub agent with perfect perception.
   - The user records it to `video/raw/narration.wav`, in Audacity or on a phone.
   - Until then, `scripts/video/tts_placeholder.py` renders a placeholder with Windows' built-in speech (`System.Speech` via PowerShell; nothing to install), so the timing and the cut can be built now.
5. **The motion graphics** live in `video/motion/`, built with HyperFrames. Use the installed `hyperframes` skills.
   - Compositions: `title`, `hook_stats`, `three_connections`, `architecture`, `rollout_privacy`, `results_card` (decision 3) and `close`.
   - They render deterministically to `video/renders/`.
   - Verify HyperFrames' licence from its package metadata. If it isn't usable, fall back to HTML and CSS captured with Playwright (the installed Edge) and ffmpeg, and say so.
6. **Screen takes are scripted.**
   - **`video/obs/scene_collection.json`:** the scenes `Dashboard` (a browser source at 1920×1080 of `http://127.0.0.1:8090/?mode=video`), `Dashboard+Camera`, `YABE` (window capture) and `PhoneCam`.
   - **`scripts/video/take.py`:** an obs-websocket v5 client written on `websockets`, which is already installed, so no new dependency. The password comes from `OBS_WS_PASSWORD`. It:
     - switches scenes at the storyboard's times;
     - starts and stops recording;
     - for the dashboard beat, starts `spotcool-world replay <run_id> --wait-viewer 60 --speed <x>`.
   - Unit-test it against a fake obs-websocket server.
7. **Captions:** `scripts/video/captions.py` builds the SRT from `narration.md` and the storyboard's timings, at most 42 characters a line and 2 lines a caption. The final cut burns them in, and the `.srt` is kept.
8. **The assembly is code, not a manual edit.** `scripts/video/assemble.py` builds the final MP4 with ffmpeg from the storyboard:
   - the renders, the takes and the phone clips;
   - the narration, with the music at −20 dB under the voice;
   - the burned-in captions;
   - loudness normalised to −14 LUFS (`loudnorm`);
   - 1080p30 H.264 with AAC.

   A missing input becomes a labelled slate ("TAKE MISSING: <beat>"), so a draft cut builds today. No Shotcut.
9. **QA:** `video/QA.md` is the checklist, and `scripts/video/qa.py` checks what code can:
   - the duration is under 3:00;
   - 1920×1080 at 30 fps;
   - the loudness is −14 ± 1 LUFS;
   - the SRT is valid and in step with the storyboard;
   - every number has a source;
   - the stub label is present whenever the results run is a stub run.
10. **Privacy:** no keys, emails, notifications or real names on screen. Takes use a clean browser profile. Consent forms never enter the repo. Only the consented clips appear.
11. **Music:** the user picks one YouTube Audio Library or Pixabay track. `assemble.py` takes its path, and `CREDITS.md` gets the row the user gives. A draft without music is fine.

## Done when
- **Green:** `uv run ruff check packages scripts`, `uv run ruff format --check packages scripts` and `uv run pytest scripts -W error::RuntimeWarning`, with the new scripts tested.
- `uv run python scripts/video/storyboard_check.py` passes.
- **The motion renders exist,** and a draft cut, `video/renders/spotcool_draft.mp4`, builds from the placeholders. It passes `qa.py`, except for the items that need the user's material, which `qa.py` lists.
- **`video/README.md`** gives the commands end to end: render the motion, record the takes, record the narration, assemble, run the QA.
- **The runbook is updated.**

## Lead amendments after the review (Nov 3)

- **The hook's sources, checked by the lead.** Kastle's 56.1 % is badge swipes against February 2020 (normal peak occupancy, not capacity), and 66.1 % was one peak day. PNNL-22072's 17.8 % against 5.9 % is a national average of whole-building energy, mostly heating. So the first draft's "half-empty offices", "as if every desk were taken", "on Tuesdays" and "up to 17.8 %" were dropped, and `numbers.md` rows 9, 12 and 13 now say what the sources measure. BEE's 55 % held.
- **The checks got stricter.** Signed numbers and ranges are found. The results card is checked against the never-quote list, its run's source and the results narration. `voice.py` refuses cuts at ambiguous pauses.
- **The takes sync to OBS.** The cue clock starts on `RecordStateChanged` (`OBS_WEBSOCKET_OUTPUT_STARTED`). The dashboard replay plays 07:00 to 19:00 at speed 2700, in 16 s, inside its 21 s beat.
- **Length:** 176 s, under the 178 s limit.
