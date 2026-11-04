# Video takes

The video has one take since the Nov 5 re-cut: [the live quad take](#the-live-quad-take), recorded by hand, which feeds the `live` and `louvers` beats. The three scripted takes below it (the BMS in YABE, the building replayed, the VRF filmed live with the phone) are kept for reference; the storyboard no longer shows them, so `take.py` finds their beats only once one is put back in it.

**The other takes are scripted.** `scripts/video/take.py <take>` drives OBS over obs-websocket from `video/storyboard.yaml`:

- it starts and stops the recording;
- it switches the scenes and prints the cues at the beat's times;
- for the building, it starts the replay itself.

It saves the recording as `video/takes/<take>.mkv`, where `scripts/video/assemble.py` finds it. Set OBS up once ([video/README.md](../../video/README.md), step 4: import `video/obs/scene_collection.json`, enable the WebSocket server, `export OBS_WS_PASSWORD=...`). `--dry-run` prints a take's schedule without OBS.

Background: [AGENTS.md, Running the world](../../packages/world/AGENTS.md#running-the-world) (Speed, Replay) and [The VRF gateway](../../packages/world/AGENTS.md#the-vrf-gateway). The numbers the narration may use are in [docs/release/numbers.md](../release/numbers.md).

## Before any take

1. The environment block of the [runbooks' README](README.md) is set, `SPOTCOOL_WORLD_FOOTAGE_DIR` included.
2. **The footage is found.** The lint must list no error and no `footage_dir is not set` warning:

   ```bash
   uv run spotcool-world lint --clips C:/Users/hussa/dev/footage/clips
   ```

   Then, while a run starts, its console names each camera's source once per scene: `camera cam-<zone>: scene <scene> from <...>/clips/<zone>_<scene>.mp4` (or a dataset segment), never `from synthetic frames`. The catalog (`curl http://127.0.0.1:8081/cameras`) lists each camera with `"source": "scenario"` or `"dataset"`; it says `scenario` for synthetic frames too, so the console line is the check.
3. The dashboard is built: `cd apps/dashboard && npm ci && npm run build`. The world serves it on port 8090.
4. One network for the whole session ([boptest-codespace.md](boptest-codespace.md), step 1). For Person A's PC, the [firewall rules](windows-firewall.md).
5. **One dashboard tab only**, in video mode: `http://127.0.0.1:8090/?mode=video`. Add `&agent=http://<agent>:8002` when Person A's agent runs. Its six camera tiles use all six of Edge's connections to the world, so a second tab starves them.
   - In a scripted take, the tab is OBS's `Dashboard browser` source, and `take.py` sets its URL (with `--agent`, the `&agent=` part).
   - Close the dashboard in Edge while a take runs.
6. Every run stops with **Ctrl+C**, never by closing its window or killing the process. Ctrl+C stops the BOPTEST tests and writes the run's manifest. A second Ctrl+C cancels at once.

## The BMS segment: an operator override in YABE

1. Start the world on loopback with the stub agent, and write the override at priority 8 before 08:30 sim time: [yabe.md](yabe.md), The operator override for the video, steps 1 and 2.
2. Once the agent panel shows the back-off (from 08:30), record the take. It follows the cues: it shows the priority array in YABE, then the back-off on the dashboard, then you release the override at the cue:

   ```bash
   uv run python scripts/video/take.py yabe_override
   ```

3. Stop the world with Ctrl+C.

## The building segment: replay a recorded run

1. **Record the run** once, paced. With Person A's agent:

   ```bash
   uv run spotcool-world run --plant boptest-local --speed 60 --run-id take-building
   ```

   Without A, add `--stub-agent`; the dashboard and the report then label the run a stub run. BOPTEST needs [the Codespace](boptest-codespace.md). Speed 60 lets each camera clip's action play, such as the 14:15 complaint.
2. **Check it:** `uv run spotcool-world report take-building`. Quote only what [numbers.md](../release/numbers.md) allows.
3. **Record the take.** The video replays the live eval run, `eval-live-hybrid-20261103-122941`, the run the results card quotes; to replay another, change the dashboard beat's `replay.run_id` in `video/storyboard.yaml`.

   ```bash
   uv run python scripts/video/take.py dashboard_replay
   ```

   It does the steps below in order:
   1. It points the `Dashboard browser` source at a blank page, so no viewer starts the replay early.
   2. It starts `spotcool-world replay <run_id> --wait-viewer 60 --speed 2700` (the beat's speed: 07:00 to 19:00 in 16 s), and waits for its dashboard.
   3. It starts OBS recording, and waits until OBS reports it is writing.
   4. As the beat starts (2 s in), it loads the dashboard. The replay starts when the page connects, so the whole day plays inside the beat's 21 s, with time left for the page to load.
   5. At the beat's end, it stops the replay and the recording.
4. **By hand, without the script:** run `uv run spotcool-world replay take-building --speed 60 --wait-viewer 60`. In OBS, start recording, then open (or reload) the one video-mode tab. The replay starts when the tab connects, and ends on its own at the record's end. To cut it short, press Ctrl+C in the replay's window.

## The VRF segment: filmed live

1. **The phone camera:** [phone-camera.md](phone-camera.md). Set `SPOTCOOL_WORLD_LIVE_CAMERA_URL`.
2. **Start SpotCool's agent first** (its own window), with the VRF loop and Meta's models ([packages/agent/README.md, The louvers demo](../../packages/agent/README.md)):

   ```bash
   uv run spotcool-agent run --vrf --vision meta --stt meta --llm meta --env-file C:/Users/hussa/dev/secrets/spotcool-agent.env
   ```

   Then **start the world a few minutes before the take**, with the Mitsubishi profile. The room model starts at 30 degC ambient and settles toward the unit's setpoint with a 2-minute time constant, so after about 6 minutes it has settled. At speed 1 the world runs in real time until Ctrl+C:

   ```bash
   SPOTCOOL_WORLD_VRF_PROFILE=mitsubishi uv run spotcool-world run --plant rc --speed 1
   ```

   The world prints `VRF <this PC's address>:47810 (BACnet/IP)`. The agent finds it by itself (`Model_Name` gives the brand). Walk across the phone's view: `IDU-VANE-H` steps Pos1 to Pos5 (the agent's log and the dashboard show each `decision`).
3. Record the take. With `$SPOTCOOL_WORLD_LIVE_CAMERA_URL` set, it points OBS's `Phone camera` source at the phone's `/video` stream. It opens the dashboard with `&agent=`, and switches between `PhoneCam` and `Dashboard+Camera` at the cues. Person A's agent aims the louvers, and the AC panel shows them slew at 15 deg/s:

   ```bash
   uv run python scripts/video/take.py phone_vrf --agent http://127.0.0.1:8002
   ```

4. After the take, press Ctrl+C in the agent's window (it releases the VRF and BMS slots), then in the world's.

The louvers are filmed live, never replayed: a replay plays the VRF back once a step, so the louvers would jump instead of slewing.

## The live quad take

**This is the video's take since the Nov 5 re-cut (AG10).** One recording feeds two beats of `video/storyboard.yaml`: `live` (60 s of the `Quad` scene) and `louvers` (12 s of the `Dashboard` scene around 14:00). It was recorded this way on Nov 5, as run `live-quad-boptest-s60` (`Quotable`, [numbers.md](../release/numbers.md)). The YABE, replay and phone takes above are kept for reference; the video no longer uses them.

Four panes at once, from OBS's `Quad` scene: the dashboard (floor plan, energy and the agent's feed) top left, the raw MEVA feeds top right, the same feeds with the boxes SAM 3.1 found bottom left, and the agent's terminal bottom right, with the `META` lines for Meta's calls (the meeting room's SAM calls are sampled, 1 in 10). It is recorded by hand in OBS; `take.py` doesn't record it.

The three browser sources are 960x540 views of the dashboard (`?panels=` and `?masks=off`, [apps/dashboard/README.md](../../apps/dashboard/README.md)). They shut down when the `Quad` scene is not shown, so they never take camera connections from the other takes. The raw feeds load the cameras from `localhost:8081` and the boxes pane from `127.0.0.1:8081`: Edge counts its six connections per host, and each pane needs six.

1. **The terminal.** Open Git Bash, title it `SpotCool agent` (OBS's `Agent terminal` source captures the window by that title) and set its font to 16 pt or more (Options > Text):

   ```bash
   echo -ne "\033]0;SpotCool agent\007"
   ```

2. **The meeting room's camera:** serve the recorded walking clip ([below](#the-meeting-room-from-a-recorded-clip-no-live-phone-needed), steps 1 and 2) on port 8099.
3. **Start the agent first**, in that terminal, on loopback, with all four Meta models and the louvers:

   ```bash
   uv run spotcool-agent run --vrf --vision meta --stt meta --llm meta --scene meta --env-file C:/Users/hussa/dev/secrets/spotcool-agent.env --world-host 127.0.0.1 2>&1 | grep --line-buffered META
   ```

   Each SAM call logs two lines, `META -> sam-3.1 | POST https://api.meta.ai/v1/responses | count humans on cam-cor | ...` as it goes out and `META <- sam-3.1 | 200 in 1.87 s | request-id ... | out: 2 boxes [...]` as it comes back (`META x` if it failed). They never hold an image or words.

4. **Then start the world** in another window, on BOPTEST ([boptest-codespace.md](boptest-codespace.md)), with the MEVA footage (`SPOTCOOL_WORLD_FOOTAGE_DIR`, [Before any take](#before-any-take), step 2), the clip as the meeting room's camera, and on loopback, at speed 60:

   ```bash
   SPOTCOOL_WORLD_BMS_ADDRESS=127.0.0.1 SPOTCOOL_WORLD_HTTP_HOST=127.0.0.1 \
   SPOTCOOL_WORLD_LIVE_CAMERA_URL=http://127.0.0.1:8099/shot.jpg SPOTCOOL_WORLD_VRF_PROFILE=mitsubishi \
   uv run spotcool-world run --plant boptest-local --speed 60 --run-id <run id>
   ```

5. **In OBS**, point the `Dashboard browser` source at a blank page (Properties > URL `about:blank`), so it takes none of the camera connections, close the dashboard in Edge, and switch to the `Quad` scene. Check that all four panes show.
6. **Record from sim 08:15:** start when the agent's terminal logs `step 08:00: people ...` (the step that ends at 08:15; the quad has no clock), about 75 s after the world starts (at speed 60 a 15-minute step takes 15 s). The 08:30 arrivals then show: the boxes appear, the zones flip, and the writes go out in the agent's feed and terminal. Keep recording through the `live` beat's 60 s and more.
7. **The louvers:** around sim 14:00, set the `Dashboard browser` URL back to `http://127.0.0.1:8090/?mode=video&agent=http://127.0.0.1:8002`, switch to the `Dashboard` scene for about 30 s while the AC unit panel follows the person in the clip, then stop the recording.
8. Press Ctrl+C in the agent's window (it releases its slots), then in the world's, then the clip camera's. The run must say `Quotable` in its report before the video quotes it.
9. **Put it in place:** rename OBS's file (named by date) to `video/takes/live_quad.mkv`. Note the second the `Quad` segment you want starts and the second the `Dashboard` segment starts, and set them as `in_s` of the `live` and `louvers` beats in `video/storyboard.yaml` (both are `0` placeholders until then).

## The meeting room from a recorded clip (no live phone needed)

The world polls the meeting room's camera for one JPEG at a time. `scripts/footage/clip_camera.py` answers those polls from a recorded clip, looped on the wall clock, so the louvers can follow a person without a live phone. SAM 3.1 and the louvers still run live on the frames; only the camera's source is a recording. Say so on screen: "The meeting room's camera plays a recorded clip".

1. **Film the clip.** On the phone, hold it landscape and still, on a shelf at about head height. Film 30 seconds: walk in from the left edge, cross slowly to the right, sit for 5 s, then walk back. Copy it to `C:\Users\hussa\dev\footage\raw\meeting_walk.mp4`, outside the repo.

   The dataset clips barely move sideways: in `admin2_one` the person stays at the right edge, and in `hall_quiet` the people stay near the centre.
2. **Serve it**, in its own terminal:
   ```bash
   uv run python scripts/footage/clip_camera.py C:/Users/hussa/dev/footage/raw/meeting_walk_30.mp4 --port 8099
   ```
   Then open `http://127.0.0.1:8099/shot.jpg` and **look at the frame**. Two takes went out with a forgotten camera's clip on this tile. The script now refuses a port that's in use.
3. **Start the agent with `--vrf`.** Then start the world with `SPOTCOOL_WORLD_LIVE_CAMERA_URL=http://127.0.0.1:8099/shot.jpg` and `SPOTCOOL_WORLD_VRF_PROFILE=mitsubishi`. The louvers' angle points follow the person in the clip; the dashboard's AC unit panel shows them.
