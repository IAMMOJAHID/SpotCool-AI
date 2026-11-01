# WP4: Camera system (simulated CCTV and a live phone camera)

**Tool:** Claude Code. **Branch:** `wp/wp4-cameras`, cut from `world/main` (`2a65685`).
**Depends on:**
- WP0 and contracts (`cameras.py`, `zones.py`, `events.py`);
- WP5: `footage_dir`, the scenes, `ScenarioStateFrame` and the calendar router;
- WP8: the clip names, and the dataset index format in `scripts/footage/wisenet_index.py`.

Footage is optional, because synthetic frames cover tests, CI and an emergency demo. The real clips are filmed on Nov 2.

## Goal
A camera system that looks like a real recorder or VMS to Person A's agent:
- `GET /cameras` returns a `spotcool_contracts.cameras.CameraCatalog`.
- `GET /cameras/{id}/snapshot.jpg` returns the camera's **latest frame** as a JPEG, the same idea as ONVIF GetSnapshotUri.
- `cam-cor`, `cam-nor`, `cam-sou`, `cam-eas` and `cam-wes` show the scene the scenario asks for in their zone.
- `cam-meeting-live` proxies a real Android phone.

## Your lane (only these paths)
- `packages/world/src/spotcool_world/services/camera_service.py`. WP4 is its only user, so reshape the WP0 base as the design needs.
- `services/clips/`, `services/datasets/`, `services/live/`.
- `transports/http/server.py`, `transports/http/cameras.py`, and the `transports/http/__init__.py` docstring.
- `processors/camera_scene.py`, plus its export in `processors/__init__.py`.
- `workers/http_server.py`: the uvicorn server worker (decision 8).
- `services/settings.py`: add `http_host` and `live_camera_url` only (decisions 8 and 9).
- `packages/world/pyproject.toml`: add `httpx` to the `cameras` extra and `uvicorn[standard]` to the `calendar` extra, then run `uv lock` and commit `uv.lock`.
- `packages/world/AGENTS.md`: add a "Cameras" section, and the phone and firewall steps under "Windows notes". Change nothing else there.
- `packages/world/tests/cameras/`, `examples/foundational/07_camera_catalog.py`, and the changelog fragment `packages/world/changelog/+wp4-cameras.added.md`.

`packages/contracts/` is read-only. If you think a contract must change, stop and report it.

## Decisions already made (binding)
1. **Playback model.** The scene follows sim time, but the frames follow wall time, as a real camera does while the simulation runs faster.
   - `CameraSceneProcessor` sets each camera's scene from the scenario on every tick.
   - The frames advance in **wall time** from the moment the camera's scene last changed. Clips loop, and a scene switch restarts at the new clip's frame 0.
   - Don't time-compress clips. At `speed=300` a 15-minute step lasts 3 s of wall time, so a scene shows only its clip's first seconds. Runs that need a clip's action (the 14:15 complaint) use a slower speed, 60 or less. Document this.
2. **Where a camera's frames come from**, for each `(camera, scene)`, in this order:
   - our own clip `footage_dir/clips/<zone>_<scene>.mp4`;
   - else a dataset segment from the index (decision 6), when the index and its files exist;
   - else **synthetic frames**, which are always available and draw the scenario's people count.

   The catalog's `source` is `scenario` for clips and synthetic frames, and `dataset` for datasets. Log once per `(camera, scene)` which source is used.
3. **Synthetic frames** (`services/clips/synthetic.py`):
   - a plain room background with N filled person silhouettes, each in a distinct, saturated colour, at the camera's size;
   - a small caption with the camera id and scene;
   - deterministic for a given (camera, scene, people, frame index).

   Tests count the people by colour blobs. Synthetic frames are for tests, CI and an emergency demo; a person detector isn't expected to see their silhouettes as people.
4. **CPU and the event loop.**
   - OpenCV calls block, so decoding and encoding run in threads (`asyncio.to_thread`) and never on the event loop. The world's lockstep tick shares that loop.
   - Each reader decodes at `snapshot_fps` (5 by default), skipping frames with `grab()`, and keeps only the latest encoded JPEG (quality 80).
   - Measure the five clip cameras together on this laptop, and report CPU and snapshot latency. They should stay under one core.
   - Readers start when the camera service starts and stop in its `stop()`; no task may leak.
5. **Frames are 1280×720**, matching WP8's normalized clips; the synthetic and dataset sources produce the same size. The catalog's `width`, `height` and `fps_hint` describe what's served.
6. **Datasets** (`services/datasets/`):
   - Read the index format that `scripts/footage/wisenet_index.py` documents (version 1, `yaml.safe_load`). Its paths are relative to `index.yaml`. The index sits at `footage_dir/datasets/<name>/index.yaml`.
   - Use only segments whose files exist.
   - The WiseNET download isn't done yet, so test with a tiny video that the test generates (a few frames, written to the test's tmp dir by the test itself, never by the camera code).
