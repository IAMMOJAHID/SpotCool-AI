"""The video's sources in git, read back: the storyboard, the sources and the narration.

Every script in ``scripts/video/`` reads the video through this module, so they agree on the
beats, their times and their text. ``video/storyboard.yaml`` is the single source of truth;
``storyboard_check.py`` validates it.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
VIDEO_DIR = REPO / "video"
STORYBOARD_FILE = VIDEO_DIR / "storyboard.yaml"
SOURCES_FILE = VIDEO_DIR / "sources.yaml"
NARRATION_FILE = VIDEO_DIR / "narration.md"
MOTION_DIR = VIDEO_DIR / "motion"
OBS_COLLECTION = VIDEO_DIR / "obs" / "scene_collection.json"
RENDERS = VIDEO_DIR / "renders"
TAKES = VIDEO_DIR / "takes"
RAW = VIDEO_DIR / "raw"
RECORDED_NARRATION = RAW / "narration.wav"
PLACEHOLDER_NARRATION = RENDERS / "narration_placeholder.wav"

BEAT_TYPES = ("title", "motion", "screen", "phone")
TAKE_SUFFIXES = (".mkv", ".mp4", ".mov")
"""The containers a take may be in, in the order they are looked for."""

# A number standing on its own: not part of an identifier such as PNNL-22072 or CO2.
_NUMBER = re.compile(r"(?<![\w.\-])\d+(?:\.\d+)?(?![\w\-])")
_SPOKEN = re.compile(r"\d+(?:\.\d+)?|%|[^\W\d_][\w'’\-]*")


@dataclass(frozen=True)
class Scene:
    """An OBS scene switch during a take.

    Parameters:
        at: Seconds into the beat.
        scene: The OBS scene's name.
    """

    at: float
    scene: str


@dataclass(frozen=True)
class Cue:
    """A prompt for the person at the keyboard during a take.

    Parameters:
        at: Seconds into the beat.
        say: What to do.
    """

    at: float
    say: str


@dataclass(frozen=True)
class Replay:
    """A recorded run the take replays on the dashboard.

    Parameters:
        run_id: The run, a directory in ``SPOTCOOL_WORLD_RUN_DIR``.
        speed: Simulation seconds per wall second.
    """

    run_id: str
    speed: float


@dataclass(frozen=True)
class Source:
    """What a beat shows: exactly one of a composition, a take or a clip.

    Parameters:
        composition: A HyperFrames composition, ``video/motion/<id>/index.html``.
        take: An OBS take, ``video/takes/<id>.<suffix>``.
        clip: Any other video file, relative to the repo root.
        in_s: Seconds into the take or clip where the beat starts (a take's pre-roll).
        run_id: For the results card: the run it is generated from.
        replay: For a take: the recorded run it replays.
        dashboard_url: For a take: the URL the OBS dashboard browser source opens.
        scenes: For a take: its scene switches, the first at 0.
        cues: For a take: prompts for the person at the keyboard.
        before: For a take: what must be ready before it starts.
    """

    composition: str | None = None
    take: str | None = None
    clip: str | None = None
    in_s: float = 0.0
    run_id: str | None = None
    replay: Replay | None = None
    dashboard_url: str | None = None
    scenes: tuple[Scene, ...] = ()
    cues: tuple[Cue, ...] = ()
    before: tuple[str, ...] = ()


@dataclass(frozen=True)
class Beat:
    """One beat of the video.

    Parameters:
        id: Its name.
        start: Seconds into the video.
        end: Seconds into the video; the next beat starts here.
        type: ``title``, ``motion``, ``screen`` or ``phone``.
        source: What it shows.
        narration_id: Its section of ``video/narration.md``, or ``None`` for none.
        on_screen_text: Its text by key: a composition's variables, or a take's ``label``.
        sources: Ids in ``video/sources.yaml`` that back its numbers.
    """

    id: str
    start: float
    end: float
    type: str
    source: Source
    narration_id: str | None
    on_screen_text: dict[str, str]
    sources: tuple[str, ...]

    @property
    def duration(self) -> float:
        """The beat's length in seconds."""
        return self.end - self.start


@dataclass(frozen=True)
class Storyboard:
    """The whole video.

    Parameters:
        title: The video's name.
        fps: Frames a second.
        width: Frame width in pixels.
        height: Frame height in pixels.
        max_s: The longest the video may run.
        beats: The beats in time order.
    """

    title: str
    fps: int
    width: int
    height: int
    max_s: float
    beats: tuple[Beat, ...]

    @property
    def total_s(self) -> float:
        """The video's length: the last beat's end."""
        return self.beats[-1].end if self.beats else 0.0

    def beat(self, beat_id: str) -> Beat:
        """The beat with this id.

        Raises:
            KeyError: If there is none.
        """
        for beat in self.beats:
            if beat.id == beat_id:
                return beat
        raise KeyError(beat_id)

    def take_beat(self, take_id: str) -> Beat:
        """The beat that shows this take.

        Raises:
            KeyError: If no beat does.
        """
        for beat in self.beats:
            if beat.source.take == take_id:
                return beat
        raise KeyError(take_id)


@dataclass(frozen=True)
class Citation:
    """A source in ``video/sources.yaml``.

    Parameters:
        id: Its id.
        name: What it is.
        doc: The repo file that records its figures, relative to the repo root.
        figures: The numbers it backs, as shown.
        cite: Its line on the closing sources slide.
        url: Where to read it, if anywhere.
        run_id: For one of our runs: the run.
    """

    id: str
    name: str
    doc: str
    figures: tuple[str, ...]
    cite: str
    url: str | None = None
    run_id: str | None = None


def numbers(text: str) -> list[str]:
    """The numbers in a text, as written: ``"56.1"`` from ``"56.1 %"``.

    A digit run inside an identifier (``PNNL-22072``, ``CO2``, a run id) is not a number.
    """
    return _NUMBER.findall(text)


def spoken_words(text: str) -> int:
    """How many words a text takes to say, numbers spelled out: ``"19.94 %"`` is five.

    Used for the narration's pace; it errs long rather than short.
    """
    count = 0
    for token in _SPOKEN.findall(text):
        if token[0].isdigit():
            whole, _, decimals = token.partition(".")
            count += _integer_words(int(whole)) + (1 + len(decimals) if decimals else 0)
        else:
            count += 1  # a word, or "%" said as "percent"
    return count


def _integer_words(n: int) -> int:
    """Words to say a whole number in English: 230 is "two hundred thirty", three."""
    if n < 20:
        return 1
    if n < 100:
        return 1 if n % 10 == 0 else 2
    if n < 1000:
        return 2 + (_integer_words(n % 100) if n % 100 else 0)
    return 2 + (_integer_words(n % 1000) if n % 1000 else 0)


def load_storyboard(path: Path = STORYBOARD_FILE) -> Storyboard:
    """Read the storyboard.

    Raises:
        ValueError: If a field is missing or of the wrong kind.
        OSError: If the file cannot be read.
    """
    data = yaml.safe_load(path.read_text("utf-8"))
    video = _mapping(data, "video", "the storyboard")
    beats = data.get("beats")
    if not isinstance(beats, list) or not beats:
        raise ValueError("the storyboard has no beats")
    return Storyboard(
        title=str(video.get("title", "")),
        fps=int(video.get("fps", 30)),
        width=int(video.get("width", 1920)),
        height=int(video.get("height", 1080)),
        max_s=float(video.get("max_s", 178)),
        beats=tuple(_beat(entry, index) for index, entry in enumerate(beats)),
    )


def _beat(entry: Any, index: int) -> Beat:
    if not isinstance(entry, dict) or "id" not in entry:
        raise ValueError(f"beat {index + 1} has no id")
    where = f"beat {entry['id']}"
    for key in ("start", "end", "type", "source"):
        if key not in entry:
            raise ValueError(f"{where} has no {key}")
    text = entry.get("on_screen_text") or {}
    if not isinstance(text, dict):
        raise ValueError(f"{where}: on_screen_text must map keys to text")
    narration = entry.get("narration_id")
    return Beat(
        id=str(entry["id"]),
        start=float(entry["start"]),
        end=float(entry["end"]),
        type=str(entry["type"]),
        source=_source(_mapping(entry, "source", where), where),
        narration_id=None if narration is None else str(narration),
        on_screen_text={str(key): str(value) for key, value in text.items()},
        sources=tuple(str(source) for source in entry.get("sources") or ()),
    )


def _source(data: dict[str, Any], where: str) -> Source:
    kinds = [key for key in ("composition", "take", "clip") if data.get(key)]
    if len(kinds) != 1:
        raise ValueError(f"{where}: source needs exactly one of composition, take or clip")
    replay = data.get("replay")
    if replay is not None:
        if not isinstance(replay, dict) or "run_id" not in replay or "speed" not in replay:
            raise ValueError(f"{where}: replay needs run_id and speed")
        replay = Replay(str(replay["run_id"]), float(replay["speed"]))
    return Source(
        composition=_optional(data, "composition"),
        take=_optional(data, "take"),
        clip=_optional(data, "clip"),
        in_s=float(data.get("in_s", 0.0)),
        run_id=_optional(data, "run_id"),
        replay=replay,
        dashboard_url=_optional(data, "dashboard_url"),
        scenes=tuple(Scene(float(s["at"]), str(s["scene"])) for s in data.get("scenes") or ()),
        cues=tuple(Cue(float(c["at"]), str(c["say"])) for c in data.get("cues") or ()),
        before=tuple(str(line) for line in data.get("before") or ()),
    )


def _mapping(data: Any, key: str, where: str) -> dict[str, Any]:
    value = data.get(key) if isinstance(data, dict) else None
    if not isinstance(value, dict):
        raise ValueError(f"{where} has no {key} mapping")
    return value


def _optional(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    return None if value is None else str(value)


def load_sources(path: Path = SOURCES_FILE) -> dict[str, Citation]:
    """Read the sources by id.

    Raises:
        ValueError: If an entry lacks its name, doc, figures or cite.
        OSError: If the file cannot be read.
    """
    data = yaml.safe_load(path.read_text("utf-8")) or {}
    citations: dict[str, Citation] = {}
    for source_id, entry in data.items():
        if not isinstance(entry, dict):
            raise ValueError(f"source {source_id} is not a mapping")
        missing = [key for key in ("name", "doc", "figures", "cite") if key not in entry]
        if missing:
            raise ValueError(f"source {source_id} has no {', '.join(missing)}")
        citations[str(source_id)] = Citation(
            id=str(source_id),
            name=str(entry["name"]),
            doc=str(entry["doc"]),
            figures=tuple(str(figure) for figure in entry["figures"] or ()),
            cite=str(entry["cite"]),
            url=_optional(entry, "url"),
            run_id=_optional(entry, "run_id"),
        )
    return citations


def load_narration(path: Path = NARRATION_FILE) -> dict[str, str]:
    """Read the narration: each ``## <narration_id>`` section's text, on one line.

    Text before the first section (the reader's instructions) and HTML comments are left out.

    Raises:
        ValueError: If a section is empty or named twice.
        OSError: If the file cannot be read.
    """
    text = re.sub(r"<!--.*?-->", "", path.read_text("utf-8"), flags=re.DOTALL)
    sections: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in [*text.splitlines(), "## "]:  # the sentinel closes the last section
        if line.startswith("## "):
            if current is not None:
                if not lines:
                    raise ValueError(f"narration section {current} is empty")
                sections[current] = " ".join(lines)
            current, lines = line[3:].strip() or None, []
            if current in sections:
                raise ValueError(f"narration section {current} appears twice")
        elif current is not None and line.strip():
            lines.append(line.strip())
    return sections


def find_take(take_id: str, takes_dir: Path = TAKES) -> Path | None:
    """The take's recorded file, or ``None`` if it has not been recorded yet."""
    for suffix in TAKE_SUFFIXES:
        path = takes_dir / f"{take_id}{suffix}"
        if path.is_file():
            return path
    return None


def narrated(board: Storyboard) -> list[Beat]:
    """The beats with narration, in time order."""
    return [beat for beat in board.beats if beat.narration_id]
