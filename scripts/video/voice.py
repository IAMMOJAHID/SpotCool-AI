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
from storyboard import Storyboard, narrated, spoken_words

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
PACE_WPM = (100.0, 190.0)
"""The pace a paragraph can be read at; a cut that gives one outside it is in the wrong place."""
SHORT_WORDS = 10
"""A paragraph this short is mostly its pauses, so only its upper pace is checked."""
PAUSE_MARGIN_S = 0.4
"""How much longer than every pause it rejects each paragraph break it takes must be."""


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
    pauses: list[tuple[float, float]], total_s: float, paragraphs: list[tuple[str, int]]
) -> list[tuple[float, float]]:
    """Cut a recording into one spoken segment per paragraph, at its longest pauses.

    The cuts are trusted only if each break taken is at least ``PAUSE_MARGIN_S`` longer than
    every pause left inside a paragraph, and each segment's pace fits its paragraph's words
    (``PACE_WPM``).

    Args:
        pauses: The recording's silences, ``(start, end)`` in seconds, in time order.
        total_s: The recording's length.
        paragraphs: Each paragraph's name and spoken words, in order.

    Returns:
        Each segment's ``(start, end)``, leading and trailing silence trimmed.

    Raises:
        NarrationError: If there are too few pauses, the breaks are not clearly the longest
            pauses (naming them), or a segment's pace is off (naming the paragraphs).
    """
    count = len(paragraphs)
    lead = next((end for start, end in pauses if start <= 0.05), 0.0)
    trail = next((start for start, end in pauses if end >= total_s - 0.05), total_s)
    inner = [p for p in pauses if p[0] > 0.05 and p[1] < total_s - 0.05]
    if len(inner) < count - 1:
        raise NarrationError(
            f"the recording has {len(inner)} pauses between paragraphs, and the narration has "
            f"{count} paragraphs: record it again with a pause of about 2 s between paragraphs"
        )
    ranked = sorted(inner, key=lambda p: p[1] - p[0], reverse=True)
    if 0 < count - 1 < len(ranked):
        taken, left = ranked[count - 2], ranked[count - 1]
        if (taken[1] - taken[0]) - (left[1] - left[0]) < PAUSE_MARGIN_S:
            raise NarrationError(
                f"the pauses at {_at(taken)} and {_at(left)} of the recording are "
                f"{taken[1] - taken[0]:.1f} s and {left[1] - left[0]:.1f} s long: too close to "
                "tell a paragraph break from a pause inside a paragraph. Record the paragraphs "
                "around them again, with about 2 s between paragraphs and no pause that long "
                "inside one."
            )
    cuts = sorted(ranked[: count - 1])
    bounds = [lead, *(edge for cut in cuts for edge in cut), trail]
    segments = [(bounds[2 * i], bounds[2 * i + 1]) for i in range(count)]
    off: list[str] = []
    for (name, words), (start, end) in zip(paragraphs, segments, strict=True):
        pace = words / max(end - start, 1e-6) * 60
        slow = words >= SHORT_WORDS and pace < PACE_WPM[0]
        if slow or pace > PACE_WPM[1]:
            off.append(
                f"{name} ({start:.1f}-{end:.1f} s of the recording, {pace:.0f} words a minute)"
            )
    if off:
        raise NarrationError(
            "the cuts look misplaced: " + "; ".join(off) + ". A pause inside a paragraph was "
            "probably taken for a break, or a break was too short. Record those paragraphs "
            "again, with a clear 2 s pause before and after each and no long pause inside them."
        )
    return segments


def _at(pause: tuple[float, float]) -> str:
    return f"{pause[0]:.1f}-{pause[1]:.1f} s"


def place(
    board: Storyboard, segments: list[tuple[float, float]], words: dict[str, int] | None = None
) -> list[Placement]:
    """Start each segment at its beat, in order.

    Args:
        board: The storyboard.
        segments: One per narrated beat, from ``split``.
        words: Each paragraph's spoken words, to say how fast a late one must be read.

    Raises:
        NarrationError: If the counts differ, or a segment runs past its beat (each one named,
            with the pace that would fit).
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
            count = (words or {}).get(beat.narration_id)
            need = f", about {count / room * 60:.0f} words a minute" if count else ""
            late.append(f"{beat.id} ({end - start:.1f} s, room for {room:.1f} s{need})")
        placements.append(
            Placement(beat.id, beat.narration_id, beat.start + LEAD_S, start, end, end - start)
        )
    if late:
        raise NarrationError(
            "narration runs past its beat: " + "; ".join(late) + ". Record those paragraphs "
            "again at that pace, cut words from them in video/narration.md, or lengthen their "
            "beats in video/storyboard.yaml."
        )
    return placements


def placements_for(
    recording: Path, board: Storyboard, narration: dict[str, str]
) -> list[Placement]:
    """The placements of a narration recording (runs ffmpeg's silence detector).

    Args:
        recording: The narration, one paragraph per narrated beat, about 2 s apart.
        board: The storyboard.
        narration: Each section's text, for the pace checks.

    Raises:
        NarrationError: If it does not fit the storyboard.
        media.ToolError: If ffmpeg fails.
    """
    words = {
        beat.narration_id: spoken_words(narration[beat.narration_id])
        for beat in narrated(board)
        if beat.narration_id
    }
    paragraphs = list(words.items())
    pauses = media.silences(recording, PAUSE_DB, PAUSE_MIN_S)
    return place(board, split(pauses, media.duration(recording), paragraphs), words)


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
