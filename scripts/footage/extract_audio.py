"""Cut a spoken complaint into the WAV format the agent's POST /voice expects.

    uv run python scripts/footage/extract_audio.py SRC [--start S] [--end S] [--name NAME] [--out-dir DIR]

SRC is a raw clip or a separate phone recording (any format ffmpeg reads). The output is
OUT_DIR/NAME.wav: 16 kHz, mono, signed 16-bit little-endian PCM, which is
spotcool_contracts.occupants.VOICE_FORMAT. NAME defaults to SRC's stem; OUT_DIR defaults to
$SPOTCOOL_WORLD_FOOTAGE_DIR/audio, outside the repo. Example:

    uv run python scripts/footage/extract_audio.py raw/south_complaint.m4a --start 2.0 --end 5.5
"""

import argparse
import subprocess
import sys
from pathlib import Path

from normalize import FOOTAGE_DIR

# Must equal spotcool_contracts.occupants.VOICE_FORMAT; tests/test_extract_audio.py checks it.
SAMPLE_RATE_HZ = 16_000
CHANNELS = 1
CODEC = "pcm_s16le"
_ENCODE_ARGS = (
    f"-map 0:a:0 -map_metadata -1 -ac {CHANNELS} -ar {SAMPLE_RATE_HZ} -c:a {CODEC}".split()
)


def extract_audio(src: Path, dst: Path, start_s: float = 0.0, end_s: float | None = None) -> Path:
    """Write the first audio stream of src, from start_s to end_s, to dst as a VOICE_FORMAT WAV.

    Returns:
        dst.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    trim = ["-ss", str(start_s)] + (["-to", str(end_s)] if end_s is not None else [])
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *trim, "-i", str(src)]
    subprocess.run([*cmd, *_ENCODE_ARGS, str(dst)], check=True)
    return dst


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("src", type=Path)
    parser.add_argument("--start", type=float, default=0.0, help="seconds into SRC (default 0)")
    parser.add_argument("--end", type=float, help="seconds into SRC to stop at (default: the end)")
    parser.add_argument("--name", help="output name without .wav (default: SRC's stem)")
    parser.add_argument("--out-dir", type=Path, default=FOOTAGE_DIR / "audio")
    args = parser.parse_args(argv)
    if args.start < 0 or (args.end is not None and args.end <= args.start):
        parser.error("need 0 <= --start < --end")
    dst = extract_audio(
        args.src, args.out_dir / f"{args.name or args.src.stem}.wav", args.start, args.end
    )
    print(dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
