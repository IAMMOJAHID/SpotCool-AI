"""Build the captions from the narration and the storyboard's timings.

    uv run python scripts/video/captions.py [--narration WAV] [--out SRT]

Each narration paragraph becomes captions of at most ``MAX_LINES`` lines of ``MAX_CHARS``
characters. A sentence never shares a line with the next, and a number keeps its unit
("19.94 %"). The captions share the paragraph's time on air, each in proportion to the words
it takes to say. That time comes from the recording (``voice.placements_for``: the recorded
narration, else the placeholder), or, without one, from a pace of 140 words a minute.
``assemble.py`` burns the same captions in and keeps the ``.srt``.
"""

import argparse
import math
import re
import sys
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import media
import voice
from storyboard import (
    PLACEHOLDER_NARRATION,
    RECORDED_NARRATION,
    RENDERS,
    Storyboard,
    load_narration,
    load_storyboard,
    spoken_words,
)

MAX_CHARS = 42
MAX_LINES = 2
SRT_FILE = RENDERS / "spotcool_draft.srt"
_NBSP = " "


@dataclass(frozen=True)
class Caption:
    """One caption on screen.

    Parameters:
        start: Seconds into the video.
        end: Seconds into the video.
        lines: Its lines, at most ``MAX_LINES`` of at most ``MAX_CHARS`` characters.
        beat_id: The beat whose narration it shows (``""`` when read back from an SRT).
    """

    start: float
    end: float
    lines: tuple[str, ...]
    beat_id: str = ""


def sentences(paragraph: str) -> list[str]:
    """A paragraph's sentences."""
    return [s for s in re.split(r"(?<=[.!?])\s+", paragraph.strip()) if s]


def wrap(sentence: str, width: int = MAX_CHARS) -> list[str]:
    """A sentence in as few lines as fit ``width``, their lengths as even as possible.

    A number and the unit after it ("17.8 %", "2.13 kelvin") stay on one line.
    """
    glued = _glue(sentence)
    words = _words(glued)
    total = len(glued)
    for target in range(max(1, math.ceil(total / math.ceil(total / width))), width + 1):
        lines = _greedy(words, target)
        if len(lines) <= math.ceil(total / width) and all(len(line) <= width for line in lines):
            return [line.replace(_NBSP, " ") for line in lines]
    return [line.replace(_NBSP, " ") for line in _greedy(words, width)]


def _glue(text: str) -> str:
    """Join a number to its unit with a no-break space, so no split falls between them."""
    return re.sub(r"(\d) (%|kWh|K h|kelvin)", lambda m: m.group(1) + _NBSP + m.group(2), text)


def parts(sentence: str) -> list[str]:
    """A sentence in as few pieces as fit a caption each.

    Cuts at its clauses (after a comma, colon or semicolon) when such cuts fit, the most even
    of them; else at the words that make the pieces most even.
    """
    if len(wrap(sentence)) <= MAX_LINES:
        return [sentence]
    words = _words(_glue(sentence))
    clause_ends = [i for i, word in enumerate(words[:-1]) if word[-1] in ",:;"]
    for count in range(math.ceil(len(wrap(sentence)) / MAX_LINES), len(words) + 1):
        clauses = [_cut(words, list(cuts)) for cuts in combinations(clause_ends, count - 1)]
        for options in (clauses, [_cut(words, _even_cuts(words, count))]):
            fitting = [o for o in options if all(len(wrap(piece)) <= MAX_LINES for piece in o)]
            if fitting:
                return min(fitting, key=lambda o: max(len(piece) for piece in o))
    return [word.replace(_NBSP, " ") for word in words]


def _words(text: str) -> list[str]:
    """Words split at plain spaces only: ``str.split()`` would split the no-break space too."""
    return [word for word in text.split(" ") if word]


def _even_cuts(words: list[str], count: int) -> list[int]:
    """The word indexes to cut after, for ``count`` pieces of near equal length."""
    ends, position = [], 0
    for word in words[:-1]:
        position += len(word) + 1
        ends.append(position)
    total = position + len(words[-1])
    return sorted(
        {
            min(range(len(ends)), key=lambda i: abs(ends[i] - total * j / count))
            for j in range(1, count)
        }
    )


def _cut(words: list[str], cuts: list[int]) -> list[str]:
    pieces, start = [], 0
    for cut in [*cuts, len(words) - 1]:
        pieces.append(" ".join(words[start : cut + 1]).replace(_NBSP, " "))
        start = cut + 1
    return pieces


def _greedy(words: list[str], width: int) -> list[str]:
    lines: list[str] = []
    for word in words:
        if lines and len(lines[-1]) + 1 + len(word) <= width:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines


