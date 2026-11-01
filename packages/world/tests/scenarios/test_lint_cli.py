from collections.abc import Callable
from pathlib import Path

import pytest

from spotcool_world.runner.cli import main

WriteScenario = Callable[..., Path]


def test_lint_checks_the_whole_library_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["lint"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == "3 scenario(s): 0 error(s), 1 warning(s)"
    assert "hybrid_tuesday.yaml:" in out[0] and ": warning: footage_dir is not set" in out[0]


def test_footage_dir_comes_from_the_settings(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "south_complaint.wav").touch()
    monkeypatch.setenv("SPOTCOOL_WORLD_FOOTAGE_DIR", str(tmp_path))
    assert main(["lint", "hybrid_tuesday"]) == 0
    assert capsys.readouterr().out.splitlines() == ["1 scenario(s): 0 error(s), 0 warning(s)"]


def test_an_error_prints_its_line_and_exits_1(
    capsys: pytest.CaptureFixture[str], write_scenario: WriteScenario, base_yaml: str
) -> None:
    path = write_scenario(base_yaml.replace('booked_at: "13:45"', 'booked_at: "14:15"'))
    assert main(["lint", str(path), "empty_friday"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"{path}:21: error: booked_at 14:15 is after the booking's from 14:00",
        "2 scenario(s): 1 error(s), 0 warning(s)",
    ]


def test_an_unknown_scenario_or_file_is_an_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert main(["lint", "monday", str(tmp_path / "nowhere.yaml")]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("monday: error: unknown scenario 'monday'")
    assert out[1].startswith(f"{tmp_path / 'nowhere.yaml'}: error: ")
    assert out[2] == "2 scenario(s): 2 error(s), 0 warning(s)"


def test_alias_files_are_findings_and_the_next_scenario_is_still_linted(
    capsys: pytest.CaptureFixture[str], write_scenario: WriteScenario
) -> None:
    loop = write_scenario("name: &loop [*loop]\n", "loop.yaml")
    # 2**39 nodes if the aliases were followed
    chain = "".join(f"a{i}: &a{i} [*a{i - 1}, *a{i - 1}]\n" for i in range(1, 40))
    nested = write_scenario(f"a0: &a0 [x, x]\n{chain}", "nested.yaml")
    assert main(["lint", str(loop), str(nested), "empty_friday"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert f"{loop}:1: error: aliases are not supported" in out
    assert f"{nested}:2: error: aliases are not supported" in out
    assert not any("empty_friday.yaml" in line for line in out)  # linted, and clean
    assert out[-1].startswith("3 scenario(s): ") and out[-1].endswith(", 0 warning(s)")


def test_clips_are_checked_when_a_clip_directory_is_given(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    clips = tmp_path / "clips"
    clips.mkdir()
    for name in ("cor_empty", "cor_few", "nor_empty", "sou_empty", "eas_empty", "wes_empty"):
        (clips / f"{name}.mp4").touch()
    assert main(["lint", "empty_friday", "--clips", str(clips)]) == 0
    assert capsys.readouterr().out.splitlines() == ["1 scenario(s): 0 error(s), 0 warning(s)"]
    (clips / "cor_few.mp4").unlink()
    assert main(["lint", "empty_friday", "--clips", str(clips)]) == 0
    assert f"warning: clip cor_few.mp4 is not in {clips}" in capsys.readouterr().out


def test_the_step_grid_can_be_changed(
    capsys: pytest.CaptureFixture[str], write_scenario: WriteScenario, base_yaml: str
) -> None:
    path = write_scenario(base_yaml.replace('"08:30", scene: many', '"08:35", scene: many'))
    assert main(["lint", str(path)]) == 1
    assert main(["lint", str(path), "--step-s", "300"]) == 0
    capsys.readouterr()


@pytest.mark.parametrize("args", [["--step-s", "0"], ["--step-s", "nan"], ["--clips", "nowhere"]])
def test_bad_options_are_usage_errors(args: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_:
        main(["lint", *args])
    assert exit_.value.code == 2
