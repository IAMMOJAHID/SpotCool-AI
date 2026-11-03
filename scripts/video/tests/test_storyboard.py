import pytest
from storyboard import (
    load_narration,
    load_sources,
    load_storyboard,
    narrated,
    numbers,
    spoken_words,
)


def test_numbers_are_standalone_figures_not_identifiers():
    assert numbers("Up to 17.8 % against 5.9 %") == ["17.8", "5.9"]
    assert numbers("priority 8, and 10.") == ["8", "10"]
    assert numbers("Source: PNNL-22072, run eval-live-hybrid-20261103-122941, CO2") == []


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
