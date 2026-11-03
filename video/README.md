# The demo video

The video is built from code, from the sources in this folder. The user supplies only what code can't: the filmed clips, the three screen and phone takes, the narration voice, one music track, and the upload. Until those exist, every step below still runs, and the cut has a labelled slate wherever material is missing ("TAKE MISSING: dashboard"), so the draft can be watched end to end.

| File | What it is |
|---|---|
| [`storyboard.yaml`](storyboard.yaml) | The single source of truth: every beat's times, type, source (composition, take or clip), narration, on-screen text and sources |
| [`narration.md`](narration.md) | The voice-over, one section per beat, written to be read aloud |
| [`sources.yaml`](sources.yaml) | Every source a beat cites, with the figures it backs and the file in the repo that records them (`docs/release/numbers.md` for every figure) |
| [`motion/<id>/index.html`](motion/) | The HyperFrames compositions: `title`, `hook_stats`, `three_connections`, `architecture`, `results_card`, `rollout_privacy`, `close` |
| [`obs/scene_collection.json`](obs/scene_collection.json) | The OBS scenes: `Dashboard`, `Dashboard+Camera`, `YABE`, `PhoneCam` |
| [`QA.md`](QA.md) | The checklist before upload |
| `raw/`, `takes/`, `renders/` | The narration and music, the OBS takes, and everything generated. All git-ignored (Law 6) |

The scripts are in [`scripts/video/`](../scripts/video/), and each one's `--help` says what it does.

## The storyboard: 2:56

| Time (s) | Beat | Type | Source | On screen |
|---|---|---|---|---|
| 0-4 | `title` | title | `title` composition | SpotCool, "Cooling that follows people" |
| 4-39 | `hook` | motion | `hook_stats` | US office attendance 56.1 % against February 2020, 66.1 % on the peak day (Kastle); about 55 % of a typical Indian commercial building's energy goes to HVAC (BEE); 17.8 % against 5.9 % of whole-building energy saved by counting occupants against presence sensors, in simulations of a large US office (PNNL-22072) |
| 39-56 | `plugs_in` | motion | `three_connections` | CCTV, the BMS and complaints: no new sensors |
| 56-72 | `world` | motion | `architecture` | The world built to test it: one BOPTEST office run twice |
| 72-92 | `bms_yabe` | screen | take `yabe_override` | The BMS in YABE: an operator at priority 8 beats the agent at 10, which backs off |
| 92-113 | `dashboard` | screen | take `dashboard_replay` | The hybrid Tuesday, a replay of SpotCool's agent's scored run (`ag3-boptest-agent-s60`): 07:00 to 19:00 in 16 s |
| 113-130 | `louvers` | phone | take `phone_vrf` | Live: the VRF's louvers follow a person |
| 130-157 | `results` | motion | `results_card` (generated) | 19.94 %, labelled "SpotCool saved (SAM 3.1 perception, in simulation)", from run `ag3-boptest-agent-s60` |
| 157-170 | `rollout` | motion | `rollout_privacy` | Rollout and privacy |
| 170-176 | `close` | title | `close` | SpotCool, the repo, the sources slide |

## Before you start

Run everything from the repo root in Git Bash, with these set:

```bash
export UV_PROJECT_ENVIRONMENT="C:/Users/hussa/dev/venvs/spotcool-ai"   # your venv, outside OneDrive
export SPOTCOOL_WORLD_RUN_DIR="C:/Users/hussa/dev/SpotCool-AI/runs"    # the saved runs (only read)
export SPOTCOOL_WORLD_FOOTAGE_DIR="C:/Users/hussa/dev/footage"         # the MEVA clips (datasets/meva) and the phone
```

You also need ffmpeg 8 on PATH, Node.js 22 or newer (HyperFrames runs through `npx`, pinned to `hyperframes@0.8.6`), OBS Studio, and Windows PowerShell for the placeholder voice. The first HyperFrames run fetches its pinned CLI with npm, the Inter and JetBrains Mono fonts from Google Fonts, and GSAP from jsDelivr; all are cached after that, and byte-identical rebuilds assume the same cached fonts. It uses the Chrome headless shell already in Puppeteer's cache (`npx --yes hyperframes@0.8.6 doctor` shows which one). Telemetry and update checks are switched off.

