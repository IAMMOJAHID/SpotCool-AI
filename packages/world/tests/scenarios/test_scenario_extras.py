"""Scenarios, the ScenarioProcessor and ``lint`` need only the core dependencies.

CI installs every extra, so this test is what keeps fastapi (the ``calendar`` extra) and httpx
(the ``occupants`` extra) out of the core's imports.
"""

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = """
import sys

for name in ("fastapi", "httpx"):
    sys.modules[name] = None  # importing it now raises ImportError

import spotcool_world
import spotcool_world.processors
import spotcool_world.scenarios
import spotcool_world.transports.http
import spotcool_world.workers
from spotcool_world.processors import FramePublisher, ScenarioProcessor
from spotcool_world.runner.cli import main
from spotcool_world.scenarios import compile_scenario, load_scenario

ScenarioProcessor(compile_scenario(load_scenario("hybrid_tuesday").scenario, step_s=900.0))
assert main(["lint"]) == 0
"""


def test_scenarios_and_lint_run_without_the_calendar_and_occupants_extras(tmp_path: Path) -> None:
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
    assert "3 scenario(s): 0 error(s)" in result.stdout
