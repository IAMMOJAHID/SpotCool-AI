# Credits

Ledger of every media asset in the SpotCool demo and video, and of the software the world and the dashboard are built with. Add one row per asset when you add the asset, and fill in every column. Media files live outside the repo in the footage directory that `$SPOTCOOL_WORLD_FOOTAGE_DIR` names (on this laptop `C:/Users/hussa/dev/footage`, shown as `footage/` below; the world has no default and draws synthetic frames without it). They are never committed.

## Media

| File | What | Source URL | Licence | Attribution | Added by |
|---|---|---|---|---|---|
| `footage/clips/<zone>_<scene>.mp4` (the 12 clips in `docs/footage/shot_list.md`), `footage/audio/south_complaint.wav`, `footage/raw/phone/live_backup.mp4` | Own recordings of consenting team members: zone scenes, the meeting-room complaint, the live phone segment | Filmed by the team, 2 November 2026 | Own work, SpotCool team | SpotCool team, consent on file (`docs/footage/consent.md`; signed sheets kept outside the repo) | WP8 |
| `footage/datasets/wisenet/` (`index.yaml`, `video<set>_<camera>.mp4`) | WiseNET multi-camera indoor dataset, I3M building, Dijon: selected sets, normalized (`scripts/footage/wisenet_codespace.md`) | <https://data.4tu.nl/datasets/5f790109-8369-4207-a3ee-35174c144483> (doi:10.4121/uuid:c1fb5962-e939-4c51-bfd5-eac6f2935d44) | CC0 1.0, from the record's citation metadata. Confirm on the 4TU page when downloading, and note the date here. | Not required. We credit: "WiseNET dataset by R. Marroquin, J. Dubois and C. Nicolle, 4TU.ResearchData, 2019." | WP8 |
| `footage/datasets/meva/<clip>.mp4`: one row per clip used. None fetched yet; candidates are in `scripts/footage/meva_fetch.py`. | MEVA (Multiview Extended Video with Activities), indoor camera clips, trimmed and re-encoded to 1280x720 H.264 | <https://registry.opendata.aws/mevadata/> (s3://mevadata-public-01) | CC BY 4.0 (<http://mevadata.org/resources/MEVA-data-license.txt>) | "Multiview Extended Video with Activities" (MEVA) dataset by Kitware Inc. and the Intelligence Advanced Research Projects Activity (IARPA), licensed under CC BY 4.0. Accessed on `<date>` from <https://registry.opendata.aws/mevadata>. Changes: trimmed and re-encoded. | WP8 |
| `video/` b-roll: one row per clip used, none chosen yet | Pexels stock video | `https://www.pexels.com/video/<id>/` per clip; licence at <https://www.pexels.com/license/> | Pexels licence: free to use and modify. Don't sell unaltered copies, imply endorsement, or show identifiable people in a bad light. | Not required. We credit: "Video by `<creator>` on Pexels." | WP8 (template), WP9 |

## Not allowed in the public video or repo

These datasets fail the licence rules for a public hackathon video, so don't use them, even as b-roll:

- **EPFL lab** multi-camera pedestrian videos: research use only.
- **CITIC-MTMC**: non-commercial only, and hackathon prize money may count as commercial use.
- **PIROPO**: research use only, released on request.

## Software

Read on 3 November 2026 (WP10), never from memory:

- **Python:** each version from `uv.lock`, each licence from the installed package's own metadata (`License-Expression`, else `License`, else its licence classifiers).
- **npm:** from `apps/dashboard/package-lock.json` and each installed package's `package.json`.
- **Programs and services:** from the licence file or text they install, as noted.

**Unverified** marks a licence we could not read on this machine. Our own packages, `spotcool-world` and `spotcool-contracts`, are BSD-2-Clause; the dashboard is MIT.

### Simulation and building data

