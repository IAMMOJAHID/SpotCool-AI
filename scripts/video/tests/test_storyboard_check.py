import storyboard_check
from storyboard import Citation, load_narration, load_sources, load_storyboard

BACNET = Citation("bacnet", "BMS rules", "docs/runbooks/yabe.md", ("8", "10"), "BACnet")
NARRATION = {"intro": "A short line.", "outro": "Another short line."}


def check(board, narration=NARRATION, citations=None):
    return storyboard_check.check(board, narration, citations or {"bacnet": BACNET})


def test_the_video_passes():
    assert storyboard_check.check(load_storyboard(), load_narration(), load_sources()) == []


def test_storyboard_check_cli_passes(capsys):
    assert storyboard_check.main([]) == 0
    assert "storyboard: ok" in capsys.readouterr().out


def test_the_tiny_board_passes(tiny_board):
    assert check(tiny_board()) == []


def test_gaps_overlaps_the_frame_grid_and_the_limit_fail(tiny_board):
    assert "outro: a gap from 2 to 2.5 s" in check(tiny_board(**{"start: 2\n": "start: 2.5\n"}))
    assert any("overlaps" in e for e in check(tiny_board(**{"start: 2\n": "start: 1.5\n"})))
    assert any("frame grid" in e for e in check(tiny_board(**{"end: 4\n": "end: 4.01\n"})))
    assert any("past its" in e for e in check(tiny_board(**{"max_s: 178": "max_s: 3"})))


def test_narration_faster_than_150_words_a_minute_fails(tiny_board):
    fast = {"intro": " ".join(["word"] * 6), "outro": "Fine."}  # six words in two seconds
    assert any("180 words a minute" in e for e in check(tiny_board(), fast))


def test_missing_and_unused_narration_fail(tiny_board):
    errors = check(tiny_board(), {"intro": "Line.", "spare": "Unused."})
    assert any("no section ## outro" in e for e in errors)
    assert any("spare is used by no beat" in e for e in errors)


def test_a_number_needs_a_source_its_beat_cites(tiny_board):
    errors = check(tiny_board(), {"intro": "Priority 8 wins.", "outro": "We saved 19.94 % today."})
    shows = [e for e in errors if " shows " in e]
    assert shows == ["outro: narration shows 19.94, which no source it cites backs"]


def test_a_never_quote_figure_fails_even_with_a_source(tiny_board):
    stale = Citation("old", "old run", "docs/release/numbers.md", ("21.14 %",), "old")
    board = tiny_board(**{"sources: []": "sources: [old]"})
    errors = check(
        board, {"intro": "Line.", "outro": "It saved 21.14 %."}, {"bacnet": BACNET, "old": stale}
    )
    assert any("never quote it" in e for e in errors)
    errors = check(
        board, {"intro": "Line.", "outro": "Up to 60 % saved."}, {"bacnet": BACNET, "old": stale}
    )
    assert any("no run of ours supports" in e for e in errors)


def test_a_source_may_not_back_a_never_quote_figure(tiny_board):
    stale = Citation("drop", "wp12-live-drop", "docs/release/numbers.md", ("21.14 %",), "old run")
    errors = check(tiny_board(), citations={"bacnet": BACNET, "drop": stale})
    assert any(e.startswith("source drop: 21.14 is superseded") for e in errors)


def test_a_results_beat_must_cite_its_runs_source(tiny_board):
    board = tiny_board(**{"take: outro_take,": "take: outro_take, run_id: some-run,"})
    assert "outro: cites no source for its run some-run in video/sources.yaml" in check(board)
    run = Citation("run", "some run", "docs/release/numbers.md", (), "our run", run_id="some-run")
    board = tiny_board(
        **{
            "take: outro_take,": "take: outro_take, run_id: some-run,",
            "sources: []": "sources: [run]",
        }
    )
    assert check(board, citations={"bacnet": BACNET, "run": run}) == []


def test_a_replay_must_play_inside_its_beat(tiny_board, monkeypatch):
    monkeypatch.delenv("SPOTCOOL_WORLD_RUN_DIR", raising=False)  # the span defaults to the day
    replay = "take: outro_take, replay: {run_id: some-run, speed: 1800},"
    errors = check(tiny_board(**{"take: outro_take,": replay}))
    assert any("the replay plays 24.0 s" in e and "raise its speed" in e for e in errors)


def test_a_source_figure_must_be_in_its_doc(tiny_board):
    wrong = Citation("bacnet", "BMS", "docs/runbooks/yabe.md", ("8", "123456"), "BACnet")
    assert "source bacnet: 123456 is not in docs/runbooks/yabe.md" in check(
        tiny_board(), citations={"bacnet": wrong}
    )


def test_an_unknown_scene_or_a_cue_outside_the_beat_fails(tiny_board):
    errors = check(
        tiny_board(**{"scene: YABE": "scene: Nowhere", "{at: 1, scene": "{at: 2, scene"})
    )
    assert any("OBS scene Nowhere" in e for e in errors)
    assert any("at 2 s is outside the beat" in e for e in errors)


def test_a_composition_must_match_its_beat(tiny_board):
    # The title composition lasts 4 s and takes title and tagline.
    board = tiny_board(
        **{
            "type: screen\n    source: {take: intro_take, in_s: 1, scenes: [{at: 0, scene: Dashboard}]}": "type: title\n    source: {composition: title}",
        }
    )
    errors = check(board)
    assert "intro: composition title lasts 4.0 s, its beat 2 s" in errors
    assert "intro: composition title declares no variable label" in errors
    assert "intro: nothing sets composition title's tagline, title" in errors
