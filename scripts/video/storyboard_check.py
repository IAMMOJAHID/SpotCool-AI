"""Check the storyboard, the narration, the sources and the compositions against each other.

    uv run python scripts/video/storyboard_check.py

Prints each beat's narration pace, then every problem; exits 1 if there is one. It fails when:

- the video runs past ``max_s`` (2:58), a beat is off the frame grid, or beats overlap or leave
  a gap;
- a beat's narration would need more than ``MAX_WPM`` words a minute, a narration id is
  missing, or a section is used twice or never;
- a number on screen or in the narration is not among the figures of a source its beat cites,
  a source's figures are not in the repo file it names, or a never-quote figure appears;
- a composition is missing, its length differs from its beat's, its variables differ from what
  the render passes, or its own text shows a number;
- a take names an OBS scene the scene collection lacks, or a switch or cue falls outside its
  beat.
"""

import json
import os
import sys
from html.parser import HTMLParser
from pathlib import Path

from render_motion import GENERATED_KEYS, composition_dir
from storyboard import (
    BEAT_TYPES,
    OBS_COLLECTION,
    REPO,
    Beat,
    Citation,
    Storyboard,
    load_narration,
    load_sources,
    load_storyboard,
    never_quoted,
    numbers,
    spoken_words,
)

MAX_WPM = 150.0
REPLAY_LOAD_S = 3.0
"""Time allowed after a replay take's beat starts for its dashboard to load and connect."""
REPLAY_SPARE_S = 1.0
DAY_S = 12 * 3600.0
"""A replay's span when its run is not on this machine: the scenario day, 07:00 to 19:00."""


def check(
    board: Storyboard,
    narration: dict[str, str],
    citations: dict[str, Citation],
    repo: Path = REPO,
) -> list[str]:
    """Every problem with the video's sources, one sentence each; empty when there is none."""
    return [
        *_timing(board),
        *_narration(board, narration),
        *_numbers(board, narration, citations, repo),
        *_compositions(board),
        *_takes(board),
    ]


def pace(beat: Beat, narration: dict[str, str]) -> float:
    """The words a minute a beat's narration needs to fit the beat."""
    if not beat.narration_id or beat.narration_id not in narration:
        return 0.0
    return spoken_words(narration[beat.narration_id]) / beat.duration * 60


def _timing(board: Storyboard) -> list[str]:
    errors: list[str] = []
    ids = [beat.id for beat in board.beats]
    errors += [f"beat id {i} is used twice" for i in sorted({i for i in ids if ids.count(i) > 1})]
    previous = 0.0
    for beat in board.beats:
        if beat.type not in BEAT_TYPES:
            errors.append(f"{beat.id}: type {beat.type} is not one of {', '.join(BEAT_TYPES)}")
        composed = beat.source.composition is not None
        if composed != (beat.type in ("title", "motion")):
            errors.append(
                f"{beat.id}: a {beat.type} beat shows a composition only if it is title or motion"
            )
        if beat.end <= beat.start:
            errors.append(f"{beat.id}: ends at {beat.end:g} s, not after its start")
        for edge in (beat.start, beat.end):
            if abs(edge * board.fps - round(edge * board.fps)) > 1e-6:
                errors.append(f"{beat.id}: {edge:g} s is not on the {board.fps} fps frame grid")
        if beat.start > previous + 1e-9:
            errors.append(f"{beat.id}: a gap from {previous:g} to {beat.start:g} s")
        elif beat.start < previous - 1e-9:
            errors.append(f"{beat.id}: overlaps the beat before, which ends at {previous:g} s")
        previous = beat.end
    if board.total_s > board.max_s:
        errors.append(f"the video runs {board.total_s:g} s, past its {board.max_s:g} s limit")
    return errors


def _narration(board: Storyboard, narration: dict[str, str]) -> list[str]:
    errors: list[str] = []
    used = [beat.narration_id for beat in board.beats if beat.narration_id]
    for beat in board.beats:
        if beat.narration_id and beat.narration_id not in narration:
            errors.append(f"{beat.id}: no section ## {beat.narration_id} in video/narration.md")
        elif not beat.talk and (rate := pace(beat, narration)) > MAX_WPM:
            errors.append(
                f"{beat.id}: its narration needs {rate:.0f} words a minute, more than {MAX_WPM:g}"
            )
    errors += [
        f"narration section {key} is used by {used.count(key)} beats"
        for key in sorted(set(used))
        if used.count(key) > 1
    ]
    errors += [
        f"narration section {key} is used by no beat" for key in narration if key not in used
    ]
    return errors


def _numbers(
    board: Storyboard, narration: dict[str, str], citations: dict[str, Citation], repo: Path
) -> list[str]:
    errors: list[str] = []
    for citation in citations.values():
        doc = repo / citation.doc
        if not doc.is_file():
            errors.append(f"source {citation.id}: its doc {citation.doc} does not exist")
            continue
        recorded = set(numbers(doc.read_text("utf-8")))
        for figure in citation.figures:
            for number in numbers(figure):
                if number not in recorded:
                    errors.append(f"source {citation.id}: {number} is not in {citation.doc}")
            errors += [f"source {citation.id}: {why}" for why in never_quoted(figure)]
    for beat in board.beats:
        unknown = [source for source in beat.sources if source not in citations]
        errors += [f"{beat.id}: source {source} is not in video/sources.yaml" for source in unknown]
        backed = {
            number
            for source in beat.sources
            if source in citations
            for figure in citations[source].figures
            for number in numbers(figure)
        }
        texts = [(f"on-screen {key}", text) for key, text in beat.on_screen_text.items()]
        if beat.narration_id in narration:
            texts.append(("narration", narration[beat.narration_id]))
        for where, text in texts:
            for number in numbers(text):
                if number not in backed:
                    errors.append(
                        f"{beat.id}: {where} shows {number}, which no source it cites backs"
                    )
            errors += [f"{beat.id}: {where}: {why}" for why in never_quoted(text)]
        run_id = beat.source.run_id
        if run_id and not any(
            citations[s].run_id == run_id for s in beat.sources if s in citations
        ):
            errors.append(f"{beat.id}: cites no source for its run {run_id} in video/sources.yaml")
    return errors


