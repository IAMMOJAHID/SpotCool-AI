import captions
import qa


def srt(tmp_path, shown):
    path = tmp_path / "cut.srt"
    path.write_text(captions.to_srt(shown), "utf-8")
    return path


NARRATION = {"intro": "A short line.", "outro": "Another short line."}


def test_captions_in_step_pass(tiny_board, tmp_path):
    shown = [
        captions.Caption(0.4, 1.2, ("A short line.",)),
        captions.Caption(2.4, 3.5, ("Another short line.",)),
    ]
    report = qa.Report()
    qa.check_captions(report, srt(tmp_path, shown), tiny_board(), NARRATION)
    assert report.failed == [] and report.passed


def test_captions_out_of_step_fail(tiny_board, tmp_path):
    shown = [
        captions.Caption(0.4, 2.5, ("A short line.",)),  # runs into the next beat
        captions.Caption(2.4, 3.5, ("Another line.", "x" * 43, "three")),
    ]
    report = qa.Report()
    qa.check_captions(report, srt(tmp_path, shown), tiny_board(), NARRATION)
    (failure,) = report.failed
    assert "overlaps" in failure and "not inside a narrated beat" in failure
    assert (
        "more than 2 lines or 42 characters" in failure
        and "outro do not say its narration" in failure
    )


def test_pending_lists_what_needs_the_user():
    record = {
        "narration": {"kind": "placeholder"},
        "beats": [
            {
                "id": "dashboard",
                "kind": "slate",
                "missing": "TAKE MISSING: dashboard",
                "how": "take.py x",
            }
        ],
        "music": "C:/music/calm.mp3",
    }
    assert qa.pending_items(record, "") == [
        "narration: the TTS placeholder; record video/raw/narration.wav (video/README.md, step 3)",
        "dashboard: TAKE MISSING: dashboard; take.py x",
        "music: add a CREDITS.md row naming calm.mp3",
    ]
    done = {"narration": {"kind": "recorded"}, "beats": [], "music": "C:/music/calm.mp3"}
    assert qa.pending_items(done, "| calm.mp3 | Pixabay |") == []


def test_qa_needs_a_built_cut(tmp_path, capsys):
    assert qa.main([str(tmp_path / "none.mp4")]) == 1
    assert "assemble.py" in capsys.readouterr().err


def test_the_stream_check_reads_ffprobe(fake_run, tiny_board):
    probe = '{"format": {"duration": "4.000"}, "streams": [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "width": 1280, "height": 720, "r_frame_rate": "25/1"}, {"codec_type": "audio", "codec_name": "aac"}]}'
    fake_run.answers.append(lambda args: (probe, "") if args[0] == "ffprobe" else None)
    report = qa.Report()
    qa.check_stream(report, qa.Path("cut.mp4"), tiny_board())
    assert report.failed == ["frame 1280x720 is 1920x1080", "frame rate 25/1 is 30/1"]
