from collections.abc import Callable
from pathlib import Path

import pytest

from spotcool_world.scenarios import Finding, library_names, lint, load_scenario
from spotcool_world.scenarios.schema import DoorOpenEvent

STEP_S = 900.0
WP8_CLIPS = [  # scripts/footage/normalize.py SCENES: the clips WP8 filmed
    "cor_empty", "cor_few", "cor_many", "nor_empty", "nor_few", "sou_empty", "sou_meeting",
    "sou_complaint", "eas_empty", "wes_empty", "wes_few", "wes_door_open",
]  # fmt: skip
WriteScenario = Callable[..., Path]


def line_of(text: str, needle: str) -> int:
    """1-based line of the first line containing ``needle``."""
    return next(n for n, line in enumerate(text.splitlines(), 1) if needle in line)


def edit(text: str, old: str, new: str) -> str:
    assert old in text, old
    return text.replace(old, new, 1)


def lint_text(
    write_scenario: WriteScenario,
    text: str,
    *,
    footage_dir: Path | None = None,
    clips_dir: Path | None = None,
) -> list[Finding]:
    path = write_scenario(text)
    return lint(path, step_s=STEP_S, footage_dir=footage_dir, clips_dir=clips_dir)


def test_the_base_scenario_is_clean(write_scenario: WriteScenario, base_yaml: str) -> None:
    assert lint_text(write_scenario, base_yaml) == []


@pytest.mark.parametrize(
    ("old", "new", "needle", "message"),
    [
        pytest.param(
            "  eas:\n",
            '  lab:\n    - {at: "07:00", scene: empty, people: 0}\n  eas:\n',
            "lab:",
            "zones.lab: Input should be 'cor', 'nor', 'sou', 'eas' or 'wes'",
            id="unknown zone",
        ),
        pytest.param(
            "room: meeting-south",
            "room: boardroom",
            "boardroom",
            "events[1].room: unknown room 'boardroom'; bookable rooms: meeting-south",
            id="unknown room",
        ),
        pytest.param(
            '  eas:\n    - {at: "07:00", scene: empty, people: 0}\n',
            "",
            "zones:",
            "zone eas is missing; list every zone",
            id="missing zone",
        ),
        pytest.param(
            '  eas:\n    - {at: "07:00"',
            '  eas:\n    - {at: "07:15"',
            '{at: "07:15"',
            "zone eas needs an entry at the start, 07:00",
            id="no entry at the start",
        ),
        pytest.param(
            '{at: "08:30", scene: many',
            '{at: "07:00", scene: many',
            '{at: "07:00", scene: many',
            "07:00 is not after the zone's previous entry",
            id="times not increasing",
        ),
        pytest.param(
            '{at: "08:30", scene: many',
            '{at: "19:15", scene: many',
            "19:15",
            "19:15 is outside the scenario's 07:00-19:00",
            id="outside start-end",
        ),
        pytest.param(
            '{at: "08:30", scene: many',
            '{at: "08:40", scene: many',
            "08:40",
            "08:40 is not on the 15-minute step grid (the world ticks every 900 s)",
            id="off grid",
        ),
        pytest.param(
            'from: "10:00", to: "10:45"',
            'from: "10:45", to: "10:00"',
            "door_open",
            "from 10:45 is not before to 10:00",
            id="from not before to",
        ),
        pytest.param(
            "  - {type: booking",
            '  - {type: door_open, zone: wes, from: "10:30", to: "11:00"}\n  - {type: booking',
            '"10:30"',
            "door_open 10:30-11:00 overlaps 10:00-10:45 in wes",
            id="overlapping doors",
        ),
        pytest.param(
            "door_open, zone: wes",
            "door_open, zone: cor",
            "door_open",
            "zone cor has no door contact in the office_bms point map",
            id="door without a contact",
        ),
        pytest.param(
            'booked_at: "13:45"',
            'booked_at: "14:15"',
            "booked_at",
            "booked_at 14:15 is after the booking's from 14:00",
            id="booked after the start",
        ),
        pytest.param(
            'end: "19:00"', 'end: "07:00"', "end:", "end 07:00 is not after start 07:00", id="end"
        ),
    ],
)
def test_each_error_is_reported_at_its_line(
    write_scenario: WriteScenario, base_yaml: str, old: str, new: str, needle: str, message: str
) -> None:
    text = edit(base_yaml, old, new)
    findings = lint_text(write_scenario, text)
    expected = (line_of(text, needle), "error", message)
    assert expected in [(f.line, f.level, f.message) for f in findings]


