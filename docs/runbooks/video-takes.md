# Video takes

The video has two live parts: the building (a recorded run, replayed for the camera) and the meeting room's VRF unit (filmed live with the phone). Background: [AGENTS.md, Running the world](../../packages/world/AGENTS.md#running-the-world) (Speed, Replay) and [The VRF gateway](../../packages/world/AGENTS.md#the-vrf-gateway). The numbers the narration may use are in [docs/release/numbers.md](../release/numbers.md).

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
6. Every run stops with **Ctrl+C**, never by closing its window or killing the process. Ctrl+C stops the BOPTEST tests and writes the run's manifest. A second Ctrl+C cancels at once.

## The building segment: replay a recorded run

1. **Record the run** once, paced. With Person A's agent:

   ```bash
   uv run spotcool-world run --plant boptest-local --speed 60 --run-id take-building
   ```

   Without A, add `--stub-agent`; the dashboard and the report then label the run a stub run. BOPTEST needs [the Codespace](boptest-codespace.md). Speed 60 lets each camera clip's action play, such as the 14:15 complaint.
2. **Check it:** `uv run spotcool-world report take-building`. Quote only what [numbers.md](../release/numbers.md) allows.
3. **Start the replay.** It waits up to 60 s for the dashboard before the first event:

   ```bash
   uv run spotcool-world replay take-building --speed 60 --wait-viewer 60
   ```

4. In OBS, start recording, then open (or reload) the one video-mode tab. The replay starts when the tab connects.
5. The replay ends on its own at the record's end. Stop OBS. To cut it short, press Ctrl+C in the replay's window.

## The VRF segment: filmed live

1. **The phone camera:** [phone-camera.md](phone-camera.md). Set `SPOTCOOL_WORLD_LIVE_CAMERA_URL`.
2. **Start the world a few minutes before the take**, with the Mitsubishi profile. The room model starts at 30 degC ambient and settles toward the unit's setpoint with a 2-minute time constant, so after about 6 minutes it has settled. At speed 1 the world runs in real time until Ctrl+C:

   ```bash
   SPOTCOOL_WORLD_VRF_PROFILE=mitsubishi uv run spotcool-world run --plant rc --speed 1
   ```

   The world prints `VRF <this PC's address>:47810 (BACnet/IP)` for Person A's agent.
3. Open the one video-mode tab with `&agent=http://<agent>:8002`, then start OBS. Person A's agent aims the louvers; the AC panel shows them slew at 15 deg/s.
4. After the take, stop OBS, then press Ctrl+C in the world's window.

The louvers are filmed live, never replayed: a replay plays the VRF back once a step, so the louvers would jump instead of slewing.
