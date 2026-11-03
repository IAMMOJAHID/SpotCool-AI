"""Assemble the video from the storyboard with ffmpeg: one command, no manual edit.

    uv run python scripts/video/assemble.py [--narration WAV] [--music FILE] [--output MP4]
                                            [--final]

Each beat shows its source for exactly its length: a motion render from
``video/renders/motion/``, a take from ``video/takes/`` (from its ``in_s``), or a clip. A source
that does not exist yet becomes a labelled slate ("TAKE MISSING: <beat>"), so a draft cut builds
before anything is filmed. Over the takes goes each beat's ``label``, and over everything the
captions, which are kept beside the video as an ``.srt``.

The sound is the narration (``video/raw/narration.wav``, else the TTS placeholder), each
paragraph started at its beat (``voice.py``), and, with ``--music``, one track looped under it
at 20 LU below the voice, faded in and out. The mix is normalised to -14 LUFS in two passes
(``loudnorm``, linear). The output is 1920x1080 at 30 fps, H.264 and AAC, written bit-exact so
the same inputs give the same file. ``--final`` refuses slates and the placeholder voice.
Beside the output, ``<output>.build.json`` records what went in, for ``qa.py``.
"""

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import captions
import media
import voice
from render_motion import OUT_DIR
from storyboard import (
    PLACEHOLDER_NARRATION,
    RECORDED_NARRATION,
    RENDERS,
    REPO,
    Beat,
    Storyboard,
    find_take,
    load_narration,
    load_storyboard,
)

OUTPUT = RENDERS / "spotcool_draft.mp4"
WORK = RENDERS / "work"
SLATE_COLOR = "0x0e1116"
PAD_COLOR = "0x0b0f14"
MUSIC_UNDER_LU = 20.0
"""How far the music sits under the voice (integrated loudness)."""
ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Segoe UI,46,&H00FFFFFF,&H00FFFFFF,&H70000000,&H00000000,0,0,0,0,100,100,0,0,3,12,0,2,160,160,54,1
Style: Label,Segoe UI,30,&H00ECF1F2,&H00FFFFFF,&H50000000,&H00000000,1,0,0,0,100,100,0,0,3,12,0,7,60,60,44,1
Style: Slate,Segoe UI,76,&H004EC1F2,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1
Style: SlateNote,Segoe UI,36,&H00B6ACA3,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


class BuildError(ValueError):
    """The cut cannot be built as asked."""


@dataclass(frozen=True)
class Visual:
    """What one beat shows in the cut.

    Parameters:
        beat: The beat.
        kind: ``render``, ``take``, ``clip`` or ``slate``.
        path: The file shown; ``None`` for a slate.
        in_s: Seconds into the file where the beat starts.
        missing: For a slate: the beat's missing source, as the slate says it.
        how: For a slate: how to make it.
    """

    beat: Beat
    kind: str
    path: Path | None
    in_s: float = 0.0
    missing: str = ""
    how: str = ""


def resolve(board: Storyboard) -> list[Visual]:
    """Each beat's visual: its source if it exists, else a slate saying what is missing."""
    visuals: list[Visual] = []
    for beat in board.beats:
        source = beat.source
        if source.composition:
            path = OUT_DIR / f"{source.composition}.mp4"
            kind, word = "render", "RENDER"
            how = "uv run python scripts/video/render_motion.py"
        elif source.take:
            path = find_take(source.take)
            kind, word = "take", "TAKE"
            how = f"uv run python scripts/video/take.py {source.take}"
        else:
            path = REPO / str(source.clip)
            kind, word, how = "clip", "CLIP", f"put the clip at {source.clip}"
        if path is not None and path.is_file():
            visuals.append(Visual(beat, kind, path, source.in_s))
        else:
            visuals.append(Visual(beat, "slate", None, 0.0, f"{word} MISSING: {beat.id}", how))
    return visuals


def pick_narration(given: Path | None) -> tuple[Path, str]:
    """The narration to use and what it is: ``recorded`` or ``placeholder``.

    Raises:
        BuildError: If there is none.
    """
    if given is not None:
        if not given.is_file():
            raise BuildError(f"no narration at {given}")
        return (
            given,
            "placeholder" if given.resolve() == PLACEHOLDER_NARRATION.resolve() else "recorded",
        )
    if RECORDED_NARRATION.is_file():
        return RECORDED_NARRATION, "recorded"
    if PLACEHOLDER_NARRATION.is_file():
        return PLACEHOLDER_NARRATION, "placeholder"
    raise BuildError(
        f"no narration: record {RECORDED_NARRATION}, or run scripts/video/tts_placeholder.py"
    )


def ass_time(seconds: float) -> str:
    """``H:MM:SS.cc``, as ASS writes times."""
    cs = round(seconds * 100)
    return f"{cs // 360_000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ass_text(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")")