@pytest.mark.parametrize(
    ("old", "new", "needle", "message"),
    [
        pytest.param("day: 199", "day: 199: 200", "day:", "invalid YAML: mapping values are not "
                     "allowed here", id="bad yaml"),
        pytest.param("day: 199\n", "day: 199\nday: 200\n", "day: 200", "duplicate key 'day'",
                     id="duplicate key"),
        pytest.param("scene: many, people: 4}", "scene: many}", "scene: many",
                     "zones.cor[1].people: Field required", id="missing field"),
        pytest.param("people: 4", "people: -1", "people: -1",
                     "zones.cor[1].people: Input should be greater than or equal to 0", id="range"),
        pytest.param("scene: many", "scene: crowd", "crowd", "zones.cor[1].scene: Input should "
                     "be 'empty', 'few', 'many', 'meeting', 'complaint' or 'door_open'", id="scene"),
        pytest.param("type: door_open", "type: party", "party", "events[0]: Input tag 'party' "
                     "found using 'type' does not match any of the expected tags: 'door_open', "
                     "'booking', 'complaint', 'clothing'", id="event type"),
        pytest.param('to: "10:45"}', 'to: "10:45", colour: red}', "colour",
                     "events[0].colour: Extra inputs are not permitted", id="unknown field"),
        pytest.param(', to: "15:00"', "", "type: booking", "events[1].to: Field required",
                     id="missing booking field"),
        pytest.param('at: "14:15"', 'at: "2:15pm"', "2:15pm",
                     "events[2].at: String should match pattern '^([01]\\d|2[0-3]):[0-5]\\d$'",
                     id="time format"),
        pytest.param('text: "Too cold in here."', f'text: "{"x" * 281}"', "type: complaint",
                     "events[2].text: String should have at most 280 characters",
                     id="complaint too long for the agent"),
        pytest.param("channel: chat", "channel: voice, audio: ../secret.wav", "secret",
                     "events[2].audio: String should match pattern "
                     "'^[A-Za-z0-9][A-Za-z0-9_.-]*\\.wav$'", id="audio outside footage"),
    ],
)  # fmt: skip
def test_file_problems_are_errors_at_their_line(
    write_scenario: WriteScenario, base_yaml: str, old: str, new: str, needle: str, message: str
) -> None:
    text = edit(base_yaml, old, new)
    findings = lint_text(write_scenario, text)
    assert [(f.line, f.level, f.message) for f in findings] == [
        (line_of(text, needle), "error", message)
    ]


@pytest.mark.parametrize("text", ["", "- a list\n"])
def test_a_file_that_is_not_a_mapping_is_an_error_at_line_1(
    write_scenario: WriteScenario, text: str
) -> None:
    findings = lint_text(write_scenario, text)
    message = "Input should be a valid dictionary or instance of Scenario"
    assert [(f.line, f.level, f.message) for f in findings] == [(1, "error", message)]


def test_an_unquoted_time_is_text_not_a_base_60_number(
    write_scenario: WriteScenario, base_yaml: str
) -> None:
    # YAML 1.1 reads an unquoted 10:00 as 600; the loader keeps it as written.
    text = edit(base_yaml, 'from: "10:00", to: "10:45"', "from: 10:00, to: 10:45")
    path = write_scenario(text)
    assert lint(path, step_s=STEP_S) == []
    door = load_scenario(path).scenario.events[0]
    assert isinstance(door, DoorOpenEvent)
    assert (door.from_, door.to) == ("10:00", "10:45")


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("scene: few, people: 1", "scene: few, people: 4", "scene few needs 1-3 people, not 4"),
        ("scene: many, people: 4", "scene: many, people: 2", "scene many needs 4+ people, not 2"),
        (
            "scene: meeting, people: 5",
            "scene: meeting, people: 1",
            "scene meeting needs 2+ people, not 1",
        ),
        (
            "scene: few, people: 1",
            "scene: complaint, people: 0",
            "scene complaint needs 1+ people, not 0",
        ),
        (
            '"07:00", scene: empty, people: 0}\n  sou',
            '"07:00", scene: empty, people: 2}\n  sou',
            "scene empty needs 0 people, not 2",
        ),
    ],
)
def test_people_that_do_not_fit_the_scene_are_a_warning(
    write_scenario: WriteScenario, base_yaml: str, old: str, new: str, message: str
) -> None:
    text = edit(base_yaml, old, new)
    needle = new.splitlines()[0]
    findings = lint_text(write_scenario, text)
    assert [(f.line, f.level, f.message) for f in findings] == [
        (line_of(text, needle), "warning", message)
    ]


def test_a_door_open_scene_takes_any_count(write_scenario: WriteScenario, base_yaml: str) -> None:
    text = edit(base_yaml, "scene: few, people: 1", "scene: door_open, people: 0")
    assert lint_text(write_scenario, text) == []


