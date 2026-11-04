"""Cut the user's talking clips for the final video (video/takes/, git-ignored).

    uv run python scripts/video/prep_clips.py [FOOTAGE_DIR]

The body drops three phrases: "and nearly 40 % of that energy is wasted" (no credible source),
"multi-agent" (the repo is one agent with several loops) and "and ambient audio" (the agent only
hears a complaint someone records). Each join has 30 ms audio fades, so it doesn't click.
"""

import sys
from pathlib import Path

import media

TAKES = Path(__file__).resolve().parents[2] / "video" / "takes"
FOOTAGE = Path("C:/Users/hussa/dev/footage/Video")
FADE_S = 0.03
BODY_KEEP = [(7.0, 11.50), (13.98, 28.54), (29.30, 39.40), (40.37, 45.20)]
"""Seconds of Video3_animation.mp4 to keep, in order."""


def graph(keep: list[tuple[float, float]]) -> str:
    """The filter that joins the kept ranges of input 0's video and audio."""
    chains = []
    for i, (start, end) in enumerate(keep):
        chains.append(f"[0:v]trim={start}:{end},setpts=PTS-STARTPTS[v{i}]")
        chains.append(
            f"[0:a]atrim={start}:{end},asetpts=PTS-STARTPTS,afade=t=in:d={FADE_S},"
            f"afade=t=out:st={end - start - FADE_S:.3f}:d={FADE_S}[a{i}]"
        )
    pairs = "".join(f"[v{i}][a{i}]" for i in range(len(keep)))
    chains.append(f"{pairs}concat=n={len(keep)}:v=1:a=1[v][a]")
    return ";".join(chains)


def main() -> None:
    footage = Path(sys.argv[1]) if len(sys.argv) > 1 else FOOTAGE
    TAKES.mkdir(parents=True, exist_ok=True)
    encode = ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-r", "30", "-c:a", "aac"]
    encode += ["-b:a", "192k", "-ar", "48000", "-ac", "2"]
    media.ffmpeg(
        "-i",
        str(footage / "Video3_animation.mp4"),
        "-filter_complex",
        graph(BODY_KEEP),
        "-map",
        "[v]",
        "-map",
        "[a]",
        *encode,
        str(TAKES / "body_round.mp4"),
    )
    media.ffmpeg(
        "-i", str(footage / "intro_first-6sec.mp4"), "-t", "6", *encode, str(TAKES / "intro.mp4")
    )
    media.ffmpeg("-i", str(footage / "Video_last.mp4"), *encode, str(TAKES / "whiteboard.mp4"))


if __name__ == "__main__":
    main()
