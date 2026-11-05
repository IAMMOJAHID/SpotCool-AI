import captions
import qa
import results_card
from results_card import ResultsCard
from storyboard import Beat, Citation, Source


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


STUB_SAID = (
    "With perfect perception, from a stub agent, SpotCool's policy used 19.94 % less HVAC "
    "energy than the baseline. Discomfort where people were rose from 0.33 to 2.13 kelvin hours."
)
LIVE = Citation(
    "run-eval-live",
    "the live eval",
    "docs/release/numbers.md",
    ("19.94 %", "230.4", "287.8", "2.13", "0.33"),
    "our run",
    run_id="eval-live-hybrid-20261103-122941",
)


def results_beat(*sources: str) -> Beat:
    source = Source(composition="results_card", run_id="eval-live-hybrid-20261103-122941")
    return Beat("results", 0, 27, "motion", source, "results", {}, sources)


def card(run_id: str = LIVE.run_id, stub: bool = True, figures=LIVE.figures) -> ResultsCard:
    label = results_card.STUB_LABEL if stub else results_card.QUOTABLE_LABEL
    return ResultsCard(run_id, stub, label, {"label": label}, tuple(figures), ())


def test_the_live_eval_card_agrees_with_its_beat():
    assert qa.card_problems(card(), results_beat("run-eval-live"), STUB_SAID, {LIVE.id: LIVE}) == []


def test_the_wp12_live_drop_card_is_refused():
    # wp12-live-drop measured 21.14 % with the earlier stub: never quoted, even on a card.
    drop = Citation("drop", "wp12-live-drop", "x", ("21.14 %",), "old", run_id="wp12-live-drop")
    said = STUB_SAID.replace("19.94", "21.14")
    problems = qa.card_problems(
        card("wp12-live-drop", figures=("21.14 %",)), results_beat("drop"), said, {"drop": drop}
    )
    assert any("card figure 21.14 %: 21.14 is superseded" in p for p in problems)


def test_a_switch_to_a_quotable_run_needs_its_source_and_new_narration():
    # the agent's run replaces the stub run on the card, but the beat and narration still don't.
    quotable = card(
        "eval-agent-quotable", stub=False, figures=("12.30 %", "250.0", "285.0", "0.50", "0.33")
    )
    problems = qa.card_problems(quotable, results_beat("run-eval-live"), STUB_SAID, {LIVE.id: LIVE})
    assert "the results beat cites no source for the card's run eval-agent-quotable" in problems
    assert "the results narration says 19.94, 2.13, which the card does not" in problems
    assert "the results narration does not say the card's saving, 12.30 %" in problems
    assert "the card is a quotable run's, but the results narration says stub" in problems


def test_a_stub_card_needs_the_narration_to_say_stub():
    said = STUB_SAID.replace("from a stub agent, ", "")
    problems = qa.card_problems(card(), results_beat("run-eval-live"), said, {LIVE.id: LIVE})
    assert problems == ["the card is a stub run's, but the results narration never says so"]
