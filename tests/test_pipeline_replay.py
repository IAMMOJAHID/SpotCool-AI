"""End to end on the demo-beat cassettes: neutral -> cold complaint -> door open -> door closed."""

import asyncio
from pathlib import Path

import numpy as np

from app.spotcool_agent.config import AppConfig
from app.spotcool_agent.memory import EpisodeStore
from app.spotcool_agent.meta_client import MockBackend, ReplayBackend
from app.spotcool_agent.pipeline import TurnContext, run_turn
from app.spotcool_agent.preprocess import TurnInput

CASSETTE = Path(__file__).parent / "fixtures" / "cassettes" / "demo_beats"
FREEZING = "It's freezing in this meeting room."


def synthetic_turn(label: str, texts=(), seconds: int = 25) -> TurnInput:
    rng = np.random.default_rng(len(label))
    frames = [rng.integers(0, 255, (360, 640, 3), dtype=np.uint8) for _ in range(seconds)]
    return TurnInput(label=label, frames=frames, video_path=Path("unused.mp4"), duration_s=float(seconds), texts=list(texts))


def replay_context(tmp_path, name: str = "demo") -> TurnContext:
    cfg = AppConfig()
    cfg.settings.mode = "replay"
    return TurnContext(cfg=cfg, backend=ReplayBackend(CASSETTE), store=EpisodeStore(tmp_path, name))


def run(ctx: TurnContext, turn: int, turn_input: TurnInput):
    return asyncio.run(run_turn(turn_input, turn, ctx))


def test_demo_beats(tmp_path):
    ctx = replay_context(tmp_path)

    neutral = run(ctx, 1, synthetic_turn("neutral"))
    assert neutral.proposal_source == "llm" and neutral.reward.trend == "baseline"
    assert [o.occupant_key for o in neutral.state.occupants] == ["occ-1"]
    assert (neutral.vrf_command.idu_vane_h, neutral.vrf_command.idu_vane_v, neutral.vrf_command.idu_sp) == ("Pos3", "Pos3", 24.0)

    cold = run(ctx, 2, synthetic_turn("complaint", texts=[FREEZING]))
    occupant = cold.state.occupants[0]
    assert occupant.occupant_key == "occ-1" and occupant.draft and occupant.observed_sensation < -1.5
    assert cold.state.complaints[0].attributed_to == ["occ-1"]
    assert (cold.vrf_command.idu_sp, cold.vrf_command.idu_fan) == (25.0, "Low")
    assert (cold.vrf_command.idu_vane_h, cold.vrf_command.idu_vane_v) == ("Pos2", "Pos1")
    assert cold.reward.source == "llm" and cold.reward.grounded.complaint_penalty == 0.3
    assert cold.state_after.occupants["occ-1"].draft_sensitive

    door = run(ctx, 3, synthetic_turn("door"))
    assert door.state.door.spark_confirmed and door.state.door.effective_open_s == 23.0
    assert door.applied.compressor_mode == "fan_only" and door.vrf_command.idu_mode == "Fan"
    assert not door.state.occupants[0].draft  # the jet now points away from them

    closed = run(ctx, 4, synthetic_turn("closed"))
    assert not closed.state.door.override_active
    assert closed.applied.compressor_mode == "fan_only"
    lockout = next(a for a in closed.adjustments if a.field == "compressor_mode")
    assert any("restart lockout" in reason for reason in lockout.reasons)

    names = {p.name for p in ctx.store.dir.iterdir()}
    assert {f"turn_00{t}{suffix}" for t in range(1, 5) for suffix in (".json", "_grid.jpg", "_hud.jpg")} <= names
    assert [r.turn for r in ctx.store.load().turns] == [1, 2, 3, 4]


def test_rerunning_a_turn_reproduces_it_and_drops_later_turns(tmp_path):
    ctx = replay_context(tmp_path)
    run(ctx, 1, synthetic_turn("neutral"))
    first = run(ctx, 2, synthetic_turn("complaint", texts=[FREEZING]))
    run(ctx, 3, synthetic_turn("door"))
    again = run(ctx, 2, synthetic_turn("complaint", texts=[FREEZING]))
    assert again.applied == first.applied and again.vrf_command == first.vrf_command
    assert again.state_after.model_dump(exclude={"occupants"}) == first.state_after.model_dump(exclude={"occupants"})
    assert [r.turn for r in ctx.store.load().turns] == [1, 2]


def test_missing_cassette_degrades_instead_of_failing(tmp_path):
    ctx = replay_context(tmp_path)
    record = run(ctx, 9, synthetic_turn("nothing recorded"))
    assert record.proposal_source == "fallback" and record.reward.source == "grounded"
    assert any(item.startswith("sam person") for item in record.degraded)


def test_mock_backend_runs_without_network(tmp_path):
    cfg = AppConfig()
    cfg.settings.mode = "mock"
    ctx = TurnContext(cfg=cfg, backend=MockBackend(), store=EpisodeStore(tmp_path, "mock"), previews=False)
    record = run(ctx, 1, synthetic_turn("mock"))
    assert record.proposal_source == "fallback" and record.reward.source == "grounded"
    assert record.vrf_command.idu_sp % 0.5 == 0 and len(record.state.occupants) == 1
    assert not any(ctx.store.dir.glob("*.jpg"))
