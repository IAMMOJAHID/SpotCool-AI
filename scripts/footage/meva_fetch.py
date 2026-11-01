"""Fetch named MEVA clips from the public S3 bucket and normalize them.

    uv run python scripts/footage/meva_fetch.py KEY [KEY ...] [--start S] [--end S] [--out OUT_DIR]

KEY is an object key in s3://mevadata-public-01. ffmpeg reads each clip straight from
https://mevadata-public-01.s3.amazonaws.com/<KEY> (plain HTTPS, no credentials) and seeks
with range requests, so only the part between --start and --end is fetched and nothing but
the normalized OUT_DIR/<clip>.mp4 is stored (default $SPOTCOOL_WORLD_FOOTAGE_DIR/datasets/meva).
MEVA clips are 5 minutes long and 70-170 MB; trim to the 30-40 s you need.

Choosing clips. CANDIDATE_KEYS lists indoor clips found on 2026-11-01 with
`aws s3 ls --no-sign-request s3://mevadata-public-01/drops-123-r13/ --recursive`. Key names
read <date>/<hour>/<start>.<end>.<site>.<camera>.r13.avi. The bucket's
phase2-known-facility-site-map.pdf shows which cameras are indoors: school G299 and G330 (gym),
G421 (cafe), G419 and G420 (entrance doors), G423 (hall); bus G331 and G507 (waiting room);
admin G326 and G329 (hallway). G508 and G509 are classrooms only when the key says "school";
under "bus" they film the subway. Watch a candidate before using it (for example
`ffplay -ss 60 https://mevadata-public-01.s3.amazonaws.com/<KEY>`).

To serve a clip from WP4's dataset adapter, add it by hand to datasets/meva/index.yaml in the
format documented in wisenet_index.py, with the people count you see. MEVA is CC BY 4.0: add
a CREDITS.md row for every clip you use.
"""

import argparse
import shutil
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from normalize import FOOTAGE_DIR, normalize_clip

BUCKET_URL = "https://mevadata-public-01.s3.amazonaws.com/"
_DIR = "drops-123-r13/2018-03-11/11/2018-03-11."
CANDIDATE_KEYS = {
    f"{_DIR}11-15-00.11-20-00.school.G421.r13.avi": "school cafe: tables, people sitting",
    f"{_DIR}11-15-00.11-20-00.school.G299.r13.avi": "school gym (large hall)",
    f"{_DIR}11-15-00.11-20-00.school.G330.r13.avi": "school gym, second view",
    f"{_DIR}11-15-00.11-20-00.school.G419.r13.avi": "school entrance doors, inside",
    f"{_DIR}11-15-00.11-20-00.school.G420.r13.avi": "school entrance doors, second view",
    f"{_DIR}11-20-00.11-25-00.bus.G331.r13.avi": "bus station waiting room with benches",
    f"{_DIR}11-20-01.11-25-01.admin.G326.r13.avi": "admin hallway with a door",
}
"""Indoor candidates: key -> what the camera shows (per the bucket's site map)."""


def clip_url(key: str) -> str:
    """Return the anonymous HTTPS URL of a bucket key."""
    return BUCKET_URL + quote(key)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("keys", nargs="+", metavar="KEY")
    parser.add_argument("--start", type=float, default=0.0, help="seconds into each clip")
    parser.add_argument("--end", type=float, help="seconds into each clip to stop at")
    parser.add_argument("--out", type=Path, default=FOOTAGE_DIR / "datasets" / "meva")
    args = parser.parse_args(argv)
    if args.start < 0 or (args.end is not None and args.end <= args.start):
        parser.error("need 0 <= --start < --end")
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is not on PATH")
    for key in args.keys:
        dst = args.out / f"{PurePosixPath(key).stem}.mp4"
        normalize_clip(clip_url(key), dst, args.start, args.end)
        print(f"{key} -> {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
