# Your guide to finishing the video and submitting

**Do the steps in order. Do only one step at a time.** Each step says who does it: **You** or **Claude**. When a step says "tell Claude", type that message in the Claude Code panel.

**What you make:** two recordings. One screen recording in OBS, and your voice reading a script. Claude builds the video from them, so you don't edit anything by hand.

**Total time for you:** about 1 hour 15 minutes.

| # | Step | Who | Time |
|---|---|---|---|
| 1 | Get the laptop ready | You | 5 min |
| 2 | Set up OBS | You | 10 min |
| 3 | Set up the terminal | You | 3 min |
| 4 | Check everything is running | Claude | 2 min |
| 5 | Record the live screen | You and Claude | 20 min |
| 6 | Record your voice | You | 20 min |
| 7 | Build the final video | Claude | 15 min |
| 8 | Watch it once | You | 5 min |
| 9 | Upload to YouTube | You | 15 min |
| 10 | Update the repo | Claude, on your word | 3 min |
| 11 | Fill in the form | You | 5 min |
| 12 | Change the API keys | You | 5 min |

**Three rules, all the way through:**
1. **Never show, paste or read out the API key.** It lives only in `C:\Users\hussa\dev\secrets\spotcool-agent.env`. Never open that file on screen.
2. **Stop programs with Ctrl+C, never by closing the window.** That's how they release the building's controls.
3. **Don't move or resize anything in OBS's preview,** and don't close the terminal during a recording.

---

## Step 1. Get the laptop ready (You, 5 min)

1. **Plug in the charger.**
2. **Keep the LAN cable connected.** Don't unplug it until you've submitted.
3. **Stop the laptop sleeping:** Windows **Settings > System > Power & battery > Screen and sleep**. Set every option to **Never**.
4. **Stop notifications appearing in the recording:** Windows **Settings > System > Notifications**, and turn on **Do not disturb**.
5. **Close** mail, chat, and browser windows you don't need. Anything visible on screen could end up in the video.

---

## Step 2. Set up OBS (You, 10 min)

OBS is the program that records the screen. You already have version 33 installed. If it's ever missing, download it from <https://obsproject.com>.

### 2.1 Open OBS and load our scenes

1. Open **OBS Studio** from the Start menu. If a setup wizard appears, click **Cancel**.
2. You already imported our scenes, so check the top of the window: the title bar should say **Scenes: SpotCool video 2**.
3. **Only if it doesn't:**
   - Click **Scene Collection** in the top menu, then **Import**.
   - Click **...** next to an empty row, and choose `C:\Users\hussa\dev\SpotCool-AI\video\obs\scene_collection.json`.
   - Tick it and click **Import**.
   - Click **Scene Collection** again, and choose the SpotCool one.
4. In the **Scenes** box at the bottom left you should see: Dashboard, Dashboard+Camera, YABE, PhoneCam, Quad. We only use **Quad** and **Dashboard**. Ignore the rest.

### 2.2 Settings

Click **Settings**, at the bottom right.

1. **Output tab:**
   - **Output Mode:** Simple.
   - **Recording Path:** click **Browse**, and choose `C:\Users\hussa\dev\SpotCool-AI\video\takes`.
   - **Recording Quality:** High Quality, Medium File Size.
   - **Recording Format:** `mkv` (a recording survives a crash).
2. **Audio tab:** set **Desktop Audio** and **Mic/Auxiliary Audio** to **Disabled**. Your voice is recorded separately in Step 6, so this keeps room noise out.
3. **Video tab:**
   - **Base (Canvas) Resolution:** `1920x1080`.
   - **Output (Scaled) Resolution:** `1920x1080`.
   - **Common FPS Values:** `30`.
4. Click **OK**.

### 2.3 Fix the three sources

You may have done these already. Check each one anyway.

**A. The dashboard pane**
1. In **Scenes**, click **Quad**.
2. In **Sources**, double-click **Quad dashboard**.
3. Click in the **URL** box, press **Ctrl+A**, and paste:
   ```text
   http://127.0.0.1:8090/?mode=video&panels=floorplan,energy,agent&agent=http://127.0.0.1:8002
   ```
4. Click **OK**.

**B. The full dashboard**
1. In **Scenes**, click **Dashboard**.
2. In **Sources**, double-click **Dashboard browser**.
3. Scroll down, and tick both **Shutdown source when not visible** and **Refresh browser when scene becomes active**.
4. Click **OK**.
5. In **Scenes**, click **Quad** again.

Without this, the camera images in the bottom-left pane go black, as they did in take 1.

