# Your guide to the submission (Nov 4, by 21:00 IST)

**For:** you, the person doing the last, hands-on steps. **Written:** Nov 4, about 03:00 IST, after every engineering work package was merged into `agent/main`.

Everything the code can do is done. What's left needs you:
- your voice;
- your screen recordings (three "takes");
- you, standing in front of the phone camera;
- the upload and the form.

This guide walks through each step from scratch: what to run, what you should see, and what to do when something goes wrong. Do the steps in the order given. The order keeps the most options open if something fails.

---

## What exists already (read once)

- **The result.** SpotCool's own agent perceived each room through Meta's SAM 3.1 on public CCTV clips and drove a simulated building's BMS. It saved **19.94 % HVAC energy** against the baseline on BOPTEST, and its report says "Quotable".
  - It was right about who was in each zone on 240 of 240 zone-steps.
  - Comfort where people were cost 2.13 against 0.33 K·h. We show that honestly.
  - The run is `runs/ag3-boptest-agent-s60`, and every figure is in `docs/release/numbers.md`.
- **The video** is built by code from `video/storyboard.yaml`. A draft cut already builds and passes the automatic QA. Only four things are missing: your narration, and the three takes (YABE, the dashboard replay, the live louvers).
- **The camera footage** is public MEVA dataset clips (CC BY 4.0), already in `C:\Users\hussa\dev\footage\datasets\meva`. Nothing needs filming except the live phone take.

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

### 0.4 YABE (for take C)
1. Download "Yet Another BACnet Explorer" from <https://sourceforge.net/projects/yetanotherbacnetexplorer/> and install it.
2. You only open it during take C. `docs/runbooks/yabe.md` has the details.

### 0.5 The phone (for take A)
1. On the Android phone, install **IP Webcam** (by Pavel Khlebovich) from Google Play.
2. In its settings, set Video preferences > Video resolution to **1280x720**.
3. Put the phone on the **same Wi-Fi** as the laptop. Plug it into a charger.

### 0.6 Network
In PowerShell, stop the laptop from jumping to other networks:
```powershell
netsh wlan show profiles
netsh wlan set profileparameter name="<each other profile>" connectionmode=manual
```

---

## Step A. The live louvers take, first (about 45 minutes; **finish by 16:00 IST**)

This is the only take that needs a person. It goes first, because if it can't be made, the video's script changes before you record the narration (see "If take A fails").

**What it shows:** you walk across the phone camera's view. The agent sees you through SAM 3.1 and aims the meeting room's simulated air-conditioning louvers at you, so the dashboard's louver angles follow you.

1. **Phone.** Mount it at about head height (around 2.8 m is the default the agent assumes), tilted down a little. It should face the area you'll walk across. In IP Webcam, tap **Start server** and read the address on the phone's screen, for example `http://192.168.0.105:8080`.
2. **Check it** in a laptop browser: `http://<phone address>/shot.jpg` must show one photo.
3. **Window 1: the agent first.** In a new Git Bash, run the three exports from 0.1, then:
   ```bash
   uv run spotcool-agent run --vrf --vision meta --stt meta --llm meta --env-file C:/Users/hussa/dev/secrets/spotcool-agent.env
   ```
   Leave it running. Warnings like "no-response" are normal until the world starts.
4. **Window 2: the world.** In a new Git Bash, run the three exports, then:
   ```bash
   export SPOTCOOL_WORLD_LIVE_CAMERA_URL="http://<phone address>/shot.jpg"
   export SPOTCOOL_WORLD_VRF_PROFILE=mitsubishi
   uv run spotcool-world run --plant rc --speed 1
   ```
   It prints its BMS and VRF addresses. **Wait 6 minutes:** the simulated room settles from 30 degC.
5. **Rehearse.**
   - Open `http://127.0.0.1:8090/?mode=video&agent=http://127.0.0.1:8002` in a browser.
   - Walk slowly from the left edge of the phone's view to the right.
   - In window 1, the agent logs `decision` lines for `IDU-VANE-H`, and the dashboard's VRF panel shows the louver angle moving.
   - **If left and right are swapped,** stop the agent (Ctrl+C), add `--vrf-mirror` to its command, and start it again.
