# WP8: Footage pipeline (own recordings and public datasets)

**Tool:** Claude Code for scripts, people for filming. **Branch:** `wp/wp8-footage`. **Depends on:** nothing. Start now.
**Directories:** `scripts/footage/`, `docs/footage/`, `CREDITS.md` (root; WP10 consolidates it later).
**Footage directory:** `C:/Users/hussa/dev/footage/`, **outside the repo and outside OneDrive**:
- `raw/`: as filmed;
- `clips/`: normalized;
- `audio/`;
- `datasets/`.

Tools already installed: **ffmpeg 8.0.1** and **AWS CLI 2**.

## People: film (Nov 2, morning)
- **`docs/footage/shot_list.md`:** a printable version of plan Appendix A, with one row per clip (camera position, framing, actors, actions, duration, file name).
- **`docs/footage/consent.md`:** a one-paragraph consent text each person in frame agrees to. It covers:
  - use in a public hackathon video and repo-linked demo;
  - frames processed in memory only;
  - no real names on screen.
- **Rules for every clip:**
  - phone fixed at 2 m or more in a corner, tilted down;
  - 720p 30 fps, landscape, 30–40 s;
  - no strangers, screens or logos;
  - one clip per scene file name, e.g. `south_meeting.mp4`.

## Scripts (`scripts/footage/`, stdlib plus ffmpeg on PATH)
1. **`normalize.py raw/ clips/`:**
   - H.264 1280×720 at 30 fps, `-an` (no audio), loudness irrelevant;
   - trim to the marked in/out points from `docs/footage/cuts.yaml`, or the whole clip;
   - faststart;
   - checks the file name pattern `<zone>_<scene>.mp4` against the scenario library's scenes.
2. **`extract_audio.py`:** pulls complaint lines from the raw clips (or separate phone recordings) into `audio/<name>.wav` as 16 kHz mono s16le, matching `spotcool_contracts.occupants.VOICE_FORMAT`. Audio stays outside the repo.
3. **`wisenet_index.py <wisenet_root>`:**
   - Maps WiseNET's 6 cameras and spaces to our 5 zones using `docs/footage/wisenet_mapping.yaml`.
   - Reads its people-tracking annotations to find segments with 0, 1–2 and 4 or more people.
   - Writes `datasets/wisenet/index.yaml`, the format WP4's dataset adapter reads: `(camera_id, scene) → file, start_s, end_s, people`.
4. **`wisenet_codespace.md` plus `wisenet_codespace.sh`:** WiseNET is a single 10.7 GB zip and **local disk is nearly full**. In a GitHub Codespace (free tier):
   1. download the zip;
   2. extract only the selected sets;
   3. run `normalize.py`;
   4. copy the small clips down with `gh codespace cp`.
5. **`meva_fetch.py`:** downloads named indoor MEVA clips from the public bucket `mevadata-public-01` (`aws s3 cp --no-sign-request`, or plain HTTPS GET), then normalizes them. MEVA is CC BY 4.0, so every clip used gets a `CREDITS.md` row.

## License ledger: `CREDITS.md`
One row per asset with: file, what it is, source URL, licence, required attribution text, and who added it.
- **Own footage:** "SpotCool team, consent on file".
- **WiseNET:** CC0. Confirm on the 4TU page when downloading.
- **MEVA:** CC BY 4.0, plus the Kitware/IARPA credit line.
- **Pexels b-roll:** Pexels licence.
- **Not allowed in the public video:** EPFL lab (research use only), CITIC-MTMC (non-commercial), PIROPO (research use only).

## Done when
- The footage directory has a normalized clip for every `(zone, scene)` that `hybrid_tuesday.yaml` uses, plus `audio/south_complaint.wav`.
- WP4's `ClipLibrary` loads all of them.
- Every asset has a `CREDITS.md` row.
- If WiseNET is used, its index is generated and the dataset adapter serves it.