**C. The terminal pane** (do Step 3 first if you have no terminal open)
1. In **Sources**, double-click **Agent terminal**.
2. **Window:** choose the entry starting `[WindowsTerminal.exe]`.
3. **Capture Method:** **Modern (WGC)**.
4. **Window Match Priority:** **Match title, otherwise find window of same executable**.
5. Tick **Multi-adapter Compatibility**.
6. Click **OK**.

**What you should see:** the terminal in the bottom-right corner of the preview, and the other three corners black. **Black is correct for now.** They fill in once the program runs.

---

## Step 3. Set up the terminal (You, 3 min)

1. Open **Windows Terminal**. If you have a tab open already, use it.
2. **Font:** click the **˅** next to the **+**, then **Settings > Git Bash > Appearance > Font size**. Set it to **20**, then click **Save**. Close the Settings tab.
3. **Make the window about half your screen's width and height,** so the text looks bigger on video.
4. **From now on,** don't minimize the terminal or close it. Other windows can cover it.

---

## Step 4. Check everything is running (Claude, 2 min)

**Tell Claude:** "check everything for the live take".

Claude checks these three and restarts any that's down:
- the BOPTEST building simulator in the cloud;
- the port forward to it;
- the camera that plays your walking clip.

**Wait for Claude to say "ready".**

---

## Step 5. Record the live screen (You and Claude, 20 min)

Nothing here needs you to be fast. The whole thing runs by itself; you click at three moments.

1. **Start the agent.** Paste this in the terminal and press **Enter**:
   ```bash
   cd /c/Users/hussa/dev/SpotCool-AI && export UV_PROJECT_ENVIRONMENT=C:/Users/hussa/dev/venvs/spotcool-ai && /c/Users/hussa/.local/bin/uv run --no-sync spotcool-agent run --world-host 127.0.0.1 --vision meta --stt meta --llm meta --scene meta --vrf --env-file C:/Users/hussa/dev/secrets/spotcool-agent.env 2>&1 | grep --line-buffered "META"
   ```
   The terminal stays **empty**. That's correct: it only shows Meta's calls, and there are none until the world starts.
2. **Start recording.** In OBS: click **Quad** in the Scenes box, then **Start Recording**, at the bottom right. The red timer at the bottom starts counting.
3. **Tell Claude:** "started". Claude starts the world.
4. **Within about a minute,** the terminal starts showing `META` lines.
   - The three other boxes may stay black. If they do, click **Quad dashboard** in Sources, then **Refresh** above the list. Do the same for **Quad raw feeds** and **Quad SAM boxes**.
5. **The simulated day runs by itself,** from 07:00 to 19:00 in about 12 minutes: one simulated hour per real minute. Don't touch anything.
6. **About 7 minutes after the first `META` lines appear** (Claude also tells you "switch now"):
   - **Write down the red timer** shown at the bottom of OBS, for example `00:08:12`.
   - Click **Dashboard** in the Scenes box.
   - Wait **30 seconds**. You'll see the full dashboard, with the AC unit and its louvers.
   - Click **Quad** again.
7. **Wait for Claude to say "done"** (when the simulated day ends).
8. Click **Stop Recording** in OBS.
9. Click into the terminal, press **Ctrl+C once**, and wait for the `$` prompt.
10. **Tell Claude:** "recorded, I switched at" and the timer you wrote down.

Claude then checks the run's verdict, renames the recording, works out the cut points, and shuts down BOPTEST.

**If something goes wrong partway,** don't panic. Tell Claude what you see. We already have take 1 as a backup, and a new try costs only 15 minutes.

---

## Step 6. Record your voice (You, 20 min)

### 6.1 How to record

1. Use a **quiet room**, with the fan and AC off, and doors closed.
2. **Use either of these. Both work:**
   - **Phone:** its voice recorder app. Hold it about a hand's width from your mouth.
   - **Laptop:** the Windows **Sound Recorder** app (search for it in the Start menu), with a headset if you have one.
3. **Read calmly,** like explaining it to a colleague. Not fast.
4. **Stop for 2 full seconds between sections** (count "one, two" in your head). Don't pause that long inside a section. This pause is how the program finds each section.
5. **If you make a mistake:** pause 2 seconds and read **that whole section again**. Tell Claude which section you repeated, and Claude keeps the last one.
6. **Do one practice read first,** without recording.

### 6.2 How to say the words

| Written | Say |
|---|---|
| 56.1 % | fifty-six point one percent |
| 55 % | fifty-five percent |
| 19.94 % | nineteen point nine four percent |
| 0.33 to 2.13 | zero point three three to two point one three |
| SAM 3.1 | sam three point one |
| HVAC | H-V-A-C (each letter) |
| BMS | B-M-S (each letter) |
| BACnet | back-net |
| BOPTEST | bop-test |
| Kastle | castle |