6. **Record.** Window 3, with the exports, `OBS_WS_PASSWORD` and `SPOTCOOL_WORLD_LIVE_CAMERA_URL`:
   ```bash
   uv run python scripts/video/take.py phone_vrf --dry-run          # prints the schedule; no OBS needed
   uv run python scripts/video/take.py phone_vrf --agent http://127.0.0.1:8002
   ```
   Press Enter when it asks, and follow the cues it prints:
   - 0 s: enter at the left edge and walk right;
   - 7 s: sit on the far chair for 5 s, then walk back toward the camera.

   The file lands in `video/takes/phone_vrf.mkv`.
7. **Watch it** (double-click the file; Windows' player is fine). Check that the louvers move smoothly and nothing private is on screen. Re-run step 6 to re-shoot.
8. **Stop:** Ctrl+C in window 1 (the agent) first, then in window 2 (the world).

**If take A fails** (no movement, the phone unreachable, or 16:00 has passed): **tell Claude "drop the louvers beat"** before you record the narration. The storyboard and the narration lose that beat (about 15 minutes of work for Claude), and the video stays complete.

---

## Step B. Record the narration (about 40 minutes)

1. Open `C:\Users\hussa\dev\SpotCool-AI\video\narration.md`. Read it top to bottom once, aloud. There's one section per beat; the title beat has none.
2. **Record in Audacity** (installed), in a quiet room, with the microphone about a hand's width away:
   - read at about **140 words a minute**, plainly, like explaining it to a colleague;
   - say every number as written: "nineteen point nine four percent", "zero point three three";
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

## Step C. The YABE take: an operator overrides the agent (about 30 minutes)

**What it shows:** a building operator holds the core zone's setpoint at a higher BACnet priority. SpotCool's agent sees the hold, backs off, and says so on the dashboard. This was rehearsed on Nov 4 with a scripted operator.

1. **Window 1: the agent, on loopback:**
   ```bash
   uv run spotcool-agent run --world-host 127.0.0.1 --bms-port 47809 --vision meta --env-file C:/Users/hussa/dev/secrets/spotcool-agent.env
   ```
2. **Window 2: the world, on loopback:**
   ```bash
   SPOTCOOL_WORLD_BMS_ADDRESS=127.0.0.1 SPOTCOOL_WORLD_BMS_PORT=47809 SPOTCOOL_WORLD_HTTP_HOST=127.0.0.1 uv run spotcool-world run --plant rc --speed 60
   ```
   Open `http://127.0.0.1:8090/?mode=video` in a browser.
3. **Connect YABE** (`docs/runbooks/yabe.md`, "Connect", steps 2–3):
   - Functions > Add BACnet Channel: "BACnet/IP V4 & V6 over Udp", port 47808, local endpoint `127.0.0.1`, then Start.
   - Functions > IP Services > Add Remote Bacnet/IP node: `1001 - 127.0.0.1:47809`.
4. **Hold Core** any time between 08:30 and 17:45 on the dashboard clock. At speed 60 a 15-minute step takes 15 s, so 08:30 comes about 90 s after the world starts.
   - Press **Ctrl+Alt+8** in YABE. It beeps, and its log says "WritePriority change to level 8".
   - Open `ANALOG_VALUE:201` (`ZN-COR-CLG-SP`) and write Present Value **23**.
   - Its Priority Array shows 23 at 8.
5. **Wait for 18:15** on the dashboard clock, about 10 minutes later. The agent panel shows *"Core: ZN-COR-CLG-SP is held at 23.0 by an operator at priority 8 -> backed off from writing 28.0"*.
6. **Record** (window 3, with the exports and `OBS_WS_PASSWORD`):
   ```bash
   uv run python scripts/video/take.py yabe_override --dry-run
   uv run python scripts/video/take.py yabe_override
   ```
   Follow its cues:
   - show the Priority Array in YABE;
   - OBS switches to the dashboard by itself;
   - at the last cue, in YABE, still at priority 8, clear Present Value and press Enter to release.

   The agent writes 28 at the next step.
7. **Stop:** Ctrl+C in window 1, then window 2.

---

## Step D. The dashboard replay take (about 10 minutes, hands off)

```bash
uv run python scripts/video/take.py dashboard_replay --dry-run
uv run python scripts/video/take.py dashboard_replay
```

It starts a replay of the agent's scored run by itself, loads the dashboard and records: 07:00 to 19:00 in 16 seconds. Don't touch anything while it runs. Afterwards, watch `video/takes/dashboard_replay.mkv`.

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

- `render_motion.py` takes about 5 minutes, and only needs re-running if something on screen changed.
- With music, add `--music video/raw/music/<file>` to the `assemble.py` line.
- **Expect:** `qa: spotcool.mp4 passes`. A `FAIL` line says what's wrong. If you can't fix it, paste the output to Claude.

---

## Step G. Watch it once, carefully (about 15 minutes)

Use `video/QA.md` ("Watch it"). The essentials:
1. **Privacy:** no key, email, notification, browser tab, real name, or private screen in any frame.
2. **Honesty:** the card says "SpotCool saved (SAM 3.1 perception, in simulation)", and the narration says the occupant-heat caveat once.
3. **The takes:**
   - YABE shows 23 at priority 8 and the "backed off" message;
   - the replay runs the whole day inside its beat;
   - the louvers slew, they don't jump.
4. **Captions:** proofread them against your voice.
5. **The close:** the repo address `github.com/IAMMOJAHID/SpotCool-AI` is right, and the sources slide can be read when paused.

---

## Step H. Upload and submit (about 30 minutes; **before 21:00 IST**)

1. **YouTube:** upload `video/renders/spotcool.mp4` as **Unlisted**.
   - **Title:** "SpotCool: cooling that follows people (Meta AI hackathon)".
   - **Description:** paste the result line and the credits below.
   - Attach `video/renders/spotcool.srt` as captions if you like.
2. **Description text** (safe to paste; every figure is from `docs/release/numbers.md`):
   > SpotCool's agent watches each zone of an office through Meta's SAM 3.1 and drives the building's BACnet BMS: empty zones get a raised setpoint and their airflow shut off, booked rooms are pre-cooled, and an operator's override always wins. In simulation (BOPTEST's multizone office, a hybrid Tuesday), it used 19.94 % less HVAC energy than the baseline, with occupancy right on every 15-minute step; discomfort where people were rose from 0.33 to 2.13 K·h. Camera footage: MEVA dataset (Kitware Inc. and IARPA), CC BY 4.0. Building model: BOPTEST (IBPSA), BSD-3-Clause.
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
| The agent keeps printing `SIM-CLOCK unanswered` | The world isn't running, or its address differs. Start the world, and check both are on the same Wi-Fi. For YABE (step C), both need the loopback options shown there. |
| `take.py` says it can't connect to OBS | OBS isn't open, its WebSocket server is off, or `OBS_WS_PASSWORD` isn't set in that window. |
| `take.py` says a scene is missing | Import `video/obs/scene_collection.json` again (step 0.3). |
| The phone's `shot.jpg` doesn't open | Phone and laptop are on different networks, or IP Webcam isn't started. The address can change after Wi-Fi reconnects. |
| The louvers move the wrong way | Restart the agent with `--vrf-mirror`. |
| The louvers don't move at all | The world must have `SPOTCOOL_WORLD_LIVE_CAMERA_URL` set, and the agent must have `--vrf`. Check the agent's log for SAM errors (rate limits). Try walking slower, closer to the phone. |
| `captions.py` says a section runs long | Re-record that section a little faster. |
| `qa.py --final` lists PENDING items | A take or the narration is missing: do that step. |
| Anything else | Copy the exact output and ask Claude. Each step's runbook is in `docs/runbooks/` and `video/README.md`. |

**Time plan,** if you start at 09:00:

| Time | Step |
|---|---|
| 09:00 | setup |
| 09:30 | A (louvers) |
| 10:30 | B (narration) |
| 11:15 | C (YABE) |
| 11:45 | D (replay) |
| 12:00 | E (music) |
| 12:15 | F (final cut) |
| 12:45 | G (watch) |
| 13:15 | H (upload and form) |

That leaves 7+ hours of buffer before 21:00.
