"""The core, the processors and the camera base run without the ``cameras`` extra.

CI installs every extra, so this test is what keeps OpenCV, numpy, httpx, FastAPI and uvicorn
out of the core's imports: ``processors/__init__`` exports ``CameraSceneProcessor``.
"""

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = """
import sys

for name in ("cv2", "numpy", "httpx", "fastapi", "starlette", "uvicorn"):
    sys.modules[name] = None  # importing it now raises ImportError

import spotcool_world.processors
import spotcool_world.services
import spotcool_world.services.datasets
import spotcool_world.transports.http
import spotcool_world.workers
from spotcool_contracts.cameras import CameraSource
from spotcool_contracts.zones import ZONES
from spotcool_world.processors import CameraSceneProcessor
from spotcool_world.services import WorldSettings
from spotcool_world.services.camera_service import (
    CameraService,
    CameraSpec,
    CameraSystem,
    create_cameras,
)


class Idle(CameraService):
    def __init__(self, camera_id):
        self.camera_id = camera_id

    @property
    def spec(self):
        return CameraSpec(self.camera_id, "idle", None, 1280, 720, 5.0, CameraSource.SCENARIO)

    def latest(self):
        return None

    async def run(self):
        return None

    def set_scene(self, scene, people):
        return None


CameraSceneProcessor(CameraSystem([Idle(info.camera_id) for info in ZONES.values()]))
try:
    create_cameras(WorldSettings())
except ImportError:
    print("create_cameras needs the cameras extra")
"""


def test_the_core_runs_without_the_cameras_extra(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SPOTCOOL_WORLD_")}
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        cwd=tmp_path,  # no stray world.yaml
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "create_cameras needs the cameras extra" in result.stdout
