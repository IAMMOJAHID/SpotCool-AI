"""Render the HyperFrames motion graphics into ``video/renders/motion/``.

    uv run python scripts/video/render_motion.py [--only ID ...] [--check]

Each composition beat of the storyboard renders its composition, ``video/motion/<id>/index.html``,
to ``video/renders/motion/<id>.mp4``. Its variables are the beat's ``on_screen_text``; the
``results_card`` also takes the generated card (``results_card.py``), and ``close`` the sources
slide, built from ``video/sources.yaml``. So the text on screen is the storyboard's, and no
composition shows a number of its own.

The CLI is pinned (``HYPERFRAMES``) and run through npx, with telemetry and update checks off,
software rendering (``--no-browser-gpu``), a fixed worker count and static-frame dedup off
(``HF_STATIC_DEDUP=false``: the frames it reuses for a still stretch varied from run to run), so
the same inputs render the same bytes. ``--check`` runs ``hyperframes check`` on each
composition instead.
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import media
from results_card import CARD_FILE, CARD_KEYS, ResultsCard, load_card
from storyboard import (
    MOTION_DIR,
    RENDERS,
    Beat,
    Citation,
    Storyboard,
    load_sources,
    load_storyboard,
)

HYPERFRAMES = "hyperframes@0.8.6"
"""The HyperFrames CLI release every render uses (Apache-2.0, from its npm package metadata)."""
OUT_DIR = RENDERS / "motion"
WORKERS = "4"
QUIET_ENV = {
    "HYPERFRAMES_NO_TELEMETRY": "1",
    "HYPERFRAMES_NO_UPDATE_CHECK": "1",
    "HYPERFRAMES_SKIP_SKILLS": "1",
    "HF_STATIC_DEDUP": "false",
}
"""No telemetry, no update or skills checks, and no static-frame dedup (it broke determinism)."""
GENERATED_KEYS: dict[str, tuple[str, ...]] = {
    "results_card": CARD_KEYS,
    "close": ("sources_text",),
}
"""The variables a composition gets from code rather than from ``on_screen_text``."""


def composition_dir(composition: str) -> Path:
    """The HyperFrames project of a composition."""
    return MOTION_DIR / composition


def sources_text(board: Storyboard, citations: dict[str, Citation]) -> str:
    """The sources slide: each source the video cites, once, in the order first cited."""
    cited = dict.fromkeys(source for beat in board.beats for source in beat.sources)
    return "\n".join(citations[source].cite for source in cited if source in citations)


def variables_for(
    beat: Beat,
    board: Storyboard,
    citations: dict[str, Citation],
    card: ResultsCard | None,
) -> dict[str, str | float]:
    """A composition beat's variables: its on-screen text plus what code generates for it.

    Raises:
        ValueError: If the results card is needed and missing, or made from another run.
    """
    values: dict[str, str | float] = dict(beat.on_screen_text)
    composition = beat.source.composition
    if composition == "results_card":
        if card is None:
            raise ValueError(f"no results card: run scripts/video/results_card.py ({CARD_FILE})")
        if beat.source.run_id and card.run_id != beat.source.run_id:
            raise ValueError(
                f"the results card is from run {card.run_id}, the storyboard names "
                f"{beat.source.run_id}: run scripts/video/results_card.py again"
            )
        values.update(card.variables)
    if composition == "close":
        values["sources_text"] = sources_text(board, citations)
    return values


def npx() -> str:
    """The npx executable.

    Raises:
        media.ToolError: If Node.js is not installed.
    """
    found = shutil.which("npx")
    if not found:
        raise media.ToolError("npx is not on PATH: install Node.js 22 or newer")
    return found


def render(composition: str, variables: dict[str, str | float], out: Path) -> Path:
    """Render one composition with its variables to an MP4 (1920x1080, 30 fps).

    The variables are kept beside it as ``<out>.vars.json``, which the QA reads back.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    values = out.with_suffix(".vars.json")
    values.write_text(json.dumps(variables, indent=2, ensure_ascii=False) + "\n", "utf-8")
    args = [npx(), "--yes", HYPERFRAMES, "render", str(composition_dir(composition))]
    args += ["--output", str(out), "--fps", "30", "--quality", "high", "--workers", WORKERS]
    args += ["--no-browser-gpu", "--strict", "--strict-variables", "--quiet"]
    args += ["--variables-file", str(values)]
    media.run(args, cwd=composition_dir(composition), env={**os.environ, **QUIET_ENV})
    return out


def check(composition: str) -> None:
    """Run ``hyperframes check`` (lint, runtime, layout, contrast) on one composition.

    Raises:
        media.ToolError: If it finds an error.
    """
    args = [npx(), "--yes", HYPERFRAMES, "check", str(composition_dir(composition))]
    media.run(args, cwd=composition_dir(composition), env={**os.environ, **QUIET_ENV})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--only", nargs="+", metavar="ID", help="these compositions only")
    parser.add_argument("--check", action="store_true", help="check instead of render")
    args = parser.parse_args(argv)
    board, citations = load_storyboard(), load_sources()
    beats = [beat for beat in board.beats if beat.source.composition]
    if args.only:
        unknown = set(args.only) - {beat.source.composition for beat in beats}
        if unknown:
            parser.error(f"no composition beat for {', '.join(sorted(unknown))}")
        beats = [beat for beat in beats if beat.source.composition in args.only]
    try:
        card = load_card()
        for beat in beats:
            composition = beat.source.composition
            assert composition is not None
            if args.check:
                check(composition)
                print(f"checked {composition}")
                continue
            out = render(
                composition,
                variables_for(beat, board, citations, card),
                OUT_DIR / f"{composition}.mp4",
            )
            print(f"{composition}: {beat.duration:g} s -> {out}")
    except (media.ToolError, ValueError, OSError) as exc:
        print(f"render_motion: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
