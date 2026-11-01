# WP4: Camera system (simulated CCTV and a live phone camera)

**Tool:** Antigravity. **Branch:** `wp/wp4-cameras`. **Depends on:** WP0 and contracts. Footage from WP8 is optional, because a synthetic source covers tests.
**Directories:** `packages/world/src/spotcool_world/services/{clips,datasets,live}/`, `transports/http/{server.py,cameras.py}`, `transports/rtsp/` (optional), `processors/camera_scene.py`, `tests/cameras/`, `examples/foundational/07_camera_catalog.py`.
**Extra:** `cameras` (fastapi, uvicorn[standard], numpy, opencv-python-headless).

## Goal
A camera system that looks like a real recorder/VMS to the agent:
- `GET /cameras` returns a `spotcool_contracts.cameras.CameraCatalog`;
- `GET /cameras/{id}/snapshot.jpg` returns the camera's **latest frame** as a JPEG, the same idea as ONVIF GetSnapshotUri;
- cameras `cam-cor`, `cam-nor`, `cam-sou`, `cam-eas` and `cam-wes` show the zone scene the scenario asks for;
- `cam-meeting-live` proxies a real Android phone.

## Design
1. **`CameraService` providers** (base class from WP0). Each camera keeps a **reader** that advances in **wall** time and holds only the latest encoded JPEG in memory, the way a real camera behaves.
   - **`clips/`:**
     - a `ClipLibrary` maps `(camera_id, scene)` to a clip file under `WorldSettings.footage_dir`;
     - `footage_dir: Path | None = None` is in the settings from WP5 (env `SPOTCOOL_WORLD_FOOTAGE_DIR`, usual location `C:/Users/hussa/dev/footage`, outside the repo);
     - clips loop; a scene switch restarts at the new clip's frame 0.
   - **`clips/synthetic.py`:** generated frames (a room background with N drawn person silhouettes) so CI, tests and an emergency demo need **no footage**.
   - **`datasets/`:** WiseNET and MEVA adapters driven by an index YAML that WP8 writes. The index maps `(camera_id, scene)` to a dataset file plus start/end seconds, and records the people count in that segment.
   - **`live/`:**
     - a phone running "IP Webcam" at `http://<phone-ip>:8080/shot.jpg`, proxied with httpx (2 s timeout); when stale, keep serving the last good frame and set the header `X-Camera-Stale: 1`;
     - optionally a laptop webcam through OpenCV.
2. **`transports/http/server.py`:** `create_http_app(settings, routers)` is one FastAPI app on `http_port` (8081), shared with WP5's calendar router.
   - CORS allows `http://127.0.0.1:*` and `http://localhost:*`, for the dashboard.
   - `GET /healthz`.
3. **`transports/http/cameras.py`:** the router.
   - The catalog builds absolute URLs from settings.
   - Snapshots return `image/jpeg` with `Cache-Control: no-store`; 404 for an unknown id; 503 while a source isn't ready.
   - Optional `GET /cameras/{id}/mjpeg` (multipart) for the dashboard's live tiles.
4. **`processors/camera_scene.py`:** `CameraSceneProcessor`. On `ScenarioStateFrame` it sets each zone camera's scene, and pushes a `CameraSceneFrame` when a scene changes.
5. **Privacy guard:**
   - frames live in memory only; nothing writes images or logs frame bytes;
   - test this by patching `builtins.open` (write modes) and `cv2.imwrite`/`cv2.VideoWriter` to fail while serving 50 snapshots.
6. **Optional `transports/rtsp/mediamtx.py`:** launches MediaMTX and ffmpeg (if both binaries are on PATH) to publish the same scenes as RTSP for a video wall. Tests skip when the binaries are absent.

## Tests
- The catalog validates against the contract model.
- A snapshot decodes as a JPEG of the configured size.
- A `ScenarioStateFrame` switches the scene, and the next snapshot comes from the new source. Check this with the synthetic source: the person count is drawn and decodable by colour blobs.
- The live proxy's timeout and stale behaviour, tested with httpx `MockTransport`.
- The privacy test.

## Done when
- The README checks are green.
- `07_camera_catalog.py` serves the synthetic cameras, and the dashboard (or a browser) shows changing snapshots.
- The phone proxy works on a real phone over the hotspot. Document the setup in AGENTS.md's Windows notes.
