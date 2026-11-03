"""Index WiseNET footage by SpotCool camera and scene, for WP4's dataset adapter.

    python3 scripts/footage/wisenet_index.py WISENET_DIR [--out OUT_DIR] [--mapping MAPPING_YAML]

WISENET_DIR holds the extracted part of WiseNET's data.zip, in any folder layout: the videos
(video<set>_<camera>.avi) and the JSON annotation files. For every zone in
docs/footage/wisenet_mapping.yaml, the script takes the mapped camera's videos and counts
the annotated people in each second. Per scene, it keeps the longest run of seconds with
0 people (scene empty), 1-2 (few) or 4 or more (many, meeting), if the run lasts at least
MIN_S, cut to MAX_S. It writes OUT_DIR/index.yaml and a normalized copy of every video the
index uses (normalize.normalize_clip on the whole video, so the times stay valid). OUT_DIR
defaults to $SPOTCOOL_WORLD_FOOTAGE_DIR/datasets/wisenet.

People are counted from the manual detection files (PD-MAN): one box per clearly visible
person per frame, with a person id; frames without boxes are left out of the file. The boxes
count what the camera shows, and frame numbers map straight onto video time. The space-level
tracking file (PT-MAN) is not used: it counts people the camera may not see, and its
timestamps would need aligning with the video. Files with automatic detections (YOLO, SSD,
HOG_SVM) are skipped.

Index format, version 1. WP4's dataset adapter reads it with yaml.safe_load:

    version: 1
    dataset: wisenet
    cameras:
      cam-cor:
        empty: {file: "video5_4.mp4", start_s: 0.0, end_s: 12.0, people: 0}
        many: {file: "video5_4.mp4", start_s: 14.0, end_s: 40.0, people: 5}

- cameras: SpotCool camera id (spotcool_contracts ZONES[zone].camera_id), then scene name.
- file: path relative to index.yaml; H.264 1280x720 at 30 fps, no audio (normalize.py).
- start_s, end_s: the segment to loop, in seconds from the start of file.
- people: the most common per-second count of annotated people in the segment.
- A scene with no entry (complaint, door_open, or no long enough run) is not served by the
  dataset; the adapter falls back to our own clips or the synthetic source.
"""

import argparse
import json
import re
import shutil
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from normalize import FOOTAGE_DIR, SCENES, normalize_clip, probe, read_flow_yaml

MAPPING_FILE = Path(__file__).resolve().parents[2] / "docs" / "footage" / "wisenet_mapping.yaml"
VIDEO_NAME = re.compile(r"video(?P<set>\d+)_(?P<camera>\d+)\.avi")
PEOPLE: dict[str, tuple[int, int | None]] = {
    "empty": (0, 0),
    "few": (1, 2),
    "many": (4, None),
    "meeting": (4, None),
}
"""Inclusive people-count range each scene needs; None means no upper bound."""
MIN_S, MAX_S = 10, 40

Index = dict[str, dict[str, dict[str, Any]]]
"""camera id -> scene -> {"file", "start_s", "end_s", "people"}."""


