import json
from dataclasses import replace

import pytest
import render_motion
from results_card import ResultsCard
from storyboard import load_sources, load_storyboard

CARD = ResultsCard(
    run_id="eval-live-hybrid-20261103-122941",
    stub=True,
    label="Upper bound",
    variables={"label": "Upper bound", "saved": "19.94 %"},
    figures=("19.94 %",),
    verdict=(),
)


def test_a_composition_gets_its_beats_text():
    board = load_storyboard()
    beat = board.beat("hook")
    values = render_motion.variables_for(beat, board, load_sources(), None)
    assert values == beat.on_screen_text


def test_the_results_card_takes_the_generated_card_and_refuses_another_run():
    board, sources = load_storyboard(), load_sources()
    beat = board.beat("results")
    values = render_motion.variables_for(beat, board, sources, CARD)
    assert values["saved"] == "19.94 %" and values["title"] == beat.on_screen_text["title"]
    with pytest.raises(ValueError, match="results_card.py"):
        render_motion.variables_for(beat, board, sources, None)
    with pytest.raises(ValueError, match="another|from run"):
        render_motion.variables_for(beat, board, sources, replace(CARD, run_id="other-run"))


def test_the_close_lists_every_cited_source_once():
    board, sources = load_storyboard(), load_sources()
    text = render_motion.variables_for(board.beat("close"), board, sources, None)["sources_text"]
    lines = text.split("\n")
    cited = {s for beat in board.beats for s in beat.sources}
    assert len(lines) == len(cited) == len(set(lines))
    assert sources["pnnl-22072"].cite in lines


def test_render_runs_the_pinned_cli_with_the_variables_file(fake_run, tmp_path, monkeypatch):
    monkeypatch.setattr(render_motion, "npx", lambda: "npx")
    out = render_motion.render(
        "title", {"title": "SpotCool", "tagline": "x"}, tmp_path / "title.mp4"
    )
    args = fake_run.calls[0]
    assert args[:4] == ["npx", "--yes", render_motion.HYPERFRAMES, "render"]
    assert args[4].endswith("title") and args[args.index("--output") + 1] == str(out)
    for flag in ("--no-browser-gpu", "--strict", "--strict-variables"):
        assert flag in args
    assert args[args.index("--workers") + 1] == render_motion.WORKERS
    values = json.loads((tmp_path / "title.vars.json").read_text("utf-8"))
    assert values == {"title": "SpotCool", "tagline": "x"}
    assert render_motion.HYPERFRAMES == "hyperframes@0.8.6"


def test_render_turns_off_telemetry_and_static_frame_dedup(fake_run, tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(render_motion, "npx", lambda: "npx")
    monkeypatch.setattr(
        render_motion.media, "run", lambda args, cwd=None, env=None: seen.update(env)
    )
    render_motion.render("title", {}, tmp_path / "title.mp4")
    assert seen["HF_STATIC_DEDUP"] == "false" and seen["HYPERFRAMES_NO_TELEMETRY"] == "1"