### 6.3 The script

Read these in this order. The time is how long the section has; it's a guide, not a test.

**1. Hook (22 s)**
> Hi. Offices are emptier now. Kastle's badge data puts US attendance at 56.1 % of its pre-pandemic level. Yet the cooling still runs on a fixed schedule, and in a typical Indian commercial building HVAC is about 55 % of the energy, says the Bureau of Energy Efficiency.

**2. What it is (14 s)**
> Most zones have no occupancy sensor, so the BMS cools an empty room like a full one. SpotCool uses what's already there: the cameras, the BMS and complaints.

**3. The test building (12 s)**
> We built a building to test it: a BACnet BMS, cameras and occupants. BOPTEST runs one office twice, with SpotCool and without.

**4. Live (60 s, the long one)**
> This is SpotCool running live. Top right, the building's cameras, here public dataset clips. Every fifteen minutes of building time, the agent sends each zone camera's snapshot to Meta's SAM 3.1 with one word: humans. Bottom right, Meta's calls as they happen: the model, the endpoint, Meta's request ID, what went in, and the boxes that came back with their confidence. Bottom left, those boxes on the frames. Then our code decides. An empty zone is set to unoccupied at twenty-eight degrees, at BACnet priority ten, and the building's own standby shuts its airflow off. A zone with people is left to the building, and an operator always wins. Top left, the dashboard shows each decision, and the power against the baseline.

**5. Louvers (12 s)**
> In the meeting room, SAM finds the person in the camera. Here, the camera plays a recorded clip. The agent turns the AC's louvers to follow them.

**6. Meta's models (20 s)**
> Meta's SAM 3.1 makes a camera an occupancy sensor. Muse Spark reads clothing and activity, and our physics sets the meeting room's setpoint. Muse Voice hears complaints. Muse Wonder answers them and narrates decisions. No language model decides a BMS write.

**7. Result (20 s)**
> In simulation, SpotCool's agent used 19.94 % less HVAC energy than the baseline, from SAM's occupancy alone, right at every zone-step it saw. Discomfort where people were rose from 0.33 to 2.13 kelvin hours. BOPTEST's occupant heat follows its own schedule.

**8. Rollout (8 s)**
> It uses a building's own BMS and cameras, stores no frame, and an operator always wins.

**9. Close (4 s)**
> SpotCool. Cooling that follows people.

Please don't change the numbers or the wording of the claims: each one is checked against our records. If a sentence is hard to say, tell Claude and it will reword it honestly.

### 6.4 Hand it over

