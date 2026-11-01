"""The telemetry processors, the recorder, the report module and the CLI need only the core.

CI installs every extra, so this test is what keeps matplotlib (the ``report`` extra) and FastAPI,
Starlette and uvicorn (the ``dashboard`` extra) out of their imports. Without matplotlib the
``report`` command says which extra to install.
"""

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = """
import sys
from datetime import UTC, datetime
from pathlib import Path

for name in ("matplotlib", "fastapi", "starlette", "uvicorn"):
    sys.modules[name] = None  # importing it now raises ImportError

import spotcool_world.processors
import spotcool_world.runner.report
import spotcool_world.serializers
import spotcool_world.transports.websocket
from spotcool_world.processors import (
    EventEmitterProcessor, KpiProcessor, RecorderProcessor, TelemetryProcessor,
)
from spotcool_world.runner.cli import main
from spotcool_world.serializers import EVENTS_FILE, MANIFEST_FILE, RunManifest
from spotcool_world.services import WorldSettings

RecorderProcessor(WorldSettings())
folder = Path("runs", "empty")
folder.mkdir(parents=True)
(folder / EVENTS_FILE).write_text("", encoding="utf-8")
manifest = RunManifest(
    run_id="empty", created_utc=datetime.now(UTC), ended="finished", end_reason="", settings={},
    git_sha=None, world_version="0", contracts_version="0", scenario="", scenario_sha256=None,
    plant="rc", step_s=900.0, start_time_s=0.0, end_time_s=0.0, final_kpis={}, dropped_events=0,
)
(folder / MANIFEST_FILE).write_text(manifest.model_dump_json(), encoding="utf-8")
assert main(["report", "empty"]) == 1
"""


def test_telemetry_the_recorder_and_the_cli_run_without_the_report_and_dashboard_extras(
    tmp_path: Path,
) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SPOTCOOL_WORLD_")}
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        cwd=tmp_path,  # no stray world.yaml; runs go to ./runs
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "report needs the report extra" in result.stdout