class _Composition(HTMLParser):
    """The facts the check needs from a composition's HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.variables: list[str] = []
        self.duration: float | None = None
        self.text: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "html" and values.get("data-composition-variables"):
            self.variables = [
                v["id"] for v in json.loads(values["data-composition-variables"] or "[]")
            ]
        if (
            self.duration is None
            and values.get("data-composition-id")
            and values.get("data-duration")
        ):
            self.duration = float(values["data-duration"] or 0)
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.text.append(data.strip())


def _compositions(board: Storyboard) -> list[str]:
    errors: list[str] = []
    for beat in board.beats:
        composition = beat.source.composition
        if not composition:
            continue
        path = composition_dir(composition) / "index.html"
        if not path.is_file():
            errors.append(
                f"{beat.id}: composition {composition} has no {path.relative_to(REPO).as_posix()}"
            )
            continue
        parsed = _Composition()
        parsed.feed(path.read_text("utf-8"))
        if parsed.duration is None or abs(parsed.duration - beat.duration) > 1e-6:
            errors.append(
                f"{beat.id}: composition {composition} lasts {parsed.duration} s, "
                f"its beat {beat.duration:g} s"
            )
        passed = set(beat.on_screen_text) | set(GENERATED_KEYS.get(composition, ()))
        declared = set(parsed.variables)
        if passed - declared:
            errors.append(
                f"{beat.id}: composition {composition} declares no variable "
                + ", ".join(sorted(passed - declared))
            )
        if declared - passed:
            errors.append(
                f"{beat.id}: nothing sets composition {composition}'s "
                + ", ".join(sorted(declared - passed))
            )
        for number in numbers(" ".join(parsed.text)):
            errors.append(
                f"{beat.id}: composition {composition} shows {number} in its own text; "
                "put it in on_screen_text"
            )
    return errors


def obs_scenes(path: Path = OBS_COLLECTION) -> set[str]:
    """The scenes of the OBS scene collection."""
    data = json.loads(path.read_text("utf-8"))
    return {source["name"] for source in data.get("sources", []) if source.get("id") == "scene"}


def replay_span_s(run_id: str) -> float:
    """The simulated seconds a run spans: its manifest's, if the run is on this machine."""
    manifest = Path(os.environ.get("SPOTCOOL_WORLD_RUN_DIR", "")) / run_id / "manifest.json"
    if os.environ.get("SPOTCOOL_WORLD_RUN_DIR") and manifest.is_file():
        data = json.loads(manifest.read_text("utf-8"))
        return float(data["end_time_s"]) - float(data["start_time_s"])
    return DAY_S


def _takes(board: Storyboard) -> list[str]:
    errors: list[str] = []
    scenes = obs_scenes()
    for beat in board.beats:
        source = beat.source
        if source.in_s < 0:
            errors.append(f"{beat.id}: in_s is negative")
        if not source.take:
            continue
        if not source.scenes or source.scenes[0].at != 0:
            errors.append(f"{beat.id}: take {source.take} needs a scene at 0")
        for item in (*source.scenes, *source.cues):
            if not 0 <= item.at < beat.duration:
                errors.append(f"{beat.id}: a switch or cue at {item.at:g} s is outside the beat")
        for scene in source.scenes:
            if scene.scene not in scenes:
                errors.append(f"{beat.id}: OBS scene {scene.scene} is not in {OBS_COLLECTION.name}")
        if source.replay and source.replay.speed <= 0:
            errors.append(f"{beat.id}: replay speed must be more than 0")
        elif source.replay:
            playing = replay_span_s(source.replay.run_id) / source.replay.speed
            if REPLAY_LOAD_S + playing + REPLAY_SPARE_S > beat.duration:
                errors.append(
                    f"{beat.id}: the replay plays {playing:.1f} s, which with "
                    f"{REPLAY_LOAD_S:g} s to load and {REPLAY_SPARE_S:g} s spare does not fit "
                    f"its {beat.duration:g} s beat: raise its speed"
                )
    return errors


def main(argv: list[str] | None = None) -> int:
    del argv
    board, narration, citations = load_storyboard(), load_narration(), load_sources()
    for beat in board.beats:
        rate = pace(beat, narration)
        words = f"{rate:5.0f} words a minute" if beat.narration_id else "   no narration"
        print(f"{beat.start:6.1f}-{beat.end:6.1f} s  {beat.type:6}  {beat.id:10}  {words}")
    print(f"total {board.total_s:g} s (limit {board.max_s:g} s)")
    errors = check(board, narration, citations)
    for error in errors:
        print(f"FAIL {error}")
    print("storyboard: ok" if not errors else f"storyboard: {len(errors)} problems")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
