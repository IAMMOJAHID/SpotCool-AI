import json
from pathlib import Path

import pytest

from spotcool_world import __version__
from spotcool_world.runner.cli import main


@pytest.fixture(autouse=True)
def empty_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # no stray world.yaml
    monkeypatch.delenv("SPOTCOOL_WORLD_PLANT", raising=False)


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_dry_run_prints_resolved_settings(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SPOTCOOL_WORLD_PLANT", "replay")
    assert main(["run", "--dry-run"]) == 0
    settings = json.loads(capsys.readouterr().out)
    assert settings["plant"] == "replay"
    assert settings["testcase"] == "multizone_office_simple_air"


def test_a_subcommand_is_required() -> None:
    with pytest.raises(SystemExit):
        main([])
