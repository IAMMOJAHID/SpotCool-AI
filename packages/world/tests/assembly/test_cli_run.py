"""``spotcool-world run`` and ``replay`` on the command line, and the CLI's logging."""

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from spotcool_world.runner.cli import main
from spotcool_world.services import WorldSettings

EXAMPLE = Path(__file__).parents[2] / "world.example.yaml"
MakeSettings = Callable[..., WorldSettings]


def test_dry_run_takes_the_flags(capsys: pytest.CaptureFixture[str]) -> None:
    arguments = ["--plant", "boptest-local", "--scenario", "empty_friday", "--speed", "0"]
    assert main(["run", *arguments, "--dry-run"]) == 0
    settings = json.loads(capsys.readouterr().out)
    assert (settings["plant"], settings["scenario"], settings["speed"]) == (
        "boptest-local",
        "empty_friday",
        None,  # 0: as fast as possible
    )


def test_a_config_file_gives_way_to_the_environment_and_the_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "custom.yaml"
    text = "plant: boptest-hosted\nscenario: all_hands_day\nspeed: 60\nhttp_port: 9001\n"
    config.write_text(text, encoding="utf-8")
    monkeypatch.setenv("SPOTCOOL_WORLD_SCENARIO", "empty_friday")
    assert main(["run", "--config", str(config), "--speed", "120", "--dry-run"]) == 0
    settings = json.loads(capsys.readouterr().out)
    assert (settings["plant"], settings["http_port"]) == ("boptest-hosted", 9001)  # the file
    assert settings["scenario"] == "empty_friday"  # the environment
    assert settings["speed"] == 120.0  # the flag


def test_the_example_settings_file_shows_the_defaults(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--config", str(EXAMPLE), "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out) == json.loads(WorldSettings().model_dump_json())


@pytest.mark.parametrize(
    "arguments",
    [["--speed", "-1"], ["--config", "missing.yaml"], ["--plant", "replay"]],
)
def test_bad_arguments_are_refused(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as refused:
        main(["run", *arguments])
    assert refused.value.code == 2


def cli(arguments: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run ``spotcool-world`` in a process of its own, as a person would."""
    command = [sys.executable, "-m", "spotcool_world.runner.cli", *arguments]
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=180)


@pytest.mark.bacnet
def test_run_replay_and_report_from_the_command_line(
    tmp_path: Path, make_settings: MakeSettings, two_hours: str
) -> None:
    ports = make_settings()  # free loopback ports
    env = {key: value for key, value in os.environ.items() if not key.startswith("SPOTCOOL_WORLD")}
    env |= {
        "SPOTCOOL_WORLD_RUN_DIR": str(tmp_path / "runs"),
        "SPOTCOOL_WORLD_HTTP_HOST": "127.0.0.1",
        "SPOTCOOL_WORLD_HTTP_PORT": str(ports.http_port),
        "SPOTCOOL_WORLD_DASHBOARD_PORT": str(ports.dashboard_port),
        "SPOTCOOL_WORLD_BMS_ADDRESS": "127.0.0.1",
        "SPOTCOOL_WORLD_BMS_PORT": str(ports.bms_port),
    }
    arguments = ["--scenario", two_hours, "--speed", "0", "--stub-agent", "--run-id", "cli-run"]
    run = cli(["run", *arguments], tmp_path, env)
    assert run.returncode == 0, run.stdout + run.stderr
    out = run.stdout
    assert out.startswith(
        "spotcool-world run cli-run: two_hours on the rc plant, as fast as possible"
    )
    assert f"  cameras       http://127.0.0.1:{ports.http_port}/cameras\n" in out
    assert f"  event stream  ws://127.0.0.1:{ports.dashboard_port}/ws/events\n" in out
    assert f"  BMS           127.0.0.1:{ports.bms_port} (BACnet/IP)\n" in out
    assert "run cli-run finished (end time reached)" in out
    kpis = r"  spotcool  energy [\d.]+ kWh, comfort where people were [\d.]+ K h, energy saved [\d.]+ %"
    assert re.search(kpis, out)
    assert re.search(r"  baseline  energy [\d.]+ kWh, comfort where people were [\d.]+ K h\n", out)
    assert out.endswith("report: spotcool-world report cli-run\n")

    replay = cli(["replay", "cli-run", "--speed", "0"], tmp_path, env)
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert replay.stdout.startswith("spotcool-world replay cli-run: ")
    assert replay.stdout.endswith("replay cli-run finished\n")

    report = cli(["report", "cli-run"], tmp_path, env)
    assert report.returncode == 0, report.stdout + report.stderr
    assert (tmp_path / "runs" / "cli-run" / "report.md").is_file()


LOGGING = """
import sys

from loguru import logger

from spotcool_world.runner.cli import configure_logging

configure_logging("INFO")
logger.add(sys.stdout, diagnose=True, format="{message}")  # the control: it shows values


def record():
    audio = b"SECRET-AUDIO-BYTES"
    raise ValueError(len(audio))


try:
    record()
except ValueError:
    logger.exception("recording failed")
logger.debug("below INFO")
"""


def test_the_cli_logs_tracebacks_without_variable_values(tmp_path: Path) -> None:
    script = tmp_path / "log_a_failure.py"  # a file: tracebacks show its lines and their values
    script.write_text(LOGGING, encoding="utf-8")
    command = [sys.executable, str(script)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    logged = re.sub(r"\x1b\[[0-9;]*m", "", result.stderr)  # without colours
    assert "recording failed" in logged
    assert "ValueError: 18" in logged
    assert "SECRET-AUDIO-BYTES" not in logged  # Law 6
    assert "SECRET-AUDIO-BYTES" in result.stdout
    assert "below INFO" not in logged


WITHOUT_BACNET = """
import sys

sys.modules["bacpypes3"] = None  # importing it now raises ImportError

from spotcool_world.runner.cli import main

assert main(["run", "--dry-run"]) == 0
assert main(["run"]) == 1
assert main(["replay", "some-run"]) == 1
"""


def test_run_and_replay_name_a_missing_extra(tmp_path: Path) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("SPOTCOOL_WORLD")}
    command = [sys.executable, "-c", WITHOUT_BACNET]
    result = subprocess.run(
        command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert "run needs the world's extras (bacpypes3 is missing)" in result.stdout
    assert "replay needs the world's extras (bacpypes3 is missing)" in result.stdout
