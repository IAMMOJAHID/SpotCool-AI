import captions
import pytest
import voice
from storyboard import load_narration, load_storyboard


def test_wrap_balances_lines_and_keeps_a_number_with_its_unit():
    lines = captions.wrap("Yet buildings still cool on a fixed schedule.")
    assert lines == ["Yet buildings still cool", "on a fixed schedule."]
    for width in range(14, 30):
        for line in captions.wrap("against 5.9 % for presence sensors, against 5.9 % again", width):
            assert not line.endswith("5.9") and not line.startswith("%")


def test_a_long_sentence_is_cut_at_a_clause_into_even_captions():
    sentence = "As a person walks across the room, the agent aims the VRF unit's louvers at them, and the louvers turn smoothly to follow."
    pieces = captions.parts(sentence)
    assert pieces == [
        "As a person walks across the room, the agent aims the VRF unit's louvers at them,",
        "and the louvers turn smoothly to follow.",
    ]


def test_chunk_keeps_short_sentences_together_and_never_exceeds_the_limits():
    assert captions.chunk("SpotCool. Cooling that follows people.") == [
        ("SpotCool.", "Cooling that follows people.")
    ]
    for paragraph in load_narration().values():
        groups = captions.chunk(paragraph)
        assert all(
            len(g) <= captions.MAX_LINES and all(len(line) <= captions.MAX_CHARS for line in g)
            for g in groups
        )
        assert " ".join(line for g in groups for line in g).split() == paragraph.split()


def test_build_spreads_a_paragraph_over_its_time_on_air(tiny_board):
    board = tiny_board()
    placement = voice.Placement("intro", "intro", 0.4, 0.0, 1.5, 1.5)
    shown = captions.build(
        board,
        {
            "intro": "One two. Three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen."
        },
        [placement],
    )
    assert shown[0].start == 0.4 and shown[-1].end == pytest.approx(1.9)
    assert all(a.end == b.start for a, b in zip(shown, shown[1:], strict=False))


def test_srt_round_trips():
    shown = [
        captions.Caption(0.4, 2.25, ("First line", "second line")),
        captions.Caption(2.25, 3661.5, ("Later",)),
    ]
    text = captions.to_srt(shown)
    assert "00:00:00,400 --> 00:00:02,250" in text and "01:00:00,000" not in text
    assert "01:01:01,500" in text
    assert [(c.start, c.end, c.lines) for c in captions.parse_srt(text)] == [
        (c.start, c.end, c.lines) for c in shown
    ]


@pytest.mark.parametrize(
    "bad", ["2\n00:00:01,000 --> 00:00:02,000\nText\n", "1\n00:00:01 --> 00:00:02\nText\n"]
)
def test_parse_srt_rejects_a_bad_block(bad):
    with pytest.raises(ValueError):
        captions.parse_srt(bad)


def test_captions_cli_estimates_without_a_recording(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(captions, "RECORDED_NARRATION", tmp_path / "none.wav")
    monkeypatch.setattr(captions, "PLACEHOLDER_NARRATION", tmp_path / "none-either.wav")
    out = tmp_path / "draft.srt"
    assert captions.main(["--out", str(out)]) == 0
    assert "estimated at 140 words a minute" in capsys.readouterr().out
    shown = captions.parse_srt(out.read_text("utf-8"))
    board = load_storyboard()
    assert shown[0].start == pytest.approx(board.beat("intro").start + voice.LEAD_S)
    assert shown[-1].end <= board.total_s