def load_manual_annotations(root: Path) -> dict[str, list[dict[str, Any]]]:
    """Return video file name -> PD-MAN frames, from every manual detection JSON under root."""
    frames_by_video: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(root.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or "video" not in data or "frames" not in data:
            continue  # tracking (PT-MAN), calibration and other files
        frames = data["frames"]
        if any(d.get("imageAlgorithm") != "groundtruth" for f in frames for d in f["detections"]):
            continue  # automatic detections
        video = Path(data["video"]).name
        if len(frames) >= len(frames_by_video.get(video, [])):
            frames_by_video[video] = frames  # an empty file never hides one with boxes
    return frames_by_video


def people_per_second(frames: list[dict[str, Any]], fps: float, duration_s: float) -> list[int]:
    """Return the median number of annotated people in each whole second of a video."""
    per_frame = [0] * round(duration_s * fps)  # PD-MAN leaves out frames without boxes
    for frame in frames:
        if 0 <= frame["frameNumber"] < len(per_frame):
            per_frame[frame["frameNumber"]] = len({d["id"] for d in frame["detections"]})
    seconds = range(int(duration_s))
    return [
        statistics.median_low(per_frame[round(s * fps) : round((s + 1) * fps)]) for s in seconds
    ]


def best_run(counts: list[int], low: int, high: int | None) -> tuple[int, int] | None:
    """Return (start, end) seconds of the longest run with low <= count <= high, cut to MAX_S.

    Returns None when the longest run is shorter than MIN_S.
    """
    best, start = (0, 0), None
    for second, count in enumerate([*counts, -1]):  # -1 closes a run still open at the end
        inside = low <= count and (high is None or count <= high)
        if inside and start is None:
            start = second
        elif not inside and start is not None:
            if second - start > best[1] - best[0]:
                best = (start, second)
            start = None
    if best[1] - best[0] < MIN_S:
        return None
    return best[0], min(best[1], best[0] + MAX_S)


def pick_segments(videos: dict[int, list[tuple[str, list[int]]]], mapping: dict[str, Any]) -> Index:
    """Choose the longest qualifying segment for every mapped (SpotCool camera, scene).

    Args:
        videos: WiseNET camera number -> [(normalized file name, people per second)], in set order.
        mapping: zone -> {"camera": WiseNET camera number, ...}, from wisenet_mapping.yaml.
    """
    index: Index = {}
    for zone, target in mapping.items():
        for scene in (s for s in SCENES[zone] if s in PEOPLE):
            runs = [
                (run, file, counts)
                for file, counts in videos.get(target["camera"], [])
                if (run := best_run(counts, *PEOPLE[scene]))
            ]
            if not runs:
                continue
            (start, end), file, counts = max(runs, key=lambda r: r[0][1] - r[0][0])
            people = Counter(counts[start:end]).most_common(1)[0][0]
            entry = {"file": file, "start_s": float(start), "end_s": float(end), "people": people}
            index.setdefault(f"cam-{zone}", {})[scene] = entry
    return index


def write_index(path: Path, index: Index) -> None:
    """Write index.yaml in the version 1 format from the module docstring."""
    lines = [
        "# WiseNET index for the SpotCool camera system. Format: scripts/footage/wisenet_index.py.",
        "# Generated: rerun wisenet_index.py instead of editing.",
        "version: 1",
        "dataset: wisenet",
        "cameras:" if index else "cameras: {}",
    ]
    for camera_id, scenes in sorted(index.items()):
        lines.append(f"  {camera_id}:")
        for scene, e in sorted(scenes.items()):
            fields = f"file: {json.dumps(e['file'])}, start_s: {e['start_s']}, end_s: {e['end_s']}"
            lines.append(f"    {scene}: {{{fields}, people: {e['people']}}}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("wisenet_dir", type=Path)
    parser.add_argument("--out", type=Path, default=FOOTAGE_DIR / "datasets" / "wisenet")
    parser.add_argument("--mapping", type=Path, default=MAPPING_FILE)
    args = parser.parse_args(argv)
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        sys.exit("ffmpeg and ffprobe must be on PATH")
    mapping = read_flow_yaml(args.mapping)
    for zone, target in mapping.items():
        if zone not in SCENES or not isinstance(target.get("camera"), int):
            sys.exit(f"{args.mapping}: {zone}: need a SpotCool zone and an integer camera number")

    annotations = load_manual_annotations(args.wisenet_dir)
    found = sorted(
        ((int(m["set"]), int(m["camera"])), path)
        for path in args.wisenet_dir.rglob("*.avi")
        if (m := VIDEO_NAME.fullmatch(path.name))
    )
    sources: dict[str, Path] = {}
    videos: dict[int, list[tuple[str, list[int]]]] = {}
    for (_, camera), path in found:
        if path.name not in annotations:
            print(f"{path.name}: no manual annotations, skipped")
            continue
        info = probe(path)
        stream = next(s for s in info["streams"] if s["codec_type"] == "video")
        num, den = stream["r_frame_rate"].split("/")
        counts = people_per_second(
            annotations[path.name], int(num) / int(den), float(info["format"]["duration"])
        )
        sources[f"{path.stem}.mp4"] = path
        videos.setdefault(camera, []).append((f"{path.stem}.mp4", counts))

    index = pick_segments(videos, mapping)
    for file in sorted({e["file"] for scenes in index.values() for e in scenes.values()}):
        normalize_clip(sources[file], args.out / file)
    write_index(args.out / "index.yaml", index)
    for zone in mapping:
        for scene in (s for s in SCENES[zone] if s in PEOPLE):
            e = index.get(f"cam-{zone}", {}).get(scene)
            found_text = (
                f"{e['file']} {e['start_s']}-{e['end_s']} s, {e['people']} people" if e else "none"
            )
            print(f"cam-{zone} {scene}: {found_text}")
    print(args.out / "index.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