7. **HTTP app** (`transports/http/server.py`): `create_http_app(routers)` is one FastAPI app.
   - It includes the cameras router and WP5's `calendar_router(store)`.
   - CORS allows `http://127.0.0.1:*` and `http://localhost:*`, for the dashboard.
   - `GET /healthz` returns 200.
8. **Server worker** (`workers/http_server.py`): `HttpServerWorker`, a `BaseWorker`.
   - `HttpServerWorker(app, host, port)` runs `uvicorn.Server` for any FastAPI app, with WebSockets and lifespan on. WP11 passes `settings.http_host` with `http_port` for the cameras and calendar app, and with `dashboard_port` for WP6's dashboard app.
   - Add `http_host: str = "0.0.0.0"` (env `SPOTCOOL_WORLD_HTTP_HOST`), because A's agent may connect from another PC.
   - `stop()` asks uvicorn to exit and waits for it.
   - A taken port fails `run()` at once with a clear error.
   - The catalog builds each camera's absolute URLs from the incoming request (`request.url_for`), so they're right for whoever asked.
9. **Live phone** (`services/live/`):
   - Add `live_camera_url: str | None = None` (env `SPOTCOOL_WORLD_LIVE_CAMERA_URL`, for example `http://192.168.43.1:8080/shot.jpg` from the "IP Webcam" app). `cam-meeting-live` is in the catalog only when it's set.
   - Poll at 2 fps or less with httpx and a 2 s timeout.
   - Serve only a response that is a JPEG (check the magic bytes) and under 5 MB.
   - Keep the last good frame. Once it's 5 s old, set the header `X-Camera-Stale: 1` on the snapshot.
   - Until the first good frame arrives, return 503. A failing phone is never fatal to the world.
   - A laptop webcam through OpenCV is optional.
10. **Snapshot API** (`transports/http/cameras.py`):
    - `image/jpeg` with `Cache-Control: no-store`;
    - 404 for an unknown id, and 503 until a camera has its first frame;
    - `GET /cameras/{id}/mjpeg` (`multipart/x-mixed-replace`) at 5 fps or less, for the dashboard's tiles; it ends cleanly when the client disconnects and never buffers without bound.
11. **`CameraSceneProcessor`** (`processors/camera_scene.py`). It's a pass-through processor; WP11 places it after the BMS output.
    - On each `ScenarioStateFrame`, it passes the frame on, sets every zone camera's scene and people count on the camera service, and pushes a `CameraSceneFrame` (contract `CameraSceneData`) for each camera whose scene changed.
    - The camera ↔ zone mapping comes from `spotcool_contracts.zones` (`ZoneInfo.camera_id`).
    - The same camera service object is shared with the HTTP router; WP11 wires it.
12. **Privacy (AGENTS.md Law 6).**
    - Frames live in memory only. Nothing writes images, and nothing logs frame bytes.
    - Use no `logger.exception` where frames are in scope; log `type(exc).__name__` and the message.
    - The test patches `builtins.open` (write modes), `cv2.imwrite` and `cv2.VideoWriter` to fail while serving 50 snapshots and some MJPEG parts. It also checks that the captured logs hold no JPEG bytes.
13. **Not in this WP:** the optional RTSP/MediaMTX video wall. Nothing uses it; it goes to the backlog.

## Tests (`packages/world/tests/cameras/`)
- The catalog validates against the contract model, with and without `live_camera_url`.
- A snapshot decodes as a 1280×720 JPEG.
- A `ScenarioStateFrame` switches the scene, and the next snapshot comes from the new source. With synthetic frames, the people count is decodable by colour blobs.
- The source order for a (camera, scene): clip, then dataset, then synthetic. Use a tiny generated video.
- The live proxy: timeout, non-JPEG and oversize responses, the stale header, and 503 before the first frame (httpx `MockTransport`).
- MJPEG: the parts are well formed, and a disconnect ends the stream.
- The server worker: starts, serves `/healthz`, stops cleanly, and fails at once on a taken port.
- Calendar mounting: `create_http_app` serves WP5's schedule endpoint too.
- The privacy test.
- No leaked tasks: green with `-W error::RuntimeWarning`.

## Done when
- **The four checks are green** with all extras installed:
  - `uv run ruff check packages`
  - `uv run ruff format --check packages`
  - `uv run pyright packages`
  - `uv run pytest packages`
- `07_camera_catalog.py` serves the synthetic cameras on 127.0.0.1:8081, switches scenes every few seconds, and prints the catalog URL; a browser shows changing snapshots.
- The CPU and latency measurement from decision 4 is in your report.
- The phone steps are documented. Checking a real phone over the hotspot is a manual step for the user (like YABE), so report it as not done.
- The changelog fragment is written, and nothing outside your lane changed except `uv.lock`.
