# WP9: Video production

**Tool:** Antigravity (HyperFrames, browser checks) with people for narration and phone shots. **Branch:** `wp/wp9-video`. **Depends on:** WP7 (dashboard), WP6/WP11 (a recorded golden run), WP8 (footage).
**Directories:** `video/` (sources in git; `video/raw/` and `video/renders/` are git-ignored), `scripts/video/`.
**Installed:** OBS Studio (with obs-websocket 5 on port 4455), ffmpeg 8, Node 24.

## Deliverable
One video **under 3:00**: 1080p30 H.264, captions burned in plus a separate `.srt`, uploaded **unlisted to YouTube**. YouTube is also where the YouTube Audio Library music licence applies. It covers the plan's §7 storyboard.

## Pieces
1. **`video/storyboard.yaml`:** the single source of truth for timing. It lists beats with `start`, `end`, `type` (`motion` | `screen` | `phone` | `title`), `scene` (an OBS scene, HyperFrames composition or footage file), `narration_id` and `on_screen_text`.
   - Target 2:50.
   - Every number on screen cites a source id from `video/sources.yaml`.
2. **`video/narration.md`:**
   - final narration at about 140 words per minute (380–400 words for 2:50), with one paragraph per beat id;
   - plain, calm delivery (the whiteboard note says "natural, calm");
   - record with Audacity, or Kokoro-82M TTS (Apache-2.0) as the fallback.
3. **`video/motion/`:** a HyperFrames project (HTML, CSS and GSAP rendered to MP4; use the installed `hyperframes` skills). Compositions:
   - `hook_stats`: 56% attendance, 55% of energy is HVAC, 17.8% vs 5.9%;
   - `three_connections`: cameras, BMS, complaints;
   - `architecture`: world vs. agent, BACnet and HTTP lines;
   - `rollout_privacy`: edge box, shadow mode, supervised control, counts only;
   - `results_card`: filled from the run's `manifest.json` KPIs by a small script, never typed by hand.

   Each composition renders to `video/renders/` deterministically.
4. **`video/obs/`:**
   - **`scene_collection.json`** (import into OBS) with scenes: `Dashboard` (browser source at 1920×1080 of `http://127.0.0.1:8090/?mode=video`), `Dashboard+Camera`, `YABE` (window capture) and `PhoneCam`.
   - **`scripts/video/take.py`:** an obs-websocket v5 client. Use a small MIT-licensed client, or write the protocol directly with `websockets`; check the licence first. It:
     1. connects (password from env `OBS_WS_PASSWORD`);
     2. switches to the first beat's scene and starts recording;
     3. starts `spotcool-world replay <run_id> --speed <x>`;
     4. switches scenes at each beat's `start` from `storyboard.yaml`;
     5. stops recording.

     This makes every take identical and re-recordable.
5. **Captions:** `scripts/video/captions.py` builds the SRT straight from `narration.md` and the storyboard timings. Optionally, faster-whisper (MIT) can check alignment against the recorded narration.
6. **Music:** one Pixabay track or one YouTube Audio Library track at −20 dB under the narration, logged in `CREDITS.md`.
7. **Edit:** a Shotcut project (`video/edit/spotcool.mlt`), or Kdenlive, that assembles the renders, takes, phone clips, narration, music and captions. Export settings go in `video/README.md`.
8. **QA checklist** (`video/QA.md`):
   - length under 3:00;
   - every on-screen number has a source on the sources slide;
   - the limitation is stated (BOPTEST occupant heat follows its schedule);
   - `CREDITS.md` complete;
   - captions proofread;
   - narration about −14 LUFS;
   - nothing private on screen (no keys, emails or notifications; use a clean browser profile).

## Done when
- The final MP4 and SRT are in `video/renders/`.
- The unlisted YouTube link is in the README.
- `QA.md` is fully ticked.
- A fresh take can be produced from `take.py` plus the edit project in under 30 minutes.