def test_voice_cues_that_fall_back_to_chat_are_warnings(
    write_scenario: WriteScenario, base_yaml: str, tmp_path: Path
) -> None:
    no_audio = edit(base_yaml, "channel: chat", "channel: voice")
    findings = lint_text(write_scenario, no_audio)
    message = "voice cue has no audio file; it falls back to chat"
    assert [(f.line, f.level, f.message) for f in findings] == [
        (line_of(no_audio, "channel: voice"), "warning", message)
    ]

    text = edit(base_yaml, "channel: chat", "channel: voice,\n     audio: south_complaint.wav")
    line = line_of(text, "audio:")
    unset = "footage_dir is not set (SPOTCOOL_WORLD_FOOTAGE_DIR); the cue falls back to chat"
    assert [(f.line, f.message) for f in lint_text(write_scenario, text)] == [(line, unset)]

    footage = tmp_path / "footage"
    wav = footage / "audio" / "south_complaint.wav"
    findings = lint_text(write_scenario, text, footage_dir=footage)
    assert [(f.line, f.message) for f in findings] == [
        (line, f"{wav} is missing; the cue falls back to chat")
    ]

    wav.parent.mkdir(parents=True)
    wav.write_bytes(b"RIFF")  # the linter checks presence only; the worker checks the format
    assert lint_text(write_scenario, text, footage_dir=footage) == []


def test_missing_clips_are_a_warning_once_at_their_first_use(
    write_scenario: WriteScenario, base_yaml: str, tmp_path: Path
) -> None:
    clips = tmp_path / "clips"
    clips.mkdir()
    for name in WP8_CLIPS:
        (clips / f"{name}.mp4").touch()
    (clips / "cor_many.mp4").unlink()
    text = edit(base_yaml, "  nor:\n", '    - {at: "09:00", scene: many, people: 4}\n  nor:\n')
    text = edit(text, '"08:30", scene: many', '"08:45", scene: many')
    findings = lint_text(write_scenario, text, clips_dir=clips)
    assert [(f.line, f.level, f.message) for f in findings] == [
        (line_of(text, '"08:45", scene: many'), "warning", f"clip cor_many.mp4 is not in {clips}")
    ]


def test_findings_print_as_file_line_level_message(
    write_scenario: WriteScenario, base_yaml: str
) -> None:
    path = write_scenario(edit(base_yaml, "people: 4", "people: 2"))
    [finding] = lint(path, step_s=STEP_S)
    assert str(finding) == f"{path}:9: warning: scene many needs 4+ people, not 2"


def test_the_step_decides_the_grid(write_scenario: WriteScenario, base_yaml: str) -> None:
    path = write_scenario(edit(base_yaml, '"08:30", scene: many', '"08:35", scene: many'))
    assert lint(path, step_s=300.0) == []
    assert [f.level for f in lint(path, step_s=STEP_S)] == ["error"]


def test_findings_come_in_line_order(write_scenario: WriteScenario, base_yaml: str) -> None:
    text = edit(base_yaml, 'booked_at: "13:45"', 'booked_at: "14:15"')
    text = edit(text, "people: 4", "people: 2")
    lines = [f.line for f in lint_text(write_scenario, text)]
    assert lines == sorted(lines) and len(lines) == 2


def test_unknown_names_and_missing_files_raise(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown scenario 'monday'"):
        lint("monday", step_s=STEP_S)
    with pytest.raises(FileNotFoundError):
        lint(tmp_path / "nowhere.yaml", step_s=STEP_S)


def test_the_library_has_no_errors() -> None:
    assert library_names() == ["all_hands_day", "empty_friday", "hybrid_tuesday"]
    for name in library_names():
        findings = lint(name, step_s=STEP_S)
        assert [f.level for f in findings if f.level == "error"] == [], name
    [warning] = lint("hybrid_tuesday", step_s=STEP_S)
    assert "footage_dir is not set" in warning.message


def test_the_library_matches_wp8_footage(tmp_path: Path) -> None:
    clips = tmp_path / "clips"
    clips.mkdir()
    for name in WP8_CLIPS:
        (clips / f"{name}.mp4").touch()
    audio = tmp_path / "audio" / "south_complaint.wav"
    audio.parent.mkdir()
    audio.touch()
    found = {
        name: [f.message for f in lint(name, step_s=STEP_S, footage_dir=tmp_path, clips_dir=clips)]
        for name in library_names()
    }
    # WP8 filmed no occupied East clip; WP4 decides the fallback.
    assert found == {
        "all_hands_day": [f"clip eas_few.mp4 is not in {clips}"],
        "empty_friday": [],
        "hybrid_tuesday": [],
    }