1. **Phone:** copy the file to the laptop by cable, WhatsApp to yourself, or email.
2. Put the file in `C:\Users\hussa\dev\SpotCool-AI\video\raw\`. Any format is fine (m4a, mp3, wav).
3. **Tell Claude:** "voice done", and the file's name.

Claude converts it and checks each section fits. If a section runs long, Claude tells you which one to record again.

---

## Step 7. Build the final video (Claude, 15 min)

**Claude does all of this.** You don't need to do anything.
- draws the slides;
- cuts your screen recording and voice together, with captions;
- runs the automatic quality checks: length, loudness, every number's source.

**Music is optional, and I recommend skipping it,** because it adds licence work and doesn't win anything. If you want it, pick one track from the YouTube Audio Library, give Claude the file and its page link, and Claude adds the credit.

The result is `C:\Users\hussa\dev\SpotCool-AI\video\renders\spotcool.mp4`.

---

## Step 8. Watch it once (You, 5 min)

Double-click `spotcool.mp4`. Watch it all the way through with sound, and check:

1. **Privacy:** no key, email, notification, browser tab or private window in any frame. Pause if you're unsure.
2. **The four panes** show, and the terminal's `META` lines can be read.
3. **Your voice** matches what's on screen.
4. **The result card** says "SpotCool saved (SAM 3.1 perception, in simulation)".
5. **In the louvers part,** the louvers move, and the tile says "recorded clip".
6. **The last slide** shows `github.com/IAMMOJAHID/SpotCool-AI`.

If anything's wrong, tell Claude the time in the video and what you saw.

---

## Step 9. Upload to YouTube (You, 15 min)

1. Go to <https://www.youtube.com> and sign in. Click **Create** (the camera icon, top right), then **Upload video**.
2. Click **Select files**, and choose `C:\Users\hussa\dev\SpotCool-AI\video\renders\spotcool.mp4`.
3. **Title:**
   ```text
   SpotCool: cooling that follows people (Meta AI hackathon)
   ```
4. **Description:** paste this. Every figure in it is checked:
   ```text
   SpotCool's agent watches each zone of an office through Meta's SAM 3.1 and drives the building's BACnet BMS: empty zones get a raised setpoint and their airflow shut off, booked rooms are pre-cooled, and an operator's override always wins. In the meeting room, the AC's louvers follow a person on camera. Muse Spark (muse-spark-1.3) reads what people wear and do, for the room's comfort setpoint; Muse Voice (muse-voice-transcribe-1.0) turns a spoken complaint into text; Muse Wonder (muse-wonder-1.0) works out what it means, answers it, and narrates each step's decisions; Muse Image (muse-image-1.0) can redraw the cameras as illustrations for a privacy view. The models perceive and code decides: no language model decides a BMS write. In simulation (BOPTEST's multizone office, a hybrid Tuesday), it used 19.94 % less HVAC energy than the baseline, and was right about which zones had people at every step it perceived; discomfort where people were rose from 0.33 to 2.13 K·h. BOPTEST's occupant heat follows its own schedule, not the cameras. Camera footage: MEVA dataset (Kitware Inc. and IARPA), CC BY 4.0. Building model: BOPTEST (IBPSA), BSD-3-Clause.
   ```
5. **Audience:** select **No, it's not made for kids**.
6. Click **Next** three times, until you reach **Visibility**.
7. Select **Unlisted**, then **Save**. Unlisted means only people with the link can see it, which is what the form needs. If the form asks for a public video, choose **Public**.
8. **Copy the link** YouTube shows.
9. **Wait until YouTube finishes processing,** then open the link and watch a few seconds to confirm it plays in HD.

---

## Step 10. Update the repo (Claude, on your word, 3 min)

Judges open the repo's front page, which shows the `main` branch. Right now `main` has almost nothing, and everything is on `dev`.

**Tell Claude:** "the video link is" followed by the link, "and update main from dev".

Claude puts the link in the README and updates `main`.

**Then check it yourself:** open <https://github.com/IAMMOJAHID/SpotCool-AI>. The front page should say "SpotCool: cooling that follows people", with your video link near the top.

---

## Step 11. Fill in the form (You, 5 min)

Read the form's own rules first. Our notes say: a video of 3 minutes or less (ours is 2:56), a public repo, contributors listed, built during the event, and third-party licences listed (ours are in `CREDITS.md`).

Give:
- **Video:** your YouTube link.
- **Repo:** `https://github.com/IAMMOJAHID/SpotCool-AI`
- **Team:** your team's names.
- **If it asks which Meta models you used:** `sam-3.1`, `muse-spark-1.3`, `muse-voice-transcribe-1.0`, `muse-wonder-1.0`, `muse-image-1.0`. The full explanation is `docs/META_MODELS.md`, which you can link.

Then **submit**, and take a screenshot of the confirmation page.

---

## Step 12. Change the API keys (You, 5 min)

Do this **after submitting**, and soon: the repo's history contains old copies of these keys.

1. In Meta's developer console, revoke the current API key and create a new one.
2. In Baseten's console, do the same.
3. If you'll use the new Meta key here, open `C:\Users\hussa\dev\secrets\spotcool-agent.env` (not on a shared screen) and replace the value after `META_API_KEY=`.

Claude can't do this for you, and you should never paste a key into chat.

---

## If something goes wrong

| What you see | What to do |
|---|---|
| OBS's preview is all black, even the terminal | Redo Step 2.3 C. Check **Modern (WGC)** and **Multi-adapter Compatibility** |
| The three browser panes stay black during a recording | Click each one in **Sources**, then **Refresh** |
| The camera tiles in the bottom-left pane go black, with only boxes showing | Redo Step 2.3 B, then **Refresh** Quad SAM boxes |
| The terminal shows a red error, or says `command not found` | Copy the text and paste it to Claude. Don't start recording |
| The terminal stays empty for over 2 minutes after "started" | Tell Claude. The world may not have started |
| OBS says it can't start recording | Check Step 2.2's Recording Path exists, and tell Claude |
| Your voice is too quiet or noisy | Record again closer to the microphone, in a quieter place |
| You're running out of time | Tell Claude. Take 1 already exists, so the live take can be skipped, and a phone voice recording is enough |
| Anything else | Copy what you see, or send a screenshot, and tell Claude |

**For Claude, or a fresh session:** the exact commands, the world's start command, and how to set the `in_s` cut points are in `docs/agent/HANDOFF.md`, "Resume here".
