# Video takes

The video has three screen parts: the BMS in YABE (an operator override), the building (a recorded run, replayed for the camera) and the meeting room's VRF unit (filmed live with the phone).

**The takes are scripted.** `scripts/video/take.py <take>` drives OBS over obs-websocket from `video/storyboard.yaml`:

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
   1. It starts `spotcool-world replay <run_id> --wait-viewer 60 --speed 1800` (the beat's speed: the day in 24 s), and waits for its dashboard.
   2. It starts OBS recording, and reloads the `Dashboard browser` source. The replay starts when the source connects.
   3. At the beat's end, it stops the recording and then the replay.
4. **By hand, without the script:** run `uv run spotcool-world replay take-building --speed 60 --wait-viewer 60`. In OBS, start recording, then open (or reload) the one video-mode tab. The replay starts when the tab connects, and ends on its own at the record's end. To cut it short, press Ctrl+C in the replay's window.

## The VRF segment: filmed live

1. **The phone camera:** [phone-camera.md](phone-camera.md). Set `SPOTCOOL_WORLD_LIVE_CAMERA_URL`.
2. **Start the world a few minutes before the take**, with the Mitsubishi profile. The room model starts at 30 degC ambient and settles toward the unit's setpoint with a 2-minute time constant, so after about 6 minutes it has settled. At speed 1 the world runs in real time until Ctrl+C:

   ```bash
   SPOTCOOL_WORLD_VRF_PROFILE=mitsubishi uv run spotcool-world run --plant rc --speed 1
   ```

   The world prints `VRF <this PC's address>:47810 (BACnet/IP)` for Person A's agent.
3. Record the take. With `$SPOTCOOL_WORLD_LIVE_CAMERA_URL` set, it points OBS's `Phone camera` source at the phone's `/video` stream. It opens the dashboard with `&agent=`, and switches between `PhoneCam` and `Dashboard+Camera` at the cues. Person A's agent aims the louvers, and the AC panel shows them slew at 15 deg/s:

   ```bash
   uv run python scripts/video/take.py phone_vrf --agent http://<agent>:8002
   ```

4. After the take, press Ctrl+C in the world's window.

The louvers are filmed live, never replayed: a replay plays the VRF back once a step, so the louvers would jump instead of slewing.
