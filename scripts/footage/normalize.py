"""Normalize filmed clips for the SpotCool camera system.

    uv run python scripts/footage/normalize.py [RAW_DIR] [CLIPS_DIR] [--cuts CUTS_YAML]

Every video at the top level of RAW_DIR (.mp4, or .mov from an iPhone) must be named
<zone>_<scene> after an entry in SCENES. Each one becomes CLIPS_DIR/<zone>_<scene>.mp4:
H.264 1280x720 at 30 fps, yuv420p, no audio, no metadata (phones store GPS), faststart, cut
to its in/out points from docs/footage/cuts.yaml or kept whole. RAW_DIR and CLIPS_DIR default
to raw/ and clips/ under $SPOTCOOL_WORLD_FOOTAGE_DIR (default C:/Users/hussa/dev/footage).

Uses only the standard library plus ffmpeg/ffprobe on PATH, so it also runs in a bare
Codespace. The other footage scripts import normalize_clip, probe and read_flow_yaml from here.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

FOOTAGE_DIR = Path(os.environ.get("SPOTCOOL_WORLD_FOOTAGE_DIR", "C:/Users/hussa/dev/footage"))
CUTS_FILE = Path(__file__).resolve().parents[2] / "docs" / "footage" / "cuts.yaml"

SCENES: dict[str, tuple[str, ...]] = {
    "cor": ("empty", "few", "many"),
    "nor": ("empty", "few"),
    "sou": ("empty", "meeting", "complaint"),
    "eas": ("empty",),
    # wes/empty is missing from WP5's scene list, but West is empty at 07:00 and after 18:00.
    "wes": ("empty", "few", "door_open"),
}
"""Scenes per zone (spotcool_contracts.zones.Zone values) the scenario library uses."""

VIDEO_SUFFIXES = (".mp4", ".mov")
WIDTH, HEIGHT, FPS = 1280, 720, 30
_VIDEO_FILTER = (
    f"fps={FPS},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
    f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1"
)
# -g 30: a keyframe every second, so camera readers can seek to an index's start_s quickly.
_ENCODE_ARGS = (
    "-map 0:v:0 -an -map_metadata -1 -map_chapters -1 "
    f"-vf {_VIDEO_FILTER} -c:v libx264 -preset veryfast -crf 23 -g 30 "
    "-pix_fmt yuv420p -movflags +faststart"
).split()


def clip_names() -> list[str]:
    """Return every valid clip file name, in SCENES order."""
    return [f"{zone}_{scene}.mp4" for zone, scenes in SCENES.items() for scene in scenes]


def clip_name(raw: Path) -> str:
    """Return the clip file name for a raw video.

    Raises:
        ValueError: If the name is not <zone>_<scene>.mp4 or .mov for a scene in SCENES.
    """
    zone, _, scene = raw.stem.partition("_")
    if raw.suffix.lower() not in VIDEO_SUFFIXES or scene not in SCENES.get(zone, ()):
        raise ValueError(f"{raw.name}: expected <zone>_<scene>.mp4 (or .mov) from the shot list")
    return f"{zone}_{scene}.mp4"


_ENTRY = re.compile(r"(?P<key>[\w.-]+):\s+\{(?P<body>[^{}#]*)\}(?:\s+#.*)?")
_NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?")  # no leading zeros: YAML 1.1 reads 012 as octal
_WORD = re.compile(r"[A-Za-z][\w.-]*")
_YAML_WORDS = {"yes", "no", "true", "false", "on", "off", "null"}  # PyYAML reads these as bool/None


def read_flow_yaml(path: Path) -> dict[str, dict[str, int | float | str]]:
    """Read the YAML subset the footage files use: one ``key: {field: value, ...}`` per line.

    Values are plain numbers or bare words; blank lines and ``#`` comments are skipped. Anything
    outside the subset raises ValueError instead of being read differently from PyYAML.
    """
    entries: dict[str, dict[str, int | float | str]] = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _ENTRY.fullmatch(line.rstrip())
        if not match or match["key"] in entries:
            raise ValueError(f"{path}:{n}: expected one new 'key: {{field: value}}' line: {line!r}")
        fields: dict[str, int | float | str] = {}
        for item in match["body"].split(","):
            name, _, value = (part.strip() for part in item.partition(": "))
            word = _WORD.fullmatch(value) and value.lower() not in _YAML_WORDS
            if not _WORD.fullmatch(name) or not (_NUMBER.fullmatch(value) or word):
                raise ValueError(f"{path}:{n}: bad field {item.strip()!r}")
            if _NUMBER.fullmatch(value):
                fields[name] = float(value) if "." in value else int(value)
            else:
                fields[name] = value
        entries[match["key"]] = fields
    return entries


def read_cuts(path: Path) -> dict[str, tuple[float, float | None]]:
    """Return clip name -> (in_s, out_s) from cuts.yaml; a missing file means no cuts.

    Raises:
        ValueError: If an entry is not a known clip name with 0 <= in < out seconds.
    """
    if not path.exists():
        return {}
    cuts: dict[str, tuple[float, float | None]] = {}
    for name, fields in read_flow_yaml(path).items():
        numbers = all(isinstance(v, int | float) for v in fields.values())
        if name not in clip_names() or not set(fields) <= {"in", "out"} or not numbers:
            raise ValueError(f"{path}: bad cut {name}: {fields}")
        start = float(fields.get("in", 0))
        end = float(fields["out"]) if "out" in fields else None
        if start < 0 or (end is not None and end <= start):
            raise ValueError(f"{path}: {name} needs 0 <= in < out, got {fields}")
        cuts[name] = (start, end)
    return cuts


def normalize_clip(
    src: Path | str, dst: Path, start_s: float = 0.0, end_s: float | None = None
) -> Path:
    """Re-encode one video into the camera format described in the module docstring.

    Args:
        src: Any video ffmpeg reads: a path, or a URL (meva_fetch.py streams from S3).
        dst: Output .mp4 path. Its directory is created; the file appears only when complete.
        start_s: Second of src to start at.
        end_s: Second of src to stop at; None keeps the rest.

    Returns:
        dst.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(f"{dst.stem}.part.mp4")
    trim = ["-ss", str(start_s)] + (["-to", str(end_s)] if end_s is not None else [])
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *trim, "-i", str(src)]
    subprocess.run([*cmd, *_ENCODE_ARGS, str(part)], check=True)
    part.replace(dst)
    return dst


