import hashlib
import json

import assemble
import captions
import media
import pytest
import qa


@pytest.fixture
def takes(monkeypatch, tmp_path):
    """Takes live in tmp_path/takes; returns that directory."""
    folder = tmp_path / "takes"
    folder.mkdir()
    monkeypatch.setattr(assemble, "find_take", lambda take: next(folder.glob(f"{take}.*"), None))
    monkeypatch.setattr(assemble, "WORK", tmp_path / "work")
    return folder


def test_a_missing_take_becomes_a_labelled_slate(tiny_board, takes):
    (takes / "outro_take.mkv").write_bytes(b"")
    intro, outro = assemble.resolve(tiny_board())
    assert (intro.kind, intro.missing) == ("slate", "TAKE MISSING: intro")
    assert intro.how == "uv run python scripts/video/take.py intro_take"
    assert (outro.kind, outro.path) == ("take", takes / "outro_take.mkv")


def test_one_take_feeds_two_beats_each_from_its_own_in_s(tiny_board, takes):
    (takes / "intro_take.mkv").write_bytes(b"")
    board = tiny_board(**{"take: outro_take,": "take: intro_take, in_s: 30,"})
    intro, outro = assemble.resolve(board)
    assert intro.path == outro.path == takes / "intro_take.mkv"
    assert (intro.in_s, outro.in_s) == (1, 30)
    args, _ = assemble.video_inputs(board, [intro, outro])
    assert args[:2] == ["-ss", "1"] and args[6:8] == ["-ss", "30"]


def test_a_beat_can_cover_part_of_its_take(tiny_board, takes):
    (takes / "intro_take.mkv").write_bytes(b"")
    board = tiny_board(
        **{
            "take: outro_take,": "take: intro_take, cover: [{x: 1, y: 2, w: 3, h: 4, color: '0x040809'}],"
        }
    )
    _, chains = assemble.video_inputs(board, assemble.resolve(board))
    assert "drawbox=x=1:y=2:w=3:h=4:color=0x040809:t=fill," in chains[1]
    assert "drawbox" not in chains[0]


def test_the_overlay_has_slates_labels_and_captions(tiny_board, takes):
    board = tiny_board()
    (takes / "outro_take.mkv").write_bytes(b"")
    visuals = assemble.resolve(board)
    shown = [
        captions.Caption(0.4, 1.7, ("A short", "line.")),
        captions.Caption(2.4, 3.7, ("Another {one}",)),
    ]
    ass = assemble.overlay(board, visuals, shown)
    assert "PlayResX: 1920" in ass and "PlayResY: 1080" in ass
    assert (
        "Dialogue: 0,0:00:00.00,0:00:02.00,Slate,,0,0,0,,{\\pos(960,440)}TAKE MISSING: intro" in ass
    )
    assert "A label at priority 8\\NMake it: uv run python scripts/video/take.py intro_take" in ass
    assert "Dialogue: 0,0:00:00.40,0:00:01.70,Caption,,0,0,0,,A short\\Nline." in ass
    assert "Another (one)" in ass  # braces would start an ASS override


def test_each_beat_gets_exactly_its_frames(tiny_board, takes):
    (takes / "intro_take.mp4").write_bytes(b"")
    board = tiny_board()
    args, chains = assemble.video_inputs(board, assemble.resolve(board))
    assert args[:6] == ["-ss", "1", "-t", "3", "-i", str(takes / "intro_take.mp4")]
    assert "tpad=stop_mode=clone:stop_duration=2,trim=end_frame=60" in chains[0]
    assert (
        args[6:10] == ["-f", "lavfi", "-t", "2"] and "color=c=0x0e1116:s=1920x1080:r=30" in args[11]
    )
    assert chains[1].endswith("trim=end_frame=60,setpts=PTS-STARTPTS[v1]")


def test_final_refuses_slates_and_the_placeholder(tiny_board, takes, tmp_path):
    narration = tmp_path / "narration_placeholder.wav"
    narration.write_bytes(b"")
    with pytest.raises(assemble.BuildError, match="TAKE MISSING: intro; TAKE MISSING: outro"):
        assemble.build(
            narration, final=True, board=tiny_board(), narration={"intro": "A.", "outro": "B."}
        )


def test_no_narration_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(assemble, "RECORDED_NARRATION", tmp_path / "a.wav")
    monkeypatch.setattr(assemble, "PLACEHOLDER_NARRATION", tmp_path / "b.wav")
    with pytest.raises(assemble.BuildError, match="tts_placeholder"):
        assemble.pick_narration(None)
    (tmp_path / "b.wav").write_bytes(b"the placeholder")
    assert assemble.pick_narration(None) == (tmp_path / "b.wav", "placeholder")
    (tmp_path / "a.wav").write_bytes(b"a real voice")
    assert assemble.pick_narration(None) == (tmp_path / "a.wav", "recorded")


def test_a_copy_of_the_placeholder_is_still_the_placeholder(monkeypatch, tmp_path):
    monkeypatch.setattr(assemble, "RECORDED_NARRATION", tmp_path / "raw" / "narration.wav")
    monkeypatch.setattr(assemble, "PLACEHOLDER_NARRATION", tmp_path / "placeholder.wav")
    (tmp_path / "placeholder.wav").write_bytes(b"the placeholder")
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "narration.wav").write_bytes(b"the placeholder")
    assert assemble.pick_narration(None)[1] == "placeholder"
    copy = tmp_path / "elsewhere.wav"
    copy.write_bytes(b"the placeholder")
    assert assemble.pick_narration(copy) == (copy, "placeholder")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_tiny_cut_builds_to_spec_and_reproduces(ffmpeg, tiny_board, takes, tmp_path):
    board = tiny_board()
    narration = {"intro": "A short line.", "outro": "Another short line."}
    take = takes / "outro_take.mp4"
    media.ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=1280x720:rate=25:duration=2",
        "-c:v",
        "libx264",
        str(take),
    )
    recording = tmp_path / "narration.wav"
    tones = "[0:a]atrim=0:1[a];[1:a]atrim=0:2[gap];[2:a]atrim=0:1.2[b];[a][gap][b]concat=n=3:v=0:a=1[out]"
    media.ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
        "-f", "lavfi", "-i", "sine=frequency=660:duration=1.2", "-filter_complex", tones, "-map", "[out]",
        str(recording),
    )  # fmt: skip
    music = tmp_path / "music.wav"
    media.ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:duration=1.5", str(music))  # looped under
    output = tmp_path / "cut.mp4"
    record = assemble.build(recording, music, output, board=board, narration=narration)
    first = _sha(output)
    assemble.build(recording, music, output, board=board, narration=narration)
    assert _sha(output) == first  # bit-exact: the same inputs give the same file

    report = qa.Report()
    qa.check_stream(report, output, board)
    qa.check_loudness(report, output)
    qa.check_captions(report, output.with_suffix(".srt"), board, narration)
    assert report.failed == [], report.failed
    assert [b["kind"] for b in record["beats"]] == ["slate", "take"]
    saved = json.loads(output.with_suffix(".build.json").read_text("utf-8"))
    assert saved["narration"]["kind"] == "recorded" and saved["music"].endswith("music.wav")
    assert qa.pending_items(saved, "") == [
        "intro: TAKE MISSING: intro; uv run python scripts/video/take.py intro_take",
        "music: add a CREDITS.md row naming music.wav",
    ]
