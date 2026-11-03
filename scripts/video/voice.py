"""Where each narration paragraph plays: cut one recording at its pauses, place each at its beat.

The narration is one recording, ``video/raw/narration.wav`` (or the placeholder that
``tts_placeholder.py`` writes), with a pause of about 2 s between paragraphs. ``split`` cuts it
at its longest pauses into one segment per narrated beat; ``place`` starts each segment
``LEAD_S`` after its beat starts, and refuses one that would run past its beat. Captions and the
assembly both work from these placements, so they stay in step.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import media
from storyboard import Storyboard, narrated

LEAD_S = 0.4
"""How long after its beat starts a paragraph starts."""
TAIL_S = 0.3
"""How much quiet a paragraph leaves before its beat ends."""
PAUSE_DB = -35.0
"""Quieter than this counts as a pause."""
PAUSE_MIN_S = 0.6
"""The shortest pause the detector reports; the cuts go at the longest ones."""
ESTIMATE_WPM = 140.0
"""The pace assumed for captions when there is no recording."""


class NarrationError(ValueError):
    """The recording does not fit the storyboard."""


@dataclass(frozen=True)
class Placement:
    """One paragraph of the narration in the cut.

    Parameters:
        beat_id: The beat it plays in.
        narration_id: Its section of ``video/narration.md``.
        at_s: Where it starts in the video.
        src_start: Where it starts in the recording (``None`` for an estimate).
        src_end: Where it ends in the recording.
        length_s: How long it plays.
    """

    beat_id: str
    narration_id: str
    at_s: float
    src_start: float | None
    src_end: float | None
    length_s: float

    @property
    def end_s(self) -> float:
        """Where it ends in the video."""
        return self.at_s + self.length_s


def split(
    pauses: list[tuple[float, float]], total_s: float, count: int
) -> list[tuple[float, float]]:
    """Cut a recording into ``count`` spoken segments at its ``count - 1`` longest pauses.

    Args:
        pauses: The recording's silences, ``(start, end)`` in seconds, in time order.
        total_s: The recording's length.
        count: How many paragraphs it holds.

    Returns:
        Each segment's ``(start, end)``, leading and trailing silence trimmed.

    Raises:
        NarrationError: If it has fewer inner pauses than it needs.
    """
    lead = next((end for start, end in pauses if start <= 0.05), 0.0)
    trail = next((start for start, end in pauses if end >= total_s - 0.05), total_s)
    inner = [p for p in pauses if p[0] > 0.05 and p[1] < total_s - 0.05]
    if len(inner) < count - 1:
        raise NarrationError(
            f"the recording has {len(inner)} pauses between paragraphs, and the narration has "
            f"{count} paragraphs: leave a pause of about 2 s between paragraphs"
        )
    cuts = sorted(sorted(inner, key=lambda p: p[1] - p[0], reverse=True)[: count - 1])
    bounds = [lead, *(edge for cut in cuts for edge in cut), trail]
    return [(bounds[2 * i], bounds[2 * i + 1]) for i in range(count)]


def place(board: Storyboard, segments: list[tuple[float, float]]) -> list[Placement]:
    """Start each segment at its beat, in order.

    Raises:
        NarrationError: If the counts differ, or a segment runs past its beat (each one named).
    """
    beats = narrated(board)
    if len(segments) != len(beats):
        raise NarrationError(f"{len(segments)} segments for {len(beats)} narrated beats")
    placements: list[Placement] = []
    late: list[str] = []
    for beat, (start, end) in zip(beats, segments, strict=True):
        assert beat.narration_id is not None
        room = beat.duration - LEAD_S - TAIL_S
        if end - start > room:
            late.append(f"{beat.id} ({end - start:.1f} s, room for {room:.1f} s)")
        placements.append(
            Placement(beat.id, beat.narration_id, beat.start + LEAD_S, start, end, end - start)
        )
    if late:
        raise NarrationError(
            "narration runs past its beat: " + "; ".join(late) + ". Read it faster, cut words "
            "from video/narration.md, or lengthen the beat in video/storyboard.yaml."
        )
    return placements


def placements_for(recording: Path, board: Storyboard) -> list[Placement]:
    """The placements of a narration recording (runs ffmpeg's silence detector).

    Raises:
        NarrationError: If it does not fit the storyboard.
        media.ToolError: If ffmpeg fails.
    """
    count = len(narrated(board))
    pauses = media.silences(recording, PAUSE_DB, PAUSE_MIN_S)
    return place(board, split(pauses, media.duration(recording), count))


def estimate(board: Storyboard, words: dict[str, int]) -> list[Placement]:
    """Placements without a recording: each paragraph at ``ESTIMATE_WPM``, cut at its beat.

    Args:
        board: The storyboard.
        words: Each narration section's spoken word count.
    """
    placements = []
    for beat in narrated(board):
        assert beat.narration_id is not None
        length = min(words[beat.narration_id] / ESTIMATE_WPM * 60, beat.duration - LEAD_S - TAIL_S)
        placements.append(
            Placement(beat.id, beat.narration_id, beat.start + LEAD_S, None, None, length)
        )
    return placements


def write_track(recording: Path, placements: list[Placement], total_s: float, out: Path) -> Path:
    """Write the placed narration: each segment at its time, silence between, ``total_s`` long.

    Args:
        recording: The narration recording.
        placements: From ``placements_for``.
        total_s: The video's length.
        out: The WAV to write (48 kHz mono).
    """
    count = len(placements)
    branches = "".join(f"[s{i}]" for i in range(count))
    graph = [f"[0:a]aresample=48000,aformat=channel_layouts=mono,asplit={count}{branches}"]
    # A silent base of the video's length: amix starts at its earliest input, and ffmpeg
    # delays a stream by its timestamps, which a WAV drops, so the base keeps t = 0.
    graph.append(f"anullsrc=r=48000:cl=mono,atrim=0:{total_s}[base]")
    for i, p in enumerate(placements):
        delay = round(p.at_s * 1000)
        graph.append(
            f"[s{i}]atrim={p.src_start}:{p.src_end},asetpts=PTS-STARTPTS,adelay={delay}:all=1[d{i}]"
        )
    mix = "".join(f"[d{i}]" for i in range(count))
    graph.append(f"[base]{mix}amix=inputs={count + 1}:normalize=0:duration=first[out]")
    out.parent.mkdir(parents=True, exist_ok=True)
    encode = ["-c:a", "pcm_s16le", "-ar", "48000", "-fflags", "+bitexact", str(out)]
    media.ffmpeg("-i", str(recording), "-filter_complex", ";".join(graph), "-map", "[out]", *encode)
    return out


def save(placements: list[Placement], path: Path) -> Path:
    """Write placements as JSON (for ``captions.py`` and the QA)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(p) for p in placements], indent=2) + "\n", "utf-8")
    return path


def load(path: Path) -> list[Placement]:
    """Read placements written by ``save``."""
    return [Placement(**entry) for entry in json.loads(path.read_text("utf-8"))]
