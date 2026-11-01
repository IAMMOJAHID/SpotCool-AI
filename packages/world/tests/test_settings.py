import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from spotcool_world.services import WorldSettings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Run every test in an empty directory with no SPOTCOOL_WORLD_* variables.
    for key in list(os.environ):
        if key.startswith("SPOTCOOL_WORLD_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


def test_defaults() -> None:
    settings = WorldSettings()
    assert settings.plant == "rc"
    assert settings.step_s == 900.0
    assert settings.speed == 300.0
    assert (settings.start_clock, settings.end_clock) == ("07:00", "19:00")
    assert (settings.bms_port, settings.vrf_port) == (47808, 47810)
    assert settings.run_dir == Path("runs")


def test_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPOTCOOL_WORLD_PLANT", "boptest-hosted")
    monkeypatch.setenv("SPOTCOOL_WORLD_STEP_S", "300")
    monkeypatch.setenv("SPOTCOOL_WORLD_SPEED", "none")
    monkeypatch.setenv("SPOTCOOL_WORLD_BMS_PORT", "47900")
    settings = WorldSettings()
    assert settings.plant == "boptest-hosted"
    assert settings.step_s == 300.0
    assert settings.speed is None
    assert settings.bms_port == 47900


def test_world_yaml_is_read_and_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    Path("world.yaml").write_text("scenario: empty_friday\nvrf_profile: daikin\n", encoding="utf-8")
    monkeypatch.setenv("SPOTCOOL_WORLD_VRF_PROFILE", "lg")
    settings = WorldSettings()
    assert settings.scenario == "empty_friday"
    assert settings.vrf_profile == "lg"


def test_unknown_yaml_key_is_an_error() -> None:
    Path("world.yaml").write_text("scenaro: typo\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        WorldSettings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SPOTCOOL_WORLD_PLANT", "boptest"),
        ("SPOTCOOL_WORLD_START_CLOCK", "7am"),
        ("SPOTCOOL_WORLD_END_CLOCK", "24:00"),
        ("SPOTCOOL_WORLD_HTTP_PORT", "70000"),
        ("SPOTCOOL_WORLD_SPEED", "0"),
    ],
)
def test_invalid_values_are_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        WorldSettings()