def chunk(paragraph: str) -> list[tuple[str, ...]]:
    """A paragraph's captions, at most ``MAX_LINES`` lines each.

    Short sentences share a caption, a line each; a sentence too long for one caption is cut
    into even pieces (``parts``), one caption each.
    """
    groups: list[list[str]] = []
    for sentence in sentences(paragraph):
        pieces = parts(sentence)
        if len(pieces) == 1 and groups and len(groups[-1]) + len(wrap(sentence)) <= MAX_LINES:
            groups[-1] += wrap(sentence)
        else:
            groups += [wrap(piece) for piece in pieces]
    return [tuple(group) for group in groups]


def build(
    board: Storyboard,
    narration: dict[str, str],
    placements: list[voice.Placement],
    word_times: dict[str, tuple[list[float], float]] | None = None,
) -> list[Caption]:
    """Every caption of the video, in time order.

    Args:
        board: The storyboard.
        narration: Each section's text.
        placements: Where each paragraph plays (``voice.placements_for`` or ``voice.estimate``).
        word_times: By narration id: when each spoken word starts in the video, and when the last
            ends. A caption then starts on its first word; otherwise captions split the paragraph
            evenly by words.
    """
    captions: list[Caption] = []
    for placement in placements:
        groups = chunk(narration[placement.narration_id])
        weights = [spoken_words(" ".join(group)) for group in groups]
        timed = (word_times or {}).get(placement.narration_id)
        t = timed[0][0] if timed else placement.at_s
        said = 0
        for group, weight in zip(groups, weights, strict=True):
            said += weight
            if timed:
                index = round(said / sum(weights) * len(timed[0]))
                end = timed[0][index] if index < len(timed[0]) else timed[1]
                end = max(end, t + 0.4)
            else:
                end = t + placement.length_s * weight / sum(weights)
            captions.append(Caption(round(t, 3), round(end, 3), group, placement.beat_id))
            t = end
    return captions


def srt_time(seconds: float) -> str:
    """``HH:MM:SS,mmm``."""
    ms = round(seconds * 1000)
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def to_srt(captions: list[Caption]) -> str:
    """The captions as SubRip text."""
    blocks = [
        f"{n}\n{srt_time(c.start)} --> {srt_time(c.end)}\n" + "\n".join(c.lines) + "\n"
        for n, c in enumerate(captions, 1)
    ]
    return "\n".join(blocks)


_TIME = r"(\d\d):(\d\d):(\d\d),(\d\d\d)"


def parse_srt(text: str) -> list[Caption]:
    """Read SubRip text back.

    Raises:
        ValueError: If a block is malformed or out of sequence.
    """
    captions: list[Caption] = []
    for n, block in enumerate(re.split(r"\n\s*\n", text.strip()), 1):
        lines = block.splitlines()
        if len(lines) < 3 or lines[0].strip() != str(n):
            raise ValueError(f"caption {n}: expected its number, its times and its text")
        times = re.fullmatch(rf"{_TIME} --> {_TIME}", lines[1].strip())
        if not times:
            raise ValueError(f"caption {n}: bad times {lines[1]!r}")
        values = [int(v) for v in times.groups()]
        start = values[0] * 3600 + values[1] * 60 + values[2] + values[3] / 1000
        end = values[4] * 3600 + values[5] * 60 + values[6] + values[7] / 1000
        captions.append(Caption(start, end, tuple(lines[2:])))
    return captions


def placements(
    board: Storyboard, narration: dict[str, str], recording: Path | None
) -> list[voice.Placement]:
    """Where each paragraph plays: from the recording if there is one, else estimated."""
    if recording is not None and recording.is_file():
        return voice.placements_for(recording, board, narration)
    words = {key: spoken_words(text) for key, text in narration.items()}
    return voice.estimate(board, words)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--narration", type=Path, help="default: the recording, else the placeholder"
    )
    parser.add_argument("--out", type=Path, default=SRT_FILE)
    args = parser.parse_args(argv)
    board, narration = load_storyboard(), load_narration()
    recording = args.narration or next(
        (p for p in (RECORDED_NARRATION, PLACEHOLDER_NARRATION) if p.is_file()), None
    )
    try:
        captions = build(board, narration, placements(board, narration, recording))
    except (voice.NarrationError, media.ToolError, OSError) as exc:
        print(f"captions: {str(exc).splitlines()[0]}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(to_srt(captions), "utf-8")
    timing = recording.name if recording else "estimated at 140 words a minute"
    print(f"{len(captions)} captions ({timing}) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
