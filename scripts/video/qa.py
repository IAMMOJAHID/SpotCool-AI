"""Check a cut for everything code can check, and list what still needs the user's material.

    uv run python scripts/video/qa.py [VIDEO] [--final]

VIDEO defaults to ``video/renders/spotcool_draft.mp4``; its ``.srt`` and ``.build.json`` (from
``assemble.py``) sit beside it. Each check prints PASS or FAIL:

- the length is under 3:00 and the storyboard's;
- 1920x1080 at 30 fps, H.264 (yuv420p) with AAC;
- the integrated loudness is -14 +/- 1 LUFS (ffmpeg's EBU R128 meter);
- the SRT is valid (numbered, in order, no overlap, at most 42 characters a line and 2 lines)
  and in step with the storyboard: every caption inside a narrated beat, saying its narration
  word for word;
- every number has a source (``storyboard_check``), and none is a never-quote figure;
- the results card is the saved run's, regenerated now through the world's report (from
  ``--run-dir`` or ``$SPOTCOOL_WORLD_RUN_DIR``); its figures are the run's in
  ``video/sources.yaml`` and none is a never-quote figure; the results beat cites that run's
  source; the results narration says only the card's numbers, its saving among them, and says
  "stub" exactly when the card is a stub run's; the render used the card; and its label is the
  verdict's.

Then PENDING lists what needs the user (the narration, missing takes, the music's credit) and
MANUAL what a person checks (``video/QA.md``). It exits 1 on a FAIL; ``--final`` fails on
PENDING too.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import captions
import media
import storyboard_check
from render_motion import OUT_DIR
from results_card import (
    QUOTABLE_LABEL,
    STUB_LABEL,
    CardRefused,
    ResultsCard,
    build_card,
    load_card,
    run_dir_from_env,
)
from storyboard import (
    RENDERS,
    REPO,
    Beat,
    Citation,
    Storyboard,
    load_narration,
    load_sources,
    load_storyboard,
    never_quoted,
    numbers,
)

from spotcool_world.runner.report import load_run

VIDEO = RENDERS / "spotcool_draft.mp4"
LIMIT_S = 180.0
LOUDNESS = (-15.0, -13.0)
MANUAL = (
    "Watch the whole cut once: no keys, emails, notifications or real names on screen "
    "(video/QA.md).",
    "Proofread the captions against the recording (video/QA.md).",
    "Upload and submit (video/README.md, step 7).",
)


@dataclass
class Report:
    """What the QA found."""

    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)

    def check(self, ok: bool, text: str) -> None:
        """Record one check."""
        (self.passed if ok else self.failed).append(text)


def check_stream(report: Report, video: Path, board: Storyboard) -> None:
    """Length, frame size, frame rate and codecs."""
    info = media.probe(video)
    length = float(info["format"]["duration"])
    report.check(length < LIMIT_S, f"length {length:.2f} s is under {LIMIT_S:g} s")
    report.check(
        abs(length - board.total_s) < 0.1,
        f"length {length:.2f} s is the storyboard's {board.total_s:g} s",
    )
    video_streams = [s for s in info["streams"] if s.get("codec_type") == "video"]
    audio_streams = [s for s in info["streams"] if s.get("codec_type") == "audio"]
    v = video_streams[0] if video_streams else {}
    size = f"{v.get('width')}x{v.get('height')}"
    report.check(
        size == f"{board.width}x{board.height}", f"frame {size} is {board.width}x{board.height}"
    )
    rate = v.get("r_frame_rate")
    report.check(rate == f"{board.fps}/1", f"frame rate {rate} is {board.fps}/1")
    report.check(
        v.get("codec_name") == "h264" and v.get("pix_fmt") == "yuv420p",
        f"video {v.get('codec_name')} {v.get('pix_fmt')} is h264 yuv420p",
    )
    codec = audio_streams[0].get("codec_name") if audio_streams else None
    report.check(codec == "aac", f"audio {codec} is aac")


def check_loudness(report: Report, video: Path) -> None:
    """Integrated loudness within the target."""
    loudness, peak = media.ebur128(video)
    low, high = LOUDNESS
    report.check(
        low <= loudness <= high,
        f"loudness {loudness:.1f} LUFS is -14 +/- 1 (true peak {peak:.1f} dBTP)",
    )


def check_captions(report: Report, srt: Path, board: Storyboard, narration: dict[str, str]) -> None:
    """The SRT's form, and its step with the storyboard's narration."""
    if not srt.is_file():
        report.check(False, f"captions: {srt.name} exists")
        return
    try:
        shown = captions.parse_srt(srt.read_text("utf-8"))
    except ValueError as exc:
        report.check(False, f"captions are valid SubRip: {exc}")
        return
    problems: list[str] = []
    said: dict[str, list[str]] = {}
    previous_end = 0.0
    for n, caption in enumerate(shown, 1):
        if caption.end <= caption.start or caption.start < previous_end - 1e-3:
            problems.append(f"caption {n} is out of order or overlaps the one before")
        previous_end = caption.end
        if len(caption.lines) > captions.MAX_LINES or any(
            len(line) > captions.MAX_CHARS for line in caption.lines
        ):
            problems.append(
                f"caption {n} has more than {captions.MAX_LINES} lines "
                f"or {captions.MAX_CHARS} characters a line"
            )
        beat = next((b for b in board.beats if b.start <= caption.start < b.end), None)
        if beat is None or not beat.narration_id or caption.end > beat.end + 1e-3:
            problems.append(f"caption {n} at {caption.start:.2f} s is not inside a narrated beat")
            continue
        said.setdefault(beat.narration_id, []).extend(caption.lines)
    for beat in board.beats:
        if beat.narration_id and _words(" ".join(said.get(beat.narration_id, []))) != _words(
            narration[beat.narration_id]
        ):
            problems.append(f"the captions of {beat.id} do not say its narration word for word")
    report.check(
        not problems,
        f"captions: {len(shown)} valid and in step with the storyboard"
        + "".join(f"; {p}" for p in problems),
    )


def _words(text: str) -> list[str]:
    return re.sub(r"\s+", " ", text).strip().split(" ")


def check_numbers(report: Report, board: Storyboard, narration: dict[str, str]) -> None:
    """Every number has a source, and none is never to be quoted."""
    errors = storyboard_check.check(board, narration, load_sources())
    report.check(
        not errors,
        "every number has a source (storyboard check)" + "".join(f"; {e}" for e in errors),
    )


def card_problems(
    card: ResultsCard, beat: Beat, said: str, citations: dict[str, Citation]
) -> list[str]:
    """Where the results card and the results beat disagree, one sentence each.

    Args:
        card: The generated card.
        beat: The beat that shows it.
        said: The beat's narration.
        citations: ``video/sources.yaml``.
    """
    problems = [f"card figure {f}: {why}" for f in card.figures for why in never_quoted(f)]
    run_sources = {c.id for c in citations.values() if c.run_id == card.run_id}
    if not run_sources & set(beat.sources):
        problems.append(f"the {beat.id} beat cites no source for the card's run {card.run_id}")
    shown = {n for figure in card.figures for n in numbers(figure)}
    spoken = numbers(said)
    stray = [n for n in spoken if n not in shown]
    if stray:
        problems.append(f"the {beat.id} narration says {', '.join(stray)}, which the card does not")
    saving = numbers(card.figures[0])
    if (
        saving and beat.narration_id is not None and saving[0] not in spoken
    ):  # a silent card shows it
        problems.append(
            f"the {beat.id} narration does not say the card's saving, {card.figures[0]}"
        )
    says_stub = "stub" in said.lower()
    if card.stub and not says_stub:
        problems.append(f"the card is a stub run's, but the {beat.id} narration never says so")
    if not card.stub and says_stub:
        problems.append(f"the card is a quotable run's, but the {beat.id} narration says stub")
    return problems


def check_card(report: Report, board: Storyboard, run_dir: Path | None = None) -> None:
    """The results card against the saved run, the sources, the narration and the render."""
    beat = next((b for b in board.beats if b.source.composition == "results_card"), None)
    if beat is None:
        return
    card = load_card()
    if card is None:
        report.check(False, "results card: run scripts/video/results_card.py")
        return
    try:
        fresh = build_card(load_run(run_dir or run_dir_from_env(), card.run_id))
    except (CardRefused, OSError, ValueError) as exc:
        report.check(False, f"results card regenerates from run {card.run_id}: {exc}")
        return
    report.check(card == fresh, f"results card is run {card.run_id}'s, regenerated now")
    report.check(
        card.run_id == beat.source.run_id,
        f"results card is the storyboard's run {beat.source.run_id}",
    )
    citations = load_sources()
    run_source = next((c for c in citations.values() if c.run_id == card.run_id), None)
    report.check(
        run_source is not None and run_source.figures == card.figures,
        f"results card figures {', '.join(card.figures)} are the run's in video/sources.yaml",
    )
    said = load_narration().get(beat.narration_id or "", "")
    problems = card_problems(card, beat, said, citations)
    report.check(
        not problems,
        "the results card agrees with its beat's sources and narration, and quotes nothing "
        "on the never-quote list" + "".join(f"; {p}" for p in problems),
    )
    used = OUT_DIR / "results_card.vars.json"
    rendered = json.loads(used.read_text("utf-8")) if used.is_file() else {}
    report.check(
        all(rendered.get(key) == value for key, value in card.variables.items()),
        "the results card render used this card",
    )
    label = STUB_LABEL if card.stub else QUOTABLE_LABEL
    report.check(
        card.label == label and rendered.get("label") == label,
        f'the {"stub" if card.stub else "quotable"} run carries "{label}"',
    )


def pending_items(record: dict, credits: str) -> list[str]:
    """What the cut still needs from the user, from the assembly's record."""
    items: list[str] = []
    if record["narration"]["kind"] != "recorded":
        items.append(
            "narration: the TTS placeholder; record video/raw/narration.wav "
            "(video/README.md, step 3)"
        )
    for beat in record["beats"]:
        if beat["kind"] == "slate":
            items.append(f"{beat['id']}: {beat['missing']}; {beat['how']}")
    music = record.get("music")
    if music and Path(music).name not in credits:
        items.append(f"music: add a CREDITS.md row naming {Path(music).name}")
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("video", nargs="?", type=Path, default=VIDEO)
    parser.add_argument("--final", action="store_true", help="fail on pending items too")
    parser.add_argument(
        "--run-dir", type=Path, help="the saved runs (default: $SPOTCOOL_WORLD_RUN_DIR)"
    )
    args = parser.parse_args(argv)
    built = args.video.with_suffix(".build.json")
    if not args.video.is_file() or not built.is_file():
        print(
            f"qa: no {args.video} with its .build.json: run scripts/video/assemble.py",
            file=sys.stderr,
        )
        return 1
    board, narration = load_storyboard(), load_narration()
    record = json.loads(built.read_text("utf-8"))
    report = Report()
    try:
        check_stream(report, args.video, board)
        check_loudness(report, args.video)
    except media.ToolError as exc:
        report.check(False, f"ffprobe and ffmpeg read the video: {exc}")
    check_captions(report, Path(record["srt"]), board, narration)
    check_numbers(report, board, narration)
    check_card(report, board, args.run_dir)
    report.pending = pending_items(record, (REPO / "CREDITS.md").read_text("utf-8"))
    for line in report.passed:
        print(f"PASS     {line}")
    for line in report.failed:
        print(f"FAIL     {line}")
    for line in report.pending:
        print(f"PENDING  {line}")
    if not record.get("music"):
        print("OPTIONAL music: none; add one with assemble.py --music (video/README.md, step 5)")
    for line in MANUAL:
        print(f"MANUAL   {line}")
    failed = bool(report.failed) or (args.final and bool(report.pending))
    verdict = (
        "fails"
        if failed
        else ("passes" if not report.pending else "passes, pending the user's material")
    )
    print(f"qa: {args.video.name} {verdict}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