| What | Version | Licence | Used for |
|---|---|---|---|
| [BOPTEST](https://github.com/ibpsa/project1-boptest), the `multizone_office_simple_air` test case | clone at `a29024f`, `1.0.0-dev` | BSD-3-Clause: the three clauses of the clone's `license.md`, © IBPSA and contributors | The building physics, run locally or in a GitHub Codespace |

### The world (Python), at run time

Each extra of `packages/world/pyproject.toml` pulls in the packages below, transitive ones included.

| Package | Version | Licence | Needed by |
|---|---|---|---|
| pydantic | 2.14.2 | MIT | core, contracts |
| pydantic-core | 2.50.2 | MIT | pydantic |
| pydantic-settings | 2.16.2 | MIT | core |
| annotated-types | 0.8.0 | MIT | pydantic |
| typing-extensions | 4.16.0 | PSF-2.0 | pydantic, FastAPI and others |
| typing-inspection | 0.4.4 | MIT | pydantic |
| python-dotenv | 1.2.4 | BSD-3-Clause | pydantic-settings, uvicorn |
| loguru | 0.7.3 | MIT (classifier) | core |
| win32-setctime | 1.2.1 | MIT | loguru, on Windows |
| colorama | 0.4.6 | BSD (classifier) | loguru, pytest, on Windows |
| PyYAML | 6.0.3 | MIT | core, contracts, uvicorn |
| BACpypes3 | 0.0.110 | MIT (`License-Expression`; its classifier says BSD) | `bacnet`: the BMS, the VRF gateway, the stub agent |
| FastAPI | 0.162.0 | MIT | `cameras`, `calendar`, `dashboard` |
| annotated-doc | 0.0.5 | MIT | FastAPI |
| opentelemetry-api | 1.45.2 | Apache-2.0 | FastAPI |
| Starlette | 1.10.0 | BSD-3-Clause | FastAPI |
| uvicorn (`standard`) | 0.54.0 | BSD-3-Clause | the HTTP and dashboard servers |
| h11 | 0.16.0 | MIT | uvicorn, httpcore |
| httptools | 0.9.0 | MIT | uvicorn |
| watchfiles | 1.3.0 | MIT | uvicorn |
| websockets | 17.4 | BSD-3-Clause | uvicorn: `/ws/events`, `/ws/agent` |
| uvloop | 0.23.0 | **Unverified**: not installed on Windows (uvicorn uses it on Linux and macOS) | uvicorn |
| click | 8.5.0 | BSD-3-Clause | uvicorn, towncrier |
| anyio | 4.15.1 | MIT | Starlette, httpx, watchfiles |
| idna | 3.20 | BSD-3-Clause | anyio, httpx |
| httpx | 0.28.1 | BSD-3-Clause | `boptest`, `cameras`, `occupants` |
| httpcore | 1.0.9 | BSD-3-Clause | httpx |
| certifi | 2026.10.23 | MPL-2.0 | httpx |
| NumPy | 2.4.6 (the lock also pins 2.5.4 for other Pythons) | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | `rc`, `cameras` |
| opencv-python-headless | 4.14.0.94 | Apache-2.0 | `cameras`: clips and snapshots |
| matplotlib | 3.11.2 | matplotlib licence (PSF-based; classifier Python Software Foundation License) | `report`: the charts |
| contourpy | 1.3.3 (the lock also pins 1.4.0) | BSD-3-Clause | matplotlib |
| cycler | 0.12.1 | BSD (classifier) | matplotlib |
| fonttools | 4.68.0 | MIT | matplotlib |
| kiwisolver | 1.5.1 | BSD (classifier) | matplotlib |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | matplotlib, pytest |
| pillow | 13.0.0 | MIT-CMU | matplotlib |
| pyparsing | 3.3.3 | MIT | matplotlib |
| python-dateutil | 4.0.0 | BSD-3-Clause | matplotlib |
| tzdata | 2026.6 | Apache-2.0 | python-dateutil |

### The world (Python), tooling only

The root `pyproject.toml`'s `dev` group; none of it ships.

| Package | Version | Licence | Used for |
|---|---|---|---|
| pytest | 8.4.2 | MIT | tests and evals |
| pytest-asyncio | 1.4.1 | Apache-2.0 | async tests |
| iniconfig | 2.3.1 | MIT | pytest |
| pluggy | 1.7.0 | MIT | pytest |
| Pygments | 2.21.0 | BSD-2-Clause | pytest |
| Ruff | 0.17.3 | MIT | lint and format |
| Pyright | 1.1.414 | MIT | type checks |
| nodeenv | 1.12.0 | BSD (classifier) | Pyright, pre-commit |
| towncrier | 25.8.0 | MIT | the changelog |
| Jinja2 | 3.1.6 | BSD (classifier) | towncrier |
| MarkupSafe | 3.0.4 | BSD-3-Clause | Jinja2 |
| pre-commit | 4.7.0 | MIT | git hooks |
| cfgv | 3.5.0 | MIT | pre-commit |
| identify | 2.6.20 | MIT | pre-commit |
| virtualenv | 21.15.2 | MIT | pre-commit |
| distlib | 0.4.4 | PSF-2.0 | virtualenv |
| filelock | 4.2.6 | MIT | virtualenv |
| platformdirs | 4.14.2 | MIT | virtualenv |
| python-discovery | 1.9.0 | MIT | virtualenv |

### The dashboard (npm)

Bundled into `apps/dashboard/dist/`, all MIT:

| Package | Version | Licence |
|---|---|---|
| react | 19.3.1 | MIT |
| react-dom | 19.3.1 | MIT |
| scheduler (react-dom) | 0.28.0 | MIT |
| chart.js | 4.6.1 | MIT |
| @kurkle/color (chart.js) | 0.3.4 | MIT |
| react-chartjs-2 | 5.3.1 | MIT |

Development only (the build, tests and lint):

| Package | Version | Licence |
|---|---|---|
| vite | 8.3.5 | MIT |
| @vitejs/plugin-react | 6.1.2 | MIT |
| typescript | 6.0.3 | Apache-2.0 |
| eslint | 10.14.0 | MIT |
| @eslint/js | 10.0.1 | MIT |
| typescript-eslint | 8.72.1 | MIT |
| eslint-plugin-react-hooks | 7.1.1 | MIT |
| globals | 17.14.0 | MIT |
| vitest | 5.0.5 | MIT |
| @playwright/test | 1.64.1 | Apache-2.0 |
| json-schema-to-typescript | 16.0.0 | MIT |
| ws | 8.22.1 | MIT |
| @types/node, @types/react, @types/react-dom | 24.19.2, 19.3.0, 19.3.0 | MIT |

The lock holds 218 packages in all:

- 165 MIT, 19 Apache-2.0 and 10 ISC;
- 6 BSD-2-Clause and 2 BSD-3-Clause;
- 12 MPL-2.0: lightningcss 1.33.0 and its per-platform binaries, used by Vite;
- 1 each of CC-BY-4.0 (caniuse-lite 1.0.30001820, browser data), Python-2.0 (argparse 2.0.1), PSF-2.0 (argparse 3.0.4) and BlueOak-1.0.0 (minimatch 10.2.6).

Only the six bundled packages above ship in `dist/`.

### Programs and services

| What | Version | Licence | Used for |
|---|---|---|---|
| CPython (uv-managed) | 3.11.11 | PSF License Agreement (its `LICENSE.txt`) | the world, the scripts, the tests |
| uv | 0.6.5 | **Unverified**: the standalone install carries no licence file | the Python workspace |
| Node.js | 24.4.1 | **Unverified**: the Windows install carries no licence file | the dashboard's build and tests |
| npm | 11.6.2 (`npm --version`; the global install under `%APPDATA%/npm`, ahead of Node's bundled 11.4.2) | Artistic-2.0 (its `package.json`) | the dashboard's dependencies |
| Microsoft Edge | installed | **Unverified**: proprietary, not redistributed | the dashboard's Playwright tests, locally |
| YABE (Yet Another BACnet Explorer) | | **Unverified** | checking the BMS and the VRF by hand (`docs/runbooks/yabe.md`) |
| IP Webcam (Android, by Pavel Khlebovich) | | **Unverified**: a free app from Google Play | the live phone camera |
| GitHub Actions, GitHub Codespaces | | GitHub's terms; free for this public repo, and 120 core-hours a month | CI; the BOPTEST stack |

### Video tools (WP9, as planned)

| What | Version | Licence | Used for |
|---|---|---|---|
| OBS Studio | installed in `C:\Program Files\obs-studio` | GPL-2.0 (its `data/obs-studio/license/gplv2.txt`) | recording the takes |
| FFmpeg | 8.0.1 | GPL-3.0-or-later as built here (`ffmpeg -L`; built with `--enable-gpl --enable-version3`). FFmpeg itself is LGPL-2.1-or-later without those options. | footage normalizing, audio extraction, the edit |
| HyperFrames | | **Unverified**: Apache-2.0 per `docs/status/HANDOFF.md` section 6; not installed in this repo | motion graphics |
| Kokoro-82M, faster-whisper, Shotcut or Kdenlive | | **Unverified**: Apache-2.0, MIT and GPL per `docs/status/HANDOFF.md` section 6; none installed yet | narration, captions, the edit |