def probe(path: Path) -> dict[str, Any]:
    """Return ffprobe's description of a media file: ``{"streams": [...], "format": {...}}``."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("raw_dir", nargs="?", type=Path, default=FOOTAGE_DIR / "raw")
    parser.add_argument("clips_dir", nargs="?", type=Path, default=FOOTAGE_DIR / "clips")
    parser.add_argument("--cuts", type=Path, default=CUTS_FILE)
    args = parser.parse_args(argv)
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is not on PATH")
    if not args.raw_dir.is_dir():
        sys.exit(f"{args.raw_dir} is not a directory")

    raws: dict[str, Path] = {}
    errors: list[str] = []
    for raw in sorted(args.raw_dir.iterdir()):
        if not raw.is_file() or raw.suffix.lower() not in VIDEO_SUFFIXES:
            continue  # voice memos, notes and the phone/ backup are not scenario clips
        try:
            name = clip_name(raw)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if name in raws:
            errors.append(f"{raw.name}: {raws[name].name} already becomes {name}")
        raws[name] = raw
    if errors:
        print(
            "\n".join(errors), f"Valid names: {', '.join(clip_names())}", sep="\n", file=sys.stderr
        )
        return 1

    cuts = read_cuts(args.cuts)
    for name, raw in raws.items():
        start, end = cuts.get(name, (0.0, None))
        normalize_clip(raw, args.clips_dir / name, start, end)
        print(f"{raw.name} -> {args.clips_dir / name}")
    missing = [name for name in clip_names() if name not in raws]
    if missing:
        print(f"Not filmed yet: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
