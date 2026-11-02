from app.spotcool_agent.memory import EpisodeStore, reidentify
from app.spotcool_agent.schemas import (
    ClipInfo,
    ControllerState,
    ManipulatedVariables,
    OccupantMemory,
    RewardBreakdown,
    RewardEvaluation,
    TurnRecord,
    TurnState,
    VrfCommand,
)


def test_reidentify_matches_by_position_and_forgets_absent_occupants():
    mapping, memories, next_index = reidentify({"0": (0.1, 0.2, 0.2, 0.6), "3": (0.6, 0.2, 0.7, 0.6)}, {}, 1, 1, 0.12, 3)
    assert mapping == {"0": "occ-1", "3": "occ-2"} and next_index == 3
    # The next clip renumbers SAM IDs and people moved a little.
    mapping, memories, next_index = reidentify({"5": (0.61, 0.21, 0.71, 0.61), "9": (0.11, 0.2, 0.21, 0.6)}, memories, 2, next_index, 0.12, 3)
    assert mapping == {"5": "occ-2", "9": "occ-1"}
    for turn in range(3, 7):  # occ-1 is away for four turns
        mapping, memories, next_index = reidentify({"1": (0.6, 0.2, 0.7, 0.6)}, memories, turn, next_index, 0.12, 3)
    assert set(memories) == {"occ-2"}


def test_neighbouring_seats_do_not_swap():
    memories = {
        "occ-1": OccupantMemory(key="occ-1", bbox=(0.40, 0.3, 0.48, 0.7), last_seen_turn=1),
        "occ-2": OccupantMemory(key="occ-2", bbox=(0.50, 0.3, 0.58, 0.7), last_seen_turn=1),
    }
    mapping, _, _ = reidentify({"a": (0.45, 0.3, 0.53, 0.7), "b": (0.53, 0.3, 0.61, 0.7)}, memories, 2, 3, 0.12, 3)
    assert mapping == {"a": "occ-1", "b": "occ-2"}


def record(turn: int, elapsed: float) -> TurnRecord:
    grounded = RewardBreakdown(score=0.0, delta_discomfort=0.0, discomfort=0.0, energy=0.0,
                               complaint_penalty=0.0, matched_occupants=0, occupied=False)  # fmt: skip
    return TurnRecord(
        turn=turn,
        mode="mock",
        state=TurnState(turn=turn, clip=ClipInfo(label="x", duration_s=35.0, frames=35, frame_size=(640, 360))),
        previous_action=ManipulatedVariables(),
        reward=RewardEvaluation(score=0.0, source="grounded", trend="baseline", explanation="", grounded=grounded),
        proposal_source="fallback",
        proposed=ManipulatedVariables(),
        applied=ManipulatedVariables(),
        vrf_command=VrfCommand(profile="mitsubishi", idu_sp=24.0, idu_mode="Cool", idu_fan="Auto"),
        state_before=ControllerState(),
        state_after=ControllerState(elapsed_s=elapsed),
    )


def test_rerun_starts_from_the_previous_state_and_drops_later_turns(tmp_path):
    store = EpisodeStore(tmp_path, "ep")
    episode = store.load()
    for turn in (1, 2, 3):
        store.upsert(episode, record(turn, 35.0 * turn))
    store.save(episode)
    episode = store.load()
    before, previous, warning = store.state_before(episode, 2)
    assert before.elapsed_s == 35.0 and previous is not None and previous.turn == 1 and warning is None
    assert store.upsert(episode, record(2, 70.0)) == [3]
    assert [r.turn for r in episode.turns] == [1, 2] and EpisodeStore.next_turn(episode) == 3
    _, _, warning = store.state_before(episode, 5)
    assert warning is not None and "turn 4 not in history" in warning


def test_reset_removes_only_episode_outputs(tmp_path):
    store = EpisodeStore(tmp_path, "ep")
    store.dir.mkdir(parents=True)
    for name in ("episode_history.json", "turn_001.json", "turn_001_grid.jpg", "notes.txt"):
        (store.dir / name).write_text("x", encoding="utf-8")
    (store.dir / "cassette").mkdir()
    store.reset()
    assert sorted(p.name for p in store.dir.iterdir()) == ["cassette", "notes.txt"]