def overlay(board: Storyboard, visuals: list[Visual], shown: list[captions.Caption]) -> str:
    """The ASS overlay burned into the cut: slates' text, takes' labels and the captions."""
    events: list[str] = []

    def event(style: str, start: float, end: float, text: str) -> None:
        events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}")

    cx = board.width // 2
    for visual in visuals:
        beat = visual.beat
        label = beat.on_screen_text.get("label", "")
        if visual.kind == "slate":
            event("Slate", beat.start, beat.end, f"{{\\pos({cx},440)}}{_ass_text(visual.missing)}")
            notes = [line for line in (label, f"Make it: {visual.how}") if line]
            event(
                "SlateNote",
                beat.start,
                beat.end,
                f"{{\\pos({cx},580)}}" + "\\N".join(map(_ass_text, notes)),
            )
        elif label:
            event("Label", beat.start, beat.end, _ass_text(label))
    for caption in shown:
        event("Caption", caption.start, caption.end, "\\N".join(map(_ass_text, caption.lines)))
    return ASS_HEADER.format(width=board.width, height=board.height) + "\n".join(events) + "\n"


def video_inputs(board: Storyboard, visuals: list[Visual]) -> tuple[list[str], list[str]]:
    """ffmpeg's input arguments and filter chains: each beat exactly its frames, 1080p30."""
    args: list[str] = []
    chains: list[str] = []
    size = f"{board.width}x{board.height}"
    for i, visual in enumerate(visuals):
        duration = visual.beat.duration
        frames = round(duration * board.fps)
        if visual.path is None:
            args += [
                "-f",
                "lavfi",
                "-t",
                f"{duration:g}",
                "-i",
                f"color=c={SLATE_COLOR}:s={size}:r={board.fps}",
            ]
            chains.append(
                f"[{i}:v]format=yuv420p,setsar=1,trim=end_frame={frames},setpts=PTS-STARTPTS[v{i}]"
            )
            continue
        seek = ["-ss", f"{visual.in_s:g}"] if visual.in_s else []
        args += [*seek, "-t", f"{duration + 1:g}", "-i", str(visual.path)]
        chains.append(
            f"[{i}:v]scale={board.width}:{board.height}:force_original_aspect_ratio=decrease:"
            "flags=lanczos,"
            f"pad={board.width}:{board.height}:(ow-iw)/2:(oh-ih)/2:color={PAD_COLOR},setsar=1,"
            f"fps={board.fps},format=yuv420p,tpad=stop_mode=clone:stop_duration={duration:g},"
            f"trim=end_frame={frames},setpts=PTS-STARTPTS[v{i}]"
        )
    return args, chains


def mix(voice_track: Path, music: Path | None, total_s: float, out: Path) -> Path:
    """The processed voice (``media.VOICE_CHAIN``), with the music, if any, looped under it.

    The music sits ``MUSIC_UNDER_LU`` below the processed voice's integrated loudness, fades in
    over 1 s and out over the last 3 s.
    """
    voice_chain = f"[0:a]{media.VOICE_CHAIN},aformat=channel_layouts=stereo"
    inputs = ["-i", str(voice_track)]
    if music is None:
        graph = f"{voice_chain}[out]"
    else:
        spoken = media.loudness(voice_track, f"{media.VOICE_CHAIN},")["input_i"]
        gain = spoken - MUSIC_UNDER_LU - media.loudness(music)["input_i"]
        fade_out = max(0.0, total_s - 3)
        graph = (
            f"{voice_chain}[v];[1:a]aresample=48000,aformat=channel_layouts=stereo,"
            f"atrim=0:{total_s:g},volume={gain:.2f}dB,afade=t=in:d=1,"
            f"afade=t=out:st={fade_out:g}:d=3[m];"
            "[v][m]amix=inputs=2:normalize=0:duration=first[out]"
        )
        inputs += ["-stream_loop", "-1", "-i", str(music)]
    encode = ["-map", "[out]", "-ar", "48000", "-c:a", "pcm_s16le", "-fflags", "+bitexact"]
    media.ffmpeg(*inputs, "-filter_complex", graph, *encode, str(out))
    return out


def master(mixed: Path) -> str:
    """The filter that brings the mix to -14 LUFS.

    A gain solved for the limiter (``media.limiter_gain``) takes the peaks, then the second,
    linear pass of a two-pass ``loudnorm`` lands the target.
    """
    shaped = f"volume={media.limiter_gain(mixed):.2f}dB,{media.LIMITER}"
    measured = media.loudness(mixed, f"{shaped},")
    return f"{shaped},{media.loudnorm_second_pass(measured)}"


