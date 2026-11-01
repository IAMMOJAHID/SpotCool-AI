# Credits

Ledger of every media asset in the SpotCool demo and video. Add one row per asset when you add the asset, and fill in every column. Media files live outside the repo in the footage directory (`$SPOTCOOL_WORLD_FOOTAGE_DIR`, default `C:/Users/hussa/dev/footage`, shown as `footage/` below). They are never committed. WP10 adds the software licences later.

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
