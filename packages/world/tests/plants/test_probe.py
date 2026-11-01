import os
from pathlib import Path

import pytest

from spotcool_world.runner.cli import main


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in list(os.environ):
        if key.startswith("SPOTCOOL_WORLD_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)  # no stray world.yaml


def test_probe_on_the_rc_plant(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["probe", "--plant", "rc", "--steps", "2"]) == 0
    out = capsys.readouterr().out
    assert "peak_cool_day from day 193 00:00" in out
    assert "cooling sp 28.00 degC" in out
    assert "step 2" in out and "step 3" not in out
    assert "ener_tot=" in out and "pgas_tot=n/a" in out
    assert "plant calls (wall time):" in out


def test_probe_needs_at_least_one_step() -> None:
    with pytest.raises(SystemExit):
        main(["probe", "--steps", "0"])
