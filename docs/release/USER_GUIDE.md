# Your guide to the submission (Nov 4, by 21:00 IST)

**For:** you, the person doing the last, hands-on steps. **Written:** Nov 4, about 03:00 IST, after every engineering work package was merged into `agent/main`. **Updated:** Nov 5, for the re-cut around the live take (AG10).

Everything the code can do is done. What's left needs you:
- your voice;
- one screen recording, the live take (Nov 5);
- the upload and the form.

This guide walks through each step from scratch: what to run, what you should see, and what to do when something goes wrong. Do the steps in the order given. The order keeps the most options open if something fails.

---

## What exists already (read once)

- **The result.** SpotCool's own agent perceived each room through Meta's SAM 3.1 on public CCTV clips and drove a simulated building's BMS. It saved **19.94 % HVAC energy** against the baseline on BOPTEST, and its report says "Quotable".
  - It was right about which zones had people at every step it perceived.
  - Comfort where people were cost 2.13 against 0.33 K·h. We show that honestly.
  - The video shows the live run `runs/live-quad-boptest-s60` (Nov 5, all four Meta models on); the scored run `runs/ag3-boptest-agent-s60` has the same figures. Every figure is in `docs/release/numbers.md`.
- **The video** (2:56) is built by code from `video/storyboard.yaml`. A draft cut already builds and passes the automatic QA. Only two things are missing: your narration, and the live take `video/takes/live_quad.mkv` (the four-pane `Quad` scene, then about 30 s of the `Dashboard` scene for the louvers).
- **The camera footage** is public MEVA dataset clips (CC BY 4.0), already in `C:\Users\hussa\dev\footage\datasets\meva`. The meeting room's camera plays a recorded walking clip. Nothing needs filming.

**Three rules hold throughout:**
1. **Keep the laptop on one Wi-Fi network for the whole day.** The agent and the world find each other by the laptop's address. If Wi-Fi switches networks, restart both.
2. **Never show or paste the API key.** It lives only in `C:\Users\hussa\dev\secrets\spotcool-agent.env`.
3. **Stop programs with Ctrl+C** in their own window, never by closing the window. That's how they release what they hold.

---

## Step 0. One-time setup (about 30 minutes)

### 0.1 The terminal and the code
Use **Git Bash** for every command in this guide, unless it says PowerShell.

```bash
cd /c/Users/hussa/dev/SpotCool-AI
git switch agent/main
git pull
export UV_PROJECT_ENVIRONMENT="C:/Users/hussa/dev/venvs/spotcool-ai"
export SPOTCOOL_WORLD_RUN_DIR="C:/Users/hussa/dev/SpotCool-AI/runs"
export SPOTCOOL_WORLD_FOOTAGE_DIR="C:/Users/hussa/dev/footage"
uv sync --all-packages --all-extras
uv run spotcool-world version
uv run spotcool-agent version
```

- **Expect:** two version lines, for example `spotcool-agent 0.1.0 (contracts 0.1.1)`.
- **The `export` lines must be set in every new Git Bash window.** Paste all three each time, or put them in `~/.bashrc`.

### 0.2 The dashboard (once)
```bash
cd apps/dashboard && npm ci && npm run build && cd ../..
```
**Expect:** it ends without errors and creates `apps/dashboard/dist`.

### 0.3 OBS Studio (installed already)
1. Open OBS. Go to Scene Collection > Import, choose `C:\Users\hussa\dev\SpotCool-AI\video\obs\scene_collection.json`, then select the "SpotCool video" collection. It has five scenes: Dashboard, Dashboard+Camera, YABE, PhoneCam and Quad.
2. Go to Tools > WebSocket Server Settings and tick **Enable WebSocket server**. Click "Show Connect Info" and copy the password.
3. In Git Bash, `export OBS_WS_PASSWORD='<the password>'`. Never commit or paste it anywhere else.
4. Go to Settings > Output > Recording and set the format to **mkv**, so a recording survives a crash.
5. Turn on Windows **Do Not Disturb**, and close mail, chat and anything with notifications.

### 0.4 YABE and the phone: not needed

The Nov 5 re-cut has no YABE take and no live phone take. `docs/runbooks/yabe.md` and the phone's steps in `docs/runbooks/video-takes.md` stay for reference.

### 0.5 Network
In PowerShell, stop the laptop from jumping to other networks:
```powershell
netsh wlan show profiles
netsh wlan set profileparameter name="<each other profile>" connectionmode=manual
```

---

## Step A. The live take (recorded Nov 5)

**What it shows:** SpotCool's agent running live on BOPTEST at speed 60, with all four Meta models on, in four panes at once (OBS's `Quad` scene): the dashboard top left, the camera clips top right, the boxes SAM 3.1 found bottom left, and the agent's terminal bottom right, with one line for every Meta call. Then about 30 s of the `Dashboard` scene around 14:00, where the meeting room's louvers follow a person in the recorded clip.

