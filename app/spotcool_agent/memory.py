import math
import os
import time
from pathlib import Path
from typing import Optional

from .schemas import ControllerState, EpisodeState, OccupantMemory, TurnRecord, utc_now

HISTORY_FILE = "episode_history.json"


class EpisodeStore:
    """runs/<episode>/episode_history.json: every turn with its controller state before and after."""

    def __init__(self, root: Path, episode_id: str) -> None:
        self.episode_id = episode_id
        self.dir = Path(root) / episode_id
        self.path = self.dir / HISTORY_FILE

    def load(self) -> EpisodeState:
        if not self.path.exists():
            return EpisodeState(episode_id=self.episode_id)
        return EpisodeState.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, episode: EpisodeState) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        episode.updated_at = utc_now()
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(episode.model_dump_json(indent=2), encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:  # OneDrive or an editor can hold the file briefly
                if attempt == 4:
                    raise
                time.sleep(0.2 * (attempt + 1))

    def reset(self) -> None:
        """Forget the episode: its history and the turn outputs written next to it."""
        if not self.dir.exists():
            return
        for path in self.dir.iterdir():
            if path.is_file() and (path.name == HISTORY_FILE or path.name.startswith("turn_")):
                path.unlink()

    @staticmethod
    def next_turn(episode: EpisodeState) -> int:
        return max((record.turn for record in episode.turns), default=0) + 1

    @staticmethod
    def state_before(
        episode: EpisodeState, turn: int
    ) -> tuple[ControllerState, Optional[TurnRecord], Optional[str]]:
        """Controller state entering `turn`: turn t-1's state_after, so a re-run is identical."""
        if turn <= 1:
            return ControllerState(), None, None
        previous = next((record for record in episode.turns if record.turn == turn - 1), None)
        if previous is None:
            return ControllerState(), None, f"turn {turn - 1} not in history; turn {turn} starts from A_0"
        return previous.state_after.model_copy(deep=True), previous, None

    @staticmethod
    def upsert(episode: EpisodeState, record: TurnRecord) -> list[int]:
        """Store the turn; later turns were computed from the old one, so they are dropped."""
        dropped = [r.turn for r in episode.turns if r.turn > record.turn]
        episode.turns = [r for r in episode.turns if r.turn < record.turn] + [record]
        return dropped


def _box_distance(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    centre = math.hypot((a[0] + a[2] - b[0] - b[2]) / 2, (a[1] + a[3] - b[1] - b[3]) / 2)
    size = (abs((a[2] - a[0]) - (b[2] - b[0])) + abs((a[3] - a[1]) - (b[3] - b[1]))) / 2
    return centre + 0.5 * size


def reidentify(
    boxes: dict[str, tuple[float, float, float, float]],
    memories: dict[str, OccupantMemory],
    turn: int,
    next_index: int,
    gate: float,
    max_absent_turns: int,
) -> tuple[dict[str, str], dict[str, OccupantMemory], int]:
    """Match this clip's SAM tracks to known occupants by image position (fixed camera).

    Greedy global matching on box distance within `gate`; new tracks get new keys and
    occupants missing for more than `max_absent_turns` turns are forgotten.
    """
    pairs = sorted(
        (_box_distance(box, memory.bbox), sam_id, key)
        for sam_id, box in boxes.items()
        for key, memory in memories.items()
    )
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for distance, sam_id, key in pairs:
        if distance > gate:
            break
        if sam_id in mapping or key in used:
            continue
        mapping[sam_id] = key
        used.add(key)
    updated: dict[str, OccupantMemory] = {}
    for key, memory in memories.items():
        if key in used:
            continue
        memory = memory.model_copy(update={"absent_turns": memory.absent_turns + 1})
        if memory.absent_turns <= max_absent_turns:
            updated[key] = memory
    for sam_id in sorted(boxes, key=lambda s: int(s) if s.isdigit() else 0):
        box = boxes[sam_id]
        if sam_id in mapping:
            key = mapping[sam_id]
            updated[key] = memories[key].model_copy(update={"bbox": box, "last_seen_turn": turn, "absent_turns": 0})
        else:
            key = f"occ-{next_index}"
            next_index += 1
            mapping[sam_id] = key
            updated[key] = OccupantMemory(key=key, bbox=box, last_seen_turn=turn)
    return mapping, updated, next_index
