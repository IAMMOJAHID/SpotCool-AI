"""``spotcool-world run`` and ``replay`` on the command line, and the CLI's logging."""

import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from spotcool_world.runner import cli as cli_module
from spotcool_world.runner.cli import main
from spotcool_world.runner.report import REPORT_FILE, STUB_AGENT_NOTE
from spotcool_world.serializers import EVENTS_FILE, MANIFEST_FILE, RunManifest
from spotcool_world.services import WorldSettings
from spotcool_world.services import rc as rc_module
from spotcool_world.services.rc import RcPlant

EXAMPLE = Path(__file__).parents[2] / "world.example.yaml"
MakeSettings = Callable[..., WorldSettings]


def test_dry_run_takes_the_flags(capsys: pytest.CaptureFixture[str]) -> None:
    arguments = ["--plant", "boptest-local", "--scenario", "empty_friday", "--speed", "0"]
    assert main(["run", *arguments, "--stub-agent", "--dry-run"]) == 0
    settings = json.loads(capsys.readouterr().out)
    assert (settings["plant"], settings["scenario"], settings["speed"]) == (
        "boptest-local",
        "empty_friday",
        None,  # 0: as fast as possible
    )
    assert settings["stub_agent"] is True


def test_the_stub_agent_is_a_setting(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert main(["run", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["stub_agent"] is False
    monkeypatch.setenv("SPOTCOOL_WORLD_STUB_AGENT", "true")
    assert main(["run", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["stub_agent"] is True


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
    [
        ["run", "--speed", "-1"],
        ["run", "--config", "missing.yaml"],
        ["run", "--plant", "replay"],
        ["replay", "a-run", "--wait-viewer", "-1"],
        ["replay", "a-run", "--config", "missing.yaml"],
    ],
)
def test_bad_arguments_are_refused(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as refused:
        main(arguments)
    assert refused.value.code == 2


def cli(arguments: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run ``spotcool-world`` in a process of its own, as a person would."""
    command = [sys.executable, "-m", "spotcool_world.runner.cli", *arguments]
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=180)


def clean_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith("SPOTCOOL_WORLD")}


@pytest.mark.bacnet
def test_run_replay_and_report_with_a_config_file(
    tmp_path: Path, make_settings: MakeSettings, two_hours: str
) -> None:
    ports = make_settings()  # free loopback ports
    runs = tmp_path / "runs"
    config = tmp_path / "test-world.yaml"
    config.write_text(
        f"run_dir: {runs.as_posix()}\nhttp_host: 127.0.0.1\nhttp_port: {ports.http_port}\n"
        f"dashboard_port: {ports.dashboard_port}\nbms_address: 127.0.0.1\n"
        f"bms_port: {ports.bms_port}\n",
        encoding="utf-8",
    )
    env = clean_environment()
    arguments = ["--config", str(config), "--scenario", two_hours, "--speed", "0", "--stub-agent"]
    run = cli(["run", *arguments, "--run-id", "cli-run"], tmp_path, env)
    assert run.returncode == 0, run.stdout + run.stderr
    out = run.stdout
    assert out.startswith(
        "spotcool-world run cli-run: two_hours on the rc plant, as fast as possible, stub agent"
    )
    assert f"  cameras       http://127.0.0.1:{ports.http_port}/cameras\n" in out
    assert f"  event stream  ws://127.0.0.1:{ports.dashboard_port}/ws/events\n" in out
    assert f"  BMS           127.0.0.1:{ports.bms_port} (BACnet/IP)\n" in out
    assert "run cli-run finished (end time reached)" in out
    assert f"\n  {STUB_AGENT_NOTE}\n" in out
    for run in ("spotcool", "baseline"):  # the RC plant's own energy is the same sum
        energy = rf"  {run:<9} energy ([\d.]+) kWh \(plant's own \1 kWh, difference 0\.000 %\), "
        assert re.search(energy + r"comfort where people were [\d.]+ K h\n", out), out
    saved = (
        r"  energy saved ([\d.]+) % by the plants' own energy, \1 % by the step-mean "
        r"integration\n"
    )
    assert re.search(saved, out), out
    assert (
        "  Not quotable:\n"
        "    - The plant is rc, our own reduced model, and its cross-check is circular: both "
        "energies come from the same sub-step sums.\n"
        "    - The run was unpaced (speed 0): the agent's writes land at random points between "
        "steps, so it does not reproduce.\n"
        "    - A stub agent ran it, with perfect perception: quote it only as an upper bound with "
        "perfect perception, labelled so.\n"
    ) in out
    run_dir, config_file = (shlex.quote(p.resolve().as_posix()) for p in (runs, config))
    assert out.endswith(
        f"report: SPOTCOOL_WORLD_RUN_DIR={run_dir} spotcool-world report cli-run\n"
        f"replay: spotcool-world replay cli-run --config {config_file}\n"
    )

    record = {name: (runs / "cli-run" / name).read_bytes() for name in (EVENTS_FILE, MANIFEST_FILE)}
    again = cli(["run", *arguments, "--run-id", "cli-run"], tmp_path, env)
    assert again.returncode == 1
    assert again.stdout.startswith("run failed: ")  # before anything started
    assert "already holds a recording" in again.stdout
    assert "Traceback" not in again.stderr
    assert {name: (runs / "cli-run" / name).read_bytes() for name in record} == record

    replay = cli(["replay", "cli-run", "--config", str(config), "--speed", "0"], tmp_path, env)
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert replay.stdout.startswith("spotcool-world replay cli-run: ")
    assert replay.stdout.endswith("replay cli-run finished\n")

    report = cli(["report", "cli-run"], tmp_path, env | {"SPOTCOOL_WORLD_RUN_DIR": str(runs)})
    assert report.returncode == 0, report.stdout + report.stderr
    top = (runs / "cli-run" / REPORT_FILE).read_text(encoding="utf-8").splitlines()[:4]
    assert f"**{STUB_AGENT_NOTE}**" in top


class BrokenPlant(RcPlant):
    """An RC plant whose every step fails."""

    async def step(self, overrides: Mapping[str, float | None]) -> dict[str, float]:
        raise RuntimeError("the plant broke")


@pytest.mark.bacnet
def test_a_run_ended_by_a_fatal_error_is_failed_in_its_record_and_printout(
    make_settings: MakeSettings,
    two_hours: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(rc_module, "RcPlant", BrokenPlant)
    monkeypatch.setattr(cli_module, "configure_logging", lambda level: None)  # keep pytest's
    settings = make_settings()
    for name in ("run_dir", "http_host", "http_port", "dashboard_port", "bms_address", "bms_port"):
        monkeypatch.setenv(f"SPOTCOOL_WORLD_{name.upper()}", str(getattr(settings, name)))
    assert main(["run", "--scenario", two_hours, "--speed", "0", "--run-id", "broken"]) == 1
    path = settings.run_dir / "broken" / MANIFEST_FILE
    done = RunManifest.model_validate_json(path.read_text(encoding="utf-8"))
    assert done.ended == "failed"
    out = capsys.readouterr().out
    assert f"run broken failed ({done.end_reason}); recorded in" in out
    assert "plant step failed: the plant broke" in done.end_reason


INVALID = """
import os

from spotcool_world.runner.cli import main

os.environ["SPOTCOOL_WORLD_BMS_ADDRESS"] = "localhost"
assert main(["run"]) == 1
del os.environ["SPOTCOOL_WORLD_BMS_ADDRESS"]
os.environ["SPOTCOOL_WORLD_LOG_LEVEL"] = "VERBOSE"
assert main(["run"]) == 1
assert main(["replay", "some-run"]) == 1
"""


def test_invalid_settings_fail_the_run_without_a_traceback(tmp_path: Path) -> None:
    command = [sys.executable, "-c", INVALID]
    result = subprocess.run(
        command, cwd=tmp_path, env=clean_environment(), capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "run failed: bms_address 'localhost' is not an IPv4 address such as 192.168.1.20 or "
        "192.168.1.20/24",
        "run failed: log_level 'VERBOSE' is not a log level, such as INFO or DEBUG",
        "replay failed: log_level 'VERBOSE' is not a log level, such as INFO or DEBUG",
    ]
    assert "Traceback" not in result.stderr


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
    command = [sys.executable, "-c", WITHOUT_BACNET]
    result = subprocess.run(
        command, cwd=tmp_path, env=clean_environment(), capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert "run needs the world's extras (bacpypes3 is missing)" in result.stdout
    assert "replay needs the world's extras (bacpypes3 is missing)" in result.stdout