def build(
    narration_arg: Path | None = None,
    music: Path | None = None,
    output: Path = OUTPUT,
    final: bool = False,
    board: Storyboard | None = None,
    narration: dict[str, str] | None = None,
) -> dict[str, object]:
    """Build the cut and its ``.srt`` and ``.build.json``; return the build record.

    ``board`` and ``narration`` default to ``video/storyboard.yaml`` and ``video/narration.md``.

    Raises:
        BuildError: If ``final`` and a slate or the placeholder voice remains, or there is no
            narration.
        voice.NarrationError: If the narration does not fit its beats.
        media.ToolError: If ffmpeg fails.
    """
    board = board or load_storyboard()
    narration = narration or load_narration()
    visuals = resolve(board)
    recording, voice_kind = pick_narration(narration_arg)
    if final:
        missing = [v.missing for v in visuals if v.kind == "slate"]
        if voice_kind == "placeholder":
            missing.append("the narration is the TTS placeholder")
        if missing:
            raise BuildError("not final: " + "; ".join(missing))
    if music is not None and not music.is_file():
        raise BuildError(f"no music at {music}")
    for visual in visuals:
        if (
            visual.path is not None
            and media.duration(visual.path) < visual.in_s + visual.beat.duration - 0.05
        ):
            print(
                f"warning: {visual.beat.id}: {visual.path.name} ends before the beat does; "
                "its last frame is held"
            )
    WORK.mkdir(parents=True, exist_ok=True)
    placements = voice.placements_for(recording, board)
    track = voice.write_track(recording, placements, board.total_s, WORK / "voice.wav")
    mixed = mix(track, music, board.total_s, WORK / "mix.wav")
    mastering = master(mixed)
    shown = captions.build(board, narration, placements)
    srt = output.with_suffix(".srt")
    output.parent.mkdir(parents=True, exist_ok=True)
    srt.write_text(captions.to_srt(shown), "utf-8")
    (WORK / "overlay.ass").write_text(overlay(board, visuals, shown), "utf-8")
    voice.save(placements, WORK / "narration_timing.json")

    args, chains = video_inputs(board, visuals)
    joined = "".join(f"[v{i}]" for i in range(len(visuals)))
    chains.append(f"{joined}concat=n={len(visuals)}:v=1:a=0,subtitles=filename=overlay.ass[vout]")
    chains.append(
        f"[{len(visuals)}:a]{mastering},aresample=48000,"
        f"atrim=0:{board.total_s:g},asetpts=PTS-STARTPTS[aout]"
    )
    video = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-profile:v", "high"]
    video += ["-pix_fmt", "yuv420p", "-r", str(board.fps), "-g", str(2 * board.fps)]
    audio = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    exact = ["-movflags", "+faststart", "-map_metadata", "-1", "-fflags", "+bitexact"]
    exact += ["-flags:v", "+bitexact", "-flags:a", "+bitexact"]
    graph = ["-filter_complex", ";".join(chains), "-map", "[vout]", "-map", "[aout]"]
    media.ffmpeg(
        *args, "-i", str(mixed), *graph, *video, *audio, *exact, str(output.resolve()), cwd=WORK
    )
    record: dict[str, object] = {
        "output": output.resolve().as_posix(),
        "srt": srt.resolve().as_posix(),
        "total_s": board.total_s,
        "final": final,
        "narration": {"path": recording.resolve().as_posix(), "kind": voice_kind},
        "music": music.resolve().as_posix() if music else None,
        "beats": [
            {
                "id": v.beat.id,
                "kind": v.kind,
                "path": v.path.resolve().as_posix() if v.path else None,
                "missing": v.missing or None,
                "how": v.how or None,
            }
            for v in visuals
        ],
        "placements": [asdict(p) for p in placements],
    }
    output.with_suffix(".build.json").write_text(json.dumps(record, indent=2) + "\n", "utf-8")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--narration", type=Path, help="default: the recording, else the placeholder"
    )
    parser.add_argument("--music", type=Path, help="one music track (credit it in CREDITS.md)")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "--final", action="store_true", help="refuse slates and the placeholder voice"
    )
    args = parser.parse_args(argv)
    try:
        record = build(args.narration, args.music, args.output, args.final)
    except (BuildError, voice.NarrationError, media.ToolError, OSError) as exc:
        print(f"assemble: {exc}", file=sys.stderr)
        return 1
    beats = record["beats"]
    assert isinstance(beats, list)
    for beat in beats:
        print(f"{beat['id']:10} {beat['kind']:6} {beat['missing'] or beat['path']}")
    narration = record["narration"]
    assert isinstance(narration, dict)
    print(f"narration: {narration['kind']}; music: {record['music'] or 'none'}")
    print(f"-> {record['output']} and {record['srt']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
