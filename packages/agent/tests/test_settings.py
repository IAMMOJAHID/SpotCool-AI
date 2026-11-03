import json
from pathlib import Path

import pytest

from spotcool_agent.runner.cli import main
from spotcool_agent.settings import AgentSettings, load_settings, primary_ipv4

KEY = "sk-not-a-real-key-0123456789"


@pytest.fixture(autouse=True)
def no_agent_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("META_API_KEY", "SPOTCOOL_AGENT_META_API_KEY", "SPOTCOOL_AGENT_VISION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    path = tmp_path / "agent.env"
    path.write_text(f"META_API_KEY={KEY}\nBASETEN_API_KEY=other\n", encoding="utf-8")
    return path


def test_the_key_comes_from_the_env_file_and_never_shows(env_file: Path) -> None:
    settings = load_settings(env_file)
    assert settings.meta_api_key is not None
    assert settings.meta_api_key.get_secret_value() == KEY
    for shown in (repr(settings), str(settings), settings.model_dump_json()):
        assert KEY not in shown


def test_flags_beat_the_environment_which_beats_the_env_file(
    env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SPOTCOOL_AGENT_VISION", "scripted")
    assert load_settings(env_file).vision == "scripted"
    assert load_settings(env_file, vision="blob", settle_s=None).vision == "blob"
    assert load_settings(env_file).settle_s == 1.0  # None leaves a setting alone


@pytest.mark.parametrize(
    "bad",
    [{"world_host": "localhost"}, {"listen": "8002"}, {"bms_port": 70000}, {"vision": "x"}],
)
def test_invalid_settings_are_refused(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        AgentSettings(**bad)  # type: ignore[arg-type]


def test_the_dry_run_prints_the_settings_with_the_key_masked(
    env_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["run", "--vision", "blob", "--dry-run", "--env-file", str(env_file)])
    out, err = capsys.readouterr()
    assert code == 0
    assert KEY not in out + err
    assert '"meta_api_key": "**********"' in out
    assert '"vision": "blob"' in out


def test_the_dry_run_without_a_key_shows_none_and_bad_flags_fail(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["run", "--vision", "blob", "--dry-run"]) == 0
    assert '"meta_api_key": null' in capsys.readouterr().out
    assert main(["run", "--world-host", "localhost", "--dry-run"]) == 1
    assert "invalid settings" in capsys.readouterr().out


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.startswith("spotcool-agent 0.1.0 (contracts ")


def test_the_world_host_is_this_machines_primary_ipv4_by_default() -> None:
    import ipaddress

    host = AgentSettings().world_host
    assert host == primary_ipv4()
    ipaddress.IPv4Address(host)


def test_stt_and_llm_follow_vision_unless_set() -> None:
    assert (AgentSettings(vision="meta").stt, AgentSettings(vision="meta").llm) == ("meta", "meta")
    assert (AgentSettings(vision="blob").stt, AgentSettings(vision="blob").llm) == ("fake", "off")
    chosen = AgentSettings(vision="blob", stt="meta", llm="fake")
    assert (chosen.stt, chosen.llm) == ("meta", "fake")


def test_the_vrf_loop_is_off_by_default_and_its_flags_reach_the_settings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = AgentSettings()
    assert not settings.vrf and settings.vrf_port == 47810
    assert (settings.vrf_camera, settings.vrf_brand, settings.vrf_mirror) == (
        "cam-meeting-live",
        None,
        False,
    )
    assert (settings.vrf_camera_height_m, settings.vrf_camera_tilt_deg) == (2.8, 30.0)
    assert (settings.vrf_hold_s, settings.draft_hold_s, settings.complaint_hold_s) == (
        10.0,
        600.0,
        900.0,
    )
    assert (settings.vrf_bacnet_port, settings.vrf_call_cap, settings.llm_call_cap) == (
        47821,
        3000,
        200,
    )
    flags = ["run", "--vision", "blob", "--vrf", "--vrf-brand", "daikin", "--vrf-mirror"]
    flags += ["--vrf-camera-tilt-deg", "40", "--llm", "fake", "--dry-run"]
    flags += ["--vrf-bacnet-port", "0", "--vrf-call-cap", "5", "--llm-call-cap", "6"]
    assert main(flags) == 0
    shown = json.loads(capsys.readouterr().out)
    assert (shown["vrf_bacnet_port"], shown["vrf_call_cap"], shown["llm_call_cap"]) == (0, 5, 6)
    assert (shown["vrf"], shown["vrf_brand"], shown["vrf_mirror"]) == (True, "daikin", True)
    assert (shown["vrf_camera_tilt_deg"], shown["stt"], shown["llm"]) == (40.0, "fake", "fake")
    assert main(["run", "--vision", "blob", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["vrf"] is False


def test_meta_models_without_a_key_fail_to_start() -> None:
    from spotcool_agent.runner.agent import create_language

    with pytest.raises(ValueError, match="--stt meta needs META_API_KEY"):
        create_language(AgentSettings(vision="blob", stt="meta"))
    with pytest.raises(ValueError, match="--llm meta needs META_API_KEY"):
        create_language(AgentSettings(vision="blob", llm="meta"))
