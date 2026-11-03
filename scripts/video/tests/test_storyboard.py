import pytest
from storyboard import (
    load_narration,
    load_sources,
    load_storyboard,
    narrated,
    never_quoted,
    numbers,
    spoken_words,
)


def test_numbers_are_standalone_figures_not_identifiers():
    assert numbers("17.8 % against 5.9 %") == ["17.8", "5.9"]
    assert numbers("priority 8, and 10.") == ["8", "10"]
    assert numbers("Source: PNNL-22072, run eval-live-hybrid-20261103-122941, CO2") == []


@pytest.mark.parametrize(
    "text, found",
    [
        ("-21.14 %", ["-21.14"]),
        ("−27 % at 25 degC", ["-27", "25"]),
        ("+3.5 %", ["+3.5"]),
        ("17-18 %", ["17", "18"]),
        ("17–18 %", ["17", "18"]),
        ("a 15-minute step in a 10-city average", ["15", "10"]),
        ("07:00 to 19:00", ["07", "00", "19", "00"]),
    ],
)
def test_numbers_keep_signs_and_both_ends_of_a_range(text, found):
    assert numbers(text) == found


@pytest.mark.parametrize(
    "identifier",
    [
        "eval-live-hybrid-20261103-122941",
        "WP1.2",
        "ZN-1",
        "h264",
        "1920x1080",
        "ANALOG_VALUE:201",
        "multizone_office_simple_air",
        "BSD-3-Clause",
    ],
)
def test_digits_in_an_identifier_are_not_numbers(identifier):
    assert numbers(f"see {identifier}.") == []


def test_never_quoted_catches_a_signed_figure_and_up_to_60():
    assert never_quoted("It saved -21.14 %.") == [
        "-21.14 is superseded (the 2 Nov checkpoint and wp12-live-drop): never quote it"
    ]
    assert "no run of ours supports" in never_quoted("Up to 60 % saved")[0]
    assert never_quoted("It saved 19.94 %, in 60 steps.") == []


def test_spoken_words_spell_numbers_out():
    assert spoken_words("19.94 %") == 5  # nineteen point nine four percent
    assert spoken_words("230.4") == 5  # two hundred thirty point four
    assert spoken_words("priority 8") == 2
    assert spoken_words("Kastle's US badge-data") == 3


def test_narration_sections_skip_the_preamble_and_comments(tmp_path):
    path = tmp_path / "narration.md"
    path.write_text(
        "# Narration\n\nRead slowly.\n\n## a\n\nFirst line.\nSecond line.\n<!-- a note -->\n\n## b\nB.\n",
        "utf-8",
    )
    assert load_narration(path) == {"a": "First line. Second line.", "b": "B."}


@pytest.mark.parametrize(
    "text, message",
    [("## a\n\n## b\nB.\n", "empty"), ("## a\nA.\n## a\nA again.\n", "twice")],
)
def test_narration_rejects_empty_and_repeated_sections(tmp_path, text, message):
    path = tmp_path / "narration.md"
    path.write_text(text, "utf-8")
    with pytest.raises(ValueError, match=message):
        load_narration(path)


def test_a_beat_shows_exactly_one_source(tiny_board):
    with pytest.raises(ValueError, match="exactly one"):
        tiny_board(**{"source: {take: outro_take,": "source: {take: outro_take, clip: x.mp4,"})


def test_the_tiny_board_loads(tiny_board):
    board = tiny_board()
    assert board.total_s == 4
    assert board.take_beat("intro_take").id == "intro"
    assert board.beat("outro").source.scenes[1].scene == "Dashboard"
    assert [beat.id for beat in narrated(board)] == ["intro", "outro"]


def test_the_video_sources_load():
    board, narration, citations = load_storyboard(), load_narration(), load_sources()
    assert board.total_s <= board.max_s
    assert all(beat.narration_id in narration for beat in narrated(board))
    assert all(source in citations for beat in board.beats for source in beat.sources)