## 1. Check the sources

```bash
uv run python scripts/video/storyboard_check.py
```

It prints each beat's narration pace and fails on any of these:

- a gap or an overlap between beats, or a video past 2:58;
- narration that needs more than 150 words a minute;
- a number on screen or in the narration that its beat's sources don't back;
- a figure from the never-quote list;
- a composition whose length or variables don't match its beat;
- a take scene that OBS lacks.

Run it after every edit to `storyboard.yaml`, `narration.md` or a composition.

## 2. Render the motion graphics

```bash
uv run python scripts/video/results_card.py              # the card's text from the saved run (stub label)
uv run python scripts/video/render_motion.py             # all seven into video/renders/motion/ (about 4 min)
uv run python scripts/video/render_motion.py --only results_card   # one again
uv run python scripts/video/render_motion.py --check     # HyperFrames' lint, layout and contrast checks
```

Each composition takes its text from its beat's `on_screen_text` (see `video/renders/motion/<id>.vars.json`). The results card takes its numbers from `results_card.py`, which reads the run through the world's report, so nobody types them. The same inputs render the same bytes.

To preview or edit a composition in the browser:

```bash
cd video/motion/hook_stats && npx --yes hyperframes@0.8.6 preview
```

Studio shows the placeholders, not the storyboard's text; the render always passes the text.

**Done Nov 4:** the results beat names SpotCool's agent's quotable run `ag3-boptest-agent-s60`. For any later run, the steps are:

1. Name it in the results beat's `run_id`, add a `video/sources.yaml` entry with that `run_id`, its figures (in `docs/release/numbers.md`) and its `cite`, and list it in the beat's `sources`.
2. Run `results_card.py` and `render_motion.py --only results_card`. The label becomes "SpotCool saved" only if the report's verdict is `Quotable`.
3. Rewrite the results paragraph of `narration.md`.

`storyboard_check.py` fails while the results beat cites no source for its run. `qa.py` fails until all of these hold:

- the card regenerates from the run the beat names;
- the card's figures equal that source's figures, and none is on the never-quote list (`NEVER_QUOTE` in `scripts/video/storyboard.py`);
- the beat cites that source;
- the results narration says only the card's numbers, its saving among them;
- the narration says "stub" exactly when the card is a stub run's;
- the render used the card, and its label is the verdict's.

## 3. Record the narration

Until it's recorded, the cut uses Windows' built-in voice:

```bash
uv run python scripts/video/tts_placeholder.py           # -> video/renders/narration_placeholder.wav
```

It prints where each paragraph lands, and fails if one runs past its beat.

To record your own:

1. Open `video/narration.md` and read it top to bottom: the hook first, the close last.
2. Read at about 140 words a minute, plainly.
3. Leave a pause of about 2 seconds between paragraphs, and none that long inside one. The assembly cuts the recording at its longest pauses, and starts each paragraph at its beat. It refuses the cut if a break is not clearly longer than every pause inside a paragraph, or if a paragraph's pace comes out outside 100 to 190 words a minute, and names the paragraphs to record again.
4. Use Audacity, or a phone voice recorder in a quiet room.
5. Save it as `video/raw/narration.wav`. From a phone:

   ```bash
   mkdir -p video/raw && ffmpeg -i narration.m4a -ac 1 -ar 48000 video/raw/narration.wav
   ```

6. Check that it fits, and see the captions' timing:

   ```bash
   uv run python scripts/video/captions.py                # -> video/renders/spotcool_draft.srt
   ```

   A paragraph that runs past its beat is named. Read it again a little faster, or lengthen the beat in the storyboard (then re-run step 1).

## 4. Record the takes

**OBS, once:**

