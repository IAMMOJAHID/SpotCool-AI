#!/usr/bin/env bash
# Build the WiseNET clip set inside a GitHub Codespace. Read wisenet_codespace.md first.
#
#   bash scripts/footage/wisenet_codespace.sh                     # sets 1 2 5 11
#   SETS="5 11" bash scripts/footage/wisenet_codespace.sh         # other sets
#   WISENET_URL=<data.zip link> bash scripts/footage/wisenet_codespace.sh   # if the 4TU lookup fails
#
# 1. Downloads WiseNET's data.zip (10.7 GB) once, resuming if interrupted, and checks its MD5.
# 2. Extracts only the chosen sets' videos (video<set>_<camera>.avi) plus every JSON file.
# 3. Prints where each camera is, to check the assumptions in docs/footage/wisenet_mapping.yaml.
# 4. Runs wisenet_index.py, which writes $WORK/wisenet/index.yaml and the normalized videos.
set -euo pipefail

SETS="${SETS:-1 2 5 11}"
WORK="${WORK:-/workspaces/wisenet}"  # on the codespace's 32 GB disk, outside the repo checkout
DATASET=5f790109-8369-4207-a3ee-35174c144483
MD5=57c3035ce10b5d2c9bc7e41572318659  # data.zip, 10,713,417,760 bytes (4TU record)
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ZIP="$WORK/data.zip"

sudo apt-get update -qq
sudo apt-get install -y -qq ffmpeg > /dev/null
mkdir -p "$WORK"

if ! echo "$MD5  $ZIP" | md5sum --check --status 2> /dev/null; then
  URL="${WISENET_URL:-$(curl -fsS "https://data.4tu.nl/v2/articles/$DATASET/files" \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)[0]["download_url"])')}"
  curl -fL --retry 5 -C - -o "$ZIP" "$URL"
  echo "$MD5  $ZIP" | md5sum --check
fi

# Python's zipfile instead of unzip: unzip builds differ on whether * matches "/", and
# zipfile.extract drops ".." and absolute parts from member names.
python3 - "$ZIP" "$WORK" $SETS << 'EOF'
import json, pathlib, re, sys, zipfile
zip_path, work, *sets = sys.argv[1:]
video = re.compile(r"(^|/)video(%s)_\d+\.avi$" % "|".join(sets))
with zipfile.ZipFile(zip_path) as archive:
    names = archive.namelist()
    pathlib.Path(work, "contents.txt").write_text("\n".join(names) + "\n")  # check here if a set is missing
    chosen = [n for n in names if n.endswith(".json") or video.search(n)]
    for name in chosen:
        archive.extract(name, pathlib.Path(work, "data"))
print(f"Extracted {len(chosen)} of {len(names)} files; set videos found:",
      sorted({m[2] for n in chosen if (m := video.search(n))}))
print("Camera locations (calibration files):")
for path in sorted(pathlib.Path(work, "data").rglob("*.json")):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, ValueError):
        continue
    if isinstance(data, dict) and "regionsOfInterest" in data:
        doors = [roi.get("represents") for roi in data["regionsOfInterest"]]
        print(" ", data.get("deviceID"), data.get("location", data.get("isHostedBy")), doors)
EOF

python3 "$REPO/scripts/footage/wisenet_index.py" "$WORK/data" --out "$WORK/wisenet"
du -sh "$WORK/wisenet"
