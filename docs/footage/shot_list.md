# Shot list: own footage (filming 2 November, morning)

Print this page in landscape and take it to the shoot. There is one row per clip. File names must match exactly, because `scripts/footage/normalize.py` rejects any other name.

## Before the first take

- **Consent.** Everyone in frame has signed [consent.md](consent.md). No strangers, no screens showing content, no logos, no name badges.
- **Phone settings.** Landscape, 720p, 30 fps. Put the phone in flight mode or do-not-disturb. Keep the room lights on.
  - Android: Camera, then Settings, then Video size: HD 720p, 30 fps.
  - iPhone: Settings, then Camera. Set Record Video to 720p HD at 30 fps and Formats to Most Compatible.
- **Mounting.** Fix the phone 2 m or higher in a corner (on a shelf, a cupboard top or an extended tripod), tilted down like a CCTV camera. Don't touch it during a take.
- **One position per zone.** Film all clips of a zone back to back without moving the phone, so a scene switch looks like the same CCTV camera.
- **Every take.**
  1. Start recording and wait 2 s.
  2. Act out the row's action.
  3. Keep going to 35 s (30-40 s is fine), then stop.
  4. If the start or the end is messy, note the good in and out seconds for `cuts.yaml`.

## Cast and wardrobe

Use role letters only: no real names on screen, on paper in frame, or in file names.

| Role | On screen in |
|---|---|
| A | `cor_few`, `cor_many`, `sou_meeting`, `sou_complaint` (speaks), live phone segment (speaks) |
| B | `cor_many`, `nor_few`, `sou_meeting`, `sou_complaint`, `wes_door_open` |
| C | `cor_many`, `nor_few`, `sou_meeting`, `sou_complaint`, `wes_door_open` |
| D | `cor_many`, `sou_meeting`, `sou_complaint`, `eas_few` |
| E | `sou_meeting`, `sou_complaint`, `wes_few`, `wes_door_open`, `eas_few` |

- **Cast size.** At least 4 people, ideally 5. With fewer, film everyone you have and write the real count in the People column. The scenario (WP5) must use the count that is on screen.
- **Wardrobe.** Everyone wears light clothes (T-shirt or shirt). A brings a jacket and hangs it on A's chair in the meeting room.
- **Laptops.** Screens face away from the camera. Cover logo stickers.

## Clips

| # | File name | Zone (room) | Camera position | Framing | Actors | People | Action | Duration |
|---|---|---|---|---|---|---|---|---|
| 1 | `cor_empty.mp4` | Core: open workspace | **C:** high corner, looking diagonally across the room | Wide: all desks and chairs, floor visible, no window glare | none | 0 | Empty desks, chairs pushed in. Nothing moves. | 35 s |
| 2 | `cor_few.mp4` | Core | C (unchanged) | Same as 1 | A | 1 | A works on a laptop at a desk near the middle of the frame for the whole take. | 35 s |
| 3 | `cor_many.mp4` | Core | C (unchanged) | Same as 1 | A, B, C, D | 4 | Four people at desks on laptops: typing, sipping, small natural movements. Nobody leaves the frame. | 35 s |
| 4 | `nor_empty.mp4` | North: team area (a second room, or a clearly different corner of a large room) | **N:** high corner | Wide: 2-3 desks | none | 0 | Empty desks. | 35 s |
| 5 | `nor_few.mp4` | North | N (unchanged) | Same as 4 | B, C | 2 | B and C work at neighbouring desks and talk quietly. Both stay in frame. | 35 s |
| 6 | `sou_empty.mp4` | South: meeting room | **S:** high corner opposite the door | Whole table and all chairs; door at the edge of the frame | none | 0 | Empty room, chairs tucked in, lights on. | 35 s |
| 7 | `sou_meeting.mp4` | South | S (unchanged) | Same as 6 | A, B, C, D, E | 5 | Five people seated round the table. One presents with gestures; the others listen and take notes. A wears a T-shirt, with A's jacket on the back of A's chair. | 35 s |
| 8 | `sou_complaint.mp4` | South | S (unchanged) | Same as 6 | A, B, C, D, E | 5 | Same seats as 7. 0-5 s: the meeting runs. 5 s: A rubs both arms. 10 s: A says, loud and clear, *"It's freezing in this meeting room."* 15 s: A puts on the jacket. To 35 s: the meeting carries on. Also record the audio (see below). | 35 s |
| 9 | `eas_empty.mp4` | East: any room (the team works from home today) | **E:** high corner | Wide: desks or tables | none | 0 | Empty room, lights on. | 35 s |
| 10 | `wes_empty.mp4` | West: entrance with a door | **W:** high corner facing the door | The door and a reception desk (or a table by the door) | none | 0 | Door closed, desk empty. | 35 s |
| 11 | `wes_few.mp4` | West | W (unchanged) | Same as 10 | E | 1 | E works at the reception desk. The door stays closed. | 35 s |
| 12 | `wes_door_open.mp4` | West | W (unchanged) | Same as 10 | E, B, C | 1 at the desk, plus 2 passing | 0-3 s: door closed, E at the desk. 3 s: B opens the door and props it open. 5-25 s: B and C carry 2-3 boxes in through the door and out again. Leave the door open to the end of the take; it must stay open for at least 20 s. | 35 s |
| 13 | Live phone segment (`cam-meeting-live`). Backup take: `raw/phone/live_backup.mp4` | South meeting room, or any room with 4-5 m to walk | Phone on a stand about 2 m up, landscape, streaming with the IP Webcam app (WP4 setup). A second phone records the backup take. | Wide enough for the whole walk: entry on the left, a chair far away on the right | A | 1 | A enters at the left edge and walks right. A sits on the far chair (about 4 m from the camera) for 5 s, gets up, walks to about 1 m from the camera and says *"The AC is blowing right on my face."* Optional second take: *"Can someone turn up the AC?"* | 30-40 s |
| 14 | `eas_few.mp4` | East | E (unchanged) | Same as 9 | D, E | 2 | D and E work at neighbouring desks on laptops, typing and talking quietly. Both stay in frame for the whole take. Only the `all_hands_day` scenario uses it: it checks that savings are about 0 when every zone is occupied. | 35 s |

