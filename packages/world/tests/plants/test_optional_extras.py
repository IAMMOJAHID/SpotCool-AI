"""The core and the CLI must run without the provider extras (httpx for BOPTEST, numpy for RC).

CI installs every extra, so this test is what enforces the rule that optional dependencies
are imported only inside their provider sub-packages.
"""

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = """
import sys

for name in ("httpx", "numpy"):
    sys.modules[name] = None  # importing it now raises ImportError

import spotcool_world.adapters
import spotcool_world.bus
import spotcool_world.clocks
import spotcool_world.frames
import spotcool_world.observers
import spotcool_world.pipeline
import spotcool_world.processors
import spotcool_world.runner
import spotcool_world.serializers
import spotcool_world.services
import spotcool_world.services.boptest.catalog
import spotcool_world.services.replay
import spotcool_world.testing
import spotcool_world.transports
import spotcool_world.workers
from spotcool_world.runner.cli import main

assert main(["version"]) == 0
assert main(["run", "--dry-run"]) == 0
"""


def test_the_core_and_the_cli_run_without_the_provider_extras(tmp_path: Path) -> None:
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
    assert "spotcool-world" in result.stdout