How it's recorded: `docs/runbooks/video-takes.md`, "The live quad take". You record it by hand in OBS; `take.py` doesn't record it.

---

## Step B. Record the narration (about 40 minutes)

1. Open `C:\Users\hussa\dev\SpotCool-AI\video\narration.md`. Read it top to bottom once, aloud. There's one section per beat, in this order: `hook`, `plugs_in`, `world`, `live` (the long one, about 50 s), `louvers`, `meta_models`, `results`, `rollout`, `close`. The title beat has none.
2. **Record in Audacity** (installed), in a quiet room, with the microphone about a hand's width away:
   - read at about **140 words a minute**, plainly, like explaining it to a colleague;
   - say every number as written: "nineteen point nine four percent", "zero point three three", "SAM three point one";
   - **pause about 2 seconds between sections,** and never that long inside one. The cut uses those pauses to place each section at its beat;
   - a mistake? Pause 2 s and read that whole section again. Then delete the bad take in Audacity before exporting.
3. **Export:** File > Export Audio > WAV (16-bit PCM). Then convert and place it:
   ```bash
   mkdir -p video/raw
   ffmpeg -i /c/path/to/your-export.wav -ac 1 -ar 48000 video/raw/narration.wav
   ```
   From a phone recording instead: `ffmpeg -i narration.m4a -ac 1 -ar 48000 video/raw/narration.wav`.
4. **Check that it fits:**
   ```bash
   uv run python scripts/video/captions.py
   ```
   - **Expect:** no errors, and `video/renders/spotcool_draft.srt` written.
   - **If a section runs past its beat,** it names it: read that section again a little faster and re-record.
   - **If it complains about the pauses,** your breaks weren't clearly longer than the pauses inside sections: re-record with clearer 2 s breaks.

---

## Step C. Put the live take in place (about 5 minutes)

1. OBS saves the recording under a date name, for example `video/takes/2026-11-05_00-58-54.mkv`. **Rename it to `video/takes/live_quad.mkv`**:
   ```bash
   mv "video/takes/<OBS's file name>.mkv" video/takes/live_quad.mkv
   ```
2. **Watch it once** (double-click; Windows' player is fine). Note two times, in seconds from the start of the file:
   - where the `Quad` part you want starts (the 08:30 arrivals are the best minute);
   - where the `Dashboard` part around 14:00 starts.

---

## Step D. Tell Claude where each beat starts (about 5 minutes)

Tell Claude the two times from Step C. Claude sets them as `in_s` in `video/storyboard.yaml`: the `live` beat (60 s) starts at the first, the `louvers` beat (12 s) at the second. Both still say `0` until then, with a comment saying so.

One file feeds both beats, and each beat shows its own part of it.

---

## Step E. Music (optional, about 15 minutes)

1. Pick one track from the YouTube Audio Library or Pixabay Music, and keep its page URL and licence text.
2. Save it as `video/raw/music/<file>`.
3. Add one row to `CREDITS.md` (Media: file, what, URL, licence, attribution, "user"). Claude can do this if you paste the page URL.

---

## Step F. The final cut and QA (about 20 minutes)

```bash
uv run python scripts/video/results_card.py
uv run python scripts/video/render_motion.py
uv run python scripts/video/assemble.py --final --output video/renders/spotcool.mp4
uv run python scripts/video/qa.py video/renders/spotcool.mp4 --final
```

What each line does:
- `results_card.py` writes the results card's numbers from the live run, `live-quad-boptest-s60` (it needs `SPOTCOOL_WORLD_RUN_DIR`, step 0.1). Nobody types them.
- `render_motion.py` draws the eight motion graphics. It takes about 5 minutes, and only needs re-running if something on screen changed.
- `assemble.py --final` builds the video. `--final` means "the real one": it stops if the narration is still the computer voice or a take is missing, instead of putting a "MISSING" slate in.
- `qa.py --final` checks the finished file: length, size, loudness, captions, every number's source, and the results card. `--final` also fails on anything still pending.

- With music, add `--music video/raw/music/<file>` to the `assemble.py` line.
- **Expect:** `qa: spotcool.mp4 passes`. A `FAIL` line says what's wrong. If you can't fix it, paste the output to Claude.

---

## Step G. Watch it once, carefully (about 15 minutes)

Use `video/QA.md` ("Watch it"). The essentials:
1. **Privacy:** no key, email, notification, browser tab, real name, or private screen in any frame.
2. **Honesty:** the card says "SpotCool saved (SAM 3.1 perception, in simulation)", and the narration says the occupant-heat caveat once.
3. **The live take:**
   - all four panes show, and the terminal's `META` lines can be read;
   - the boxes appear and the zones flip at the arrivals;
   - in the louvers beat, the louvers slew, they don't jump.
4. **Captions:** proofread them against your voice.
5. **The close:** the repo address `github.com/IAMMOJAHID/SpotCool-AI` is right, and the sources slide can be read when paused.

---

## Step H. Upload and submit (about 30 minutes; **before 21:00 IST**)

1. **YouTube:** upload `video/renders/spotcool.mp4` as **Unlisted**.
   - **Title:** "SpotCool: cooling that follows people (Meta AI hackathon)".
   - **Description:** paste the result line and the credits below.
   - Attach `video/renders/spotcool.srt` as captions if you like.
2. **Description text** (safe to paste; every figure is from `docs/release/numbers.md`):
   > SpotCool's agent watches each zone of an office through Meta's SAM 3.1 and drives the building's BACnet BMS: empty zones get a raised setpoint and their airflow shut off, booked rooms are pre-cooled, and an operator's override always wins. In the meeting room, the AC's louvers follow a person on camera. Muse Spark (muse-spark-1.3) reads what people wear and do, for the room's comfort setpoint; Muse Voice (muse-voice-transcribe-1.0) turns a spoken complaint into text; Muse Wonder (muse-wonder-1.0) works out what it means, answers it, and narrates each step's decisions. The models perceive and code decides: no language model writes to the BMS. In simulation (BOPTEST's multizone office, a hybrid Tuesday), it used 19.94 % less HVAC energy than the baseline, and was right about which zones had people at every step it perceived; discomfort where people were rose from 0.33 to 2.13 K·h. BOPTEST's occupant heat follows its own schedule, not the cameras. Camera footage: MEVA dataset (Kitware Inc. and IARPA), CC BY 4.0. Building model: BOPTEST (IBPSA), BSD-3-Clause.