Row 10 (`wes_empty`) is missing from the scenario's scene list, but the timeline needs it: West is empty from 07:00 to 08:30 and after 18:00.

### Complaint audio (row 8)

1. Put a second phone on the table near A, running a voice recorder, and start it before the take.
2. After the take, A says the line three more times with a 2 s pause between them. This gives clean takes to choose from.
3. Save the recording as `raw/south_complaint.m4a` (any audio format works).

## Where the clips play (`hybrid_tuesday`, sim time)

| Sim time | Core | North | South | East | West |
|---|---|---|---|---|---|
| 07:00 | `cor_empty` | `nor_empty` | `sou_empty` | `eas_empty` | `wes_empty` |
| 08:30 | `cor_many` | `nor_few` | `sou_empty` | `eas_empty` | `wes_few` |
| 10:00-10:45 | `cor_many` | `nor_few` | `sou_empty` | `eas_empty` | `wes_door_open` |
| 10:45 | `cor_many` | `nor_few` | `sou_empty` | `eas_empty` | `wes_few` |
| 12:30-13:30 (lunch) | `cor_few` | `nor_empty` | `sou_empty` | `eas_empty` | `wes_few` |
| 13:30 | `cor_many` | `nor_few` | `sou_empty` | `eas_empty` | `wes_few` |
| 14:00 | `cor_many` | `nor_few` | `sou_meeting` | `eas_empty` | `wes_few` |
| 14:15-15:00 (cue at 14:15) | `cor_many` | `nor_few` | `sou_complaint`, plus the voice cue `south_complaint.wav` | `eas_empty` | `wes_few` |
| 15:00 | `cor_many` | `nor_few` | `sou_empty` | `eas_empty` | `wes_few` |
| 17:30 | `cor_few` | `nor_few` | `sou_empty` | `eas_empty` | `wes_few` |
| 18:00 | `cor_empty` | `nor_empty` | `sou_empty` | `eas_empty` | `wes_empty` |

The calendar shows the 14:00 South booking from 13:45. That is calendar data, not a clip.

Every time is on the world's 15-minute step grid (:00, :15, :30, :45): the world ticks every 15 simulated minutes, so a change between ticks would only show at the next tick.

## Filming order (about 90 minutes)

1. Core: rows 1, 2, 3.
2. North: rows 4, 5.
3. South: rows 6, 7, 8, plus the complaint audio.
4. East: rows 9 and 14.
5. West: rows 10, 11, 12.
6. Live phone segment: row 13.

## After the shoot

1. Copy every take to `C:/Users/hussa/dev/footage/raw/` and rename it to its file name. Keep `.mov` for iPhone files. Put the phone backup in `raw/phone/`.
2. Watch each clip. If a clip needs trimming, add its in and out seconds to [cuts.yaml](cuts.yaml).
3. Normalize the clips into `clips/`:

   ```bash
   uv run python scripts/footage/normalize.py
   ```

4. Cut the best complaint line into `audio/south_complaint.wav`. Replace `2.0` and `5.5` with the start and end seconds of the best take:

   ```bash
   uv run python scripts/footage/extract_audio.py \
       C:/Users/hussa/dev/footage/raw/south_complaint.m4a --start 2.0 --end 5.5
   ```

5. Store the signed consent sheets in `C:/Users/hussa/dev/footage/consent/`. They contain real names, so they never go into the repo.