1. Scene Collection > Import `video/obs/scene_collection.json`, then select "SpotCool video".
2. Tools > WebSocket Server Settings > Enable WebSocket server. Copy the server password into `export OBS_WS_PASSWORD=...` (never commit it).
3. Settings > Output > Recording: format `mkv` (it survives a crash).
4. The Dashboard browser source has its own clean browser profile: no logins, no bookmarks.
5. Close mail and chat apps and turn on Do Not Disturb, so no notification lands in a take.

`take.py` sets the canvas to 1920x1080 at 30 fps. It also points the Dashboard source at the beat's URL, and the Phone camera source at `$SPOTCOOL_WORLD_LIVE_CAMERA_URL`'s `/video` stream.

**Each take** prints what must be ready, waits for Enter, then:

1. records the beat's pre-roll and the beat;
2. switches scenes and prints cues at the storyboard's times;
3. saves the recording as `video/takes/<take>.mkv` (the take it replaces becomes `<take>.previous.mkv`).

Rehearse first with `--dry-run`, which needs no OBS. In this order:

```bash
# The camera tiles show the MEVA clips in $SPOTCOOL_WORLD_FOOTAGE_DIR/datasets/meva (docs/agent/HANDOFF.md, D1).
# Build the dashboard once:
cd apps/dashboard && npm ci && npm run build && cd ../..

# 1. The BMS in YABE: docs/runbooks/yabe.md, The operator override for the video. The agent, then the world
#    on loopback; hold ZN-COR-CLG-SP at 23 at priority 8 between 08:30 and 17:45; wait for the back-off at 18:15:
uv run python scripts/video/take.py yabe_override --dry-run
uv run python scripts/video/take.py yabe_override

# 2. The hybrid Tuesday: the script starts the replay of the agent's scored run itself.
uv run python scripts/video/take.py dashboard_replay

# 3. The louvers, live with SpotCool's agent (docs/runbooks/video-takes.md, The VRF segment):
export SPOTCOOL_WORLD_LIVE_CAMERA_URL=http://<phone address>/shot.jpg
uv run python scripts/video/take.py phone_vrf --agent http://127.0.0.1:8002
```

Watch each take in VLC before the next: no keys, emails, notifications or real names on screen. A take is re-shot by running its command again.

## 5. Choose the music (optional)

Pick one track from the YouTube Audio Library or Pixabay Music. Keep its page URL and licence. Save it as `video/raw/music/<file>`, and add its row to [`CREDITS.md`](../CREDITS.md) (Media). The assembly loops it under the voice, 20 LU below the narration, fading in over 1 s and out over 3 s. A cut without music is fine.

## 6. Assemble

```bash
uv run python scripts/video/assemble.py                                   # the draft: video/renders/spotcool_draft.mp4
uv run python scripts/video/assemble.py --music video/raw/music/<file>    # with the music
uv run python scripts/video/assemble.py --final --music video/raw/music/<file> --output video/renders/spotcool.mp4
```

What it does:

- Each beat shows its source for exactly its length: its render, its take (from `in_s`), or a slate.
- Each take gets its beat's label, top left.
- The narration (recorded, else the placeholder) is limited and normalised to -14 LUFS, with the captions burned in.
- The output is 1080p30 H.264 with AAC, bit-exact for the same inputs, with an `.srt` and a `.build.json` beside it.
- `--final` refuses a slate or the placeholder voice.

## 7. QA and upload

```bash
uv run python scripts/video/qa.py                                  # the draft: passes, listing what is pending
uv run python scripts/video/qa.py video/renders/spotcool.mp4 --final   # the final: fails on anything pending
```

`qa.py` checks:

- the length, frame size, frame rate and codecs;
- the loudness (-14 +/- 1 LUFS);
- the SRT, against the storyboard and the narration;
- every number's source;
- the results card, regenerated from the run and checked against the render, with the stub label on a stub run.

It then lists PENDING (your material) and MANUAL items, and [`QA.md`](QA.md) covers the rest.

Upload `video/renders/spotcool.mp4` to the hackathon form (and YouTube, if the form asks for a link) before Nov 4, 21:00 IST. The captions are burned in. Attach `spotcool.srt` only where the form asks for a captions file. On YouTube it would show the captions twice when CC is on.
