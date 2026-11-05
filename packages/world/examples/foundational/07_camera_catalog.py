"""07: The camera catalog: the world's CCTV cameras over HTTP, as SpotCool's agent sees them.

The cameras look like a video recorder: GET /cameras lists them (a CameraCatalog), and
GET /cameras/{id}/snapshot.jpg returns a camera's latest frame, like ONVIF GetSnapshotUri.
This example plays the hybrid Tuesday from 07:00, one 15-minute step a second: the
ScenarioProcessor says what each zone shows, and the CameraSceneProcessor puts it on the zone
cameras, so the scenes change every few seconds from 08:30. Without footage the cameras draw
synthetic frames, one coloured silhouette per person.

Open the printed catalog URL in a browser, then a snapshot or MJPEG URL: the frames change with
the scenes. --live-url adds a phone running IP Webcam as cam-meeting-live (packages/world/
AGENTS.md has the phone steps); --footage DIR plays DIR/clips/<zone>_<scene>.mp4 instead of
synthetic frames. It exits after --ticks steps; Ctrl+C stops it sooner.

Run: uv run python packages/world/examples/foundational/07_camera_catalog.py --ticks 8
"""

import argparse
import asyncio
import sys
from pathlib import Path

from loguru import logger

from spotcool_world.clocks import SimClock, WallPacer
from spotcool_world.frames import CameraSceneFrame, Frame
from spotcool_world.pipeline import Pipeline, PipelineParams
from spotcool_world.processors import (
    CameraSceneProcessor,
    FrameDirection,
    FrameProcessor,
    ScenarioProcessor,
    TickSource,
)
from spotcool_world.runner import WorkerRunner
from spotcool_world.scenarios import compile_scenario, load_scenario
from spotcool_world.services import WorldSettings
from spotcool_world.services.camera_service import create_cameras
from spotcool_world.transports.http.cameras import cameras_router
from spotcool_world.transports.http.server import create_http_app
from spotcool_world.workers import PipelineWorker
from spotcool_world.workers.http_server import HttpServerWorker

STEP_S = 900.0


class SceneChanges(FrameProcessor):
    """Prints each camera's scene change."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, CameraSceneFrame) and frame.sim_time_s is not None:
            data = frame.data
            print(f"{SimClock.clock_text(frame.sim_time_s)}  {data.camera_id:<8} -> {data.scene}")
        await self.push_frame(frame, direction)


async def main(args: argparse.Namespace) -> int:
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    settings = WorldSettings(
        http_host=args.host,
        http_port=args.port,
        footage_dir=args.footage,
        live_camera_url=args.live_url,
    )
    try:
        cameras = create_cameras(settings)
    except ValueError as exc:
        print(f"cannot set up the cameras: {exc}")
        return 1
    compiled = compile_scenario(load_scenario("hybrid_tuesday").scenario, step_s=STEP_S)
    clock = SimClock(compiled.start_s, STEP_S)
    end_s = min(compiled.start_s + args.ticks * STEP_S, compiled.end_s)
    pipeline = Pipeline(
        [
            TickSource(clock, WallPacer(args.speed), end_s),
            ScenarioProcessor(compiled),
            CameraSceneProcessor(cameras),
            SceneChanges(),
        ]
    )
    params = PipelineParams(clock=clock, scenario=compiled.name, plant="none", end_time_s=end_s)
    day = PipelineWorker(pipeline, params=params, name="hybrid-tuesday")
    app = create_http_app([cameras_router(cameras)])
    http = HttpServerWorker(app, settings.http_host, settings.http_port, name="http")

    @day.event_handler("on_pipeline_finished")
    async def stop_serving(worker: PipelineWorker, frame: Frame) -> None:
        await http.stop("the day is over")

    base = f"http://{args.host}:{args.port}"
    print(f"Camera catalog: {base}/cameras")
    print(f"Snapshots:      {base}/cameras/cam-cor/snapshot.jpg (and every id in the catalog)")
    print(f"MJPEG:          {base}/cameras/cam-cor/mjpeg")
    await cameras.start()
    try:
        await WorkerRunner().run(day, http)
    except OSError as exc:
        print(f"cannot serve HTTP: {exc}. Another program on port {args.port}? Try --port 8082.")
        return 1
    finally:
        await cameras.stop()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="The world's camera catalog over HTTP.")
    parser.add_argument("--ticks", type=int, default=48, help="15-minute steps to play (48: a day)")
    parser.add_argument("--speed", type=float, default=900.0, help="sim seconds per wall second")
    parser.add_argument("--host", default="127.0.0.1", help="address to serve on")
    parser.add_argument("--port", type=int, default=8081, help="TCP port (default 8081)")
    parser.add_argument(
        "--live-url", help="the phone's snapshot URL, e.g. http://192.168.43.1:8080/shot.jpg"
    )
    parser.add_argument("--footage", type=Path, help="a footage folder with clips/ (default: none)")
    sys.exit(asyncio.run(main(parser.parse_args())))