3. **Watch the uploaded copy once,** at full quality.
4. **Put the link in the README.** Tell Claude "the video link is", followed by the URL. Claude replaces the README's `Video: <YouTube link>` line on a `docs/readme-video-link` branch and merges it into `agent/main` and `dev` (and `main`, below). Then open `https://github.com/IAMMOJAHID/SpotCool-AI` and check that the front page shows the new README with the link.
5. **The judges see `main`.** It's the repo's default branch, and until it's updated it holds only the "Initial commit". The submission-ready repo is `dev` (merged Nov 4, AG6). Before you submit, tell Claude "update main from dev". `main` is an ancestor of `dev`, so it's a fast-forward with nothing to resolve. Or set the default branch to `dev` in GitHub's settings.
6. **The form.** Confirm its rules on the form itself. Our notes say: a video of 3 minutes or less (ours is 2:56), a public repo, contributors listed, built during the event, and third-party licences listed (`CREDITS.md`). Give:
   - the YouTube link;
   - the repo `https://github.com/IAMMOJAHID/SpotCool-AI` (its default branch, once step 5 is done);
   - the team.

---

## Step I. After submitting

1. **Rotate the keys** (urgent, because the repo history holds the old ones): the Meta API key and the Baseten key, in their providers' consoles. Then put the new Meta key in `C:\Users\hussa\dev\secrets\spotcool-agent.env` (`META_API_KEY=...`).
2. **The BOPTEST Codespace** is already stopped. If you ever start it, stop it after: `gh codespace stop -c spotcool-boptest-qxxjjj94x672rp`.
3. **The repo** needs nothing more. `dev` holds everything, with the judges' README, and so does `main` once Step H5 is done.

---

## If something goes wrong

| What you see | What to do |
|---|---|
| The agent keeps printing `SIM-CLOCK unanswered` | The world isn't running, or its address differs. Start the world, and check both are on the same Wi-Fi. |
| A scene is missing in OBS | Import `video/obs/scene_collection.json` again (step 0.3). |
| `captions.py` says a section runs long | Re-record that section a little faster. |
| `qa.py --final` lists PENDING items | The narration or `video/takes/live_quad.mkv` is missing: do Step B or Step C. |
| The live or louvers beat shows the wrong moment | Its `in_s` is off: tell Claude the right time (Step D). |
| Anything else | Copy the exact output and ask Claude. Each step's runbook is in `docs/runbooks/` and `video/README.md`. |

**Time plan,** if you start at 09:00:

| Time | Step |
|---|---|
| 09:00 | setup |
| 09:30 | B (narration) |
| 10:15 | C and D (the live take in place) |
| 10:30 | E (music) |
| 10:45 | F (final cut) |
| 11:15 | G (watch) |
| 11:45 | H (upload and form) |
