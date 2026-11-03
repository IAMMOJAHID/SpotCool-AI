# Video QA

Run through this list before the upload. The items marked **(qa.py)** are checked by `uv run python scripts/video/qa.py <video> --final`. Every other item needs a person watching the cut on a real screen with sound.

## Automated

- [ ] **(storyboard_check.py)** The sources agree:
  - the beats tile the video and end by 2:58;
  - no narration needs more than 150 words a minute;
  - every number has a source its beat cites, recorded in `docs/release/numbers.md`;
  - no never-quote figure appears, on screen, in the narration or on the results card (the list is `NEVER_QUOTE` in `scripts/video/storyboard.py`, from `docs/release/numbers.md`).
- [ ] **(qa.py)** The format and the sound:
  - under 3:00, and the storyboard's length;
  - 1920x1080 at 30 fps, H.264 yuv420p with AAC;
  - -14 +/- 1 LUFS.
- [ ] **(qa.py)** The SRT is valid:
  - at most 42 characters a line and 2 lines a caption;
  - every caption inside its beat;
  - the captions say the narration word for word.
- [ ] **(qa.py)** The results card is the saved run's, regenerated through the world's report:
  - its figures are the run's in `video/sources.yaml`;
  - the render used it;
  - a stub run says "Upper bound with perfect perception (stub agent, in simulation)".
- [ ] **(qa.py --final)** Nothing is pending:
  - the narration is recorded, not the placeholder;
  - no slate is left;
  - the music, if any, is in `CREDITS.md`.

## Watch it

- [ ] **Privacy.** On every frame of every take, there is:
  - no API key;
  - no email address or notification;
  - no browser tab or bookmark bar;
  - no real name;
  - no badge or logo;
  - no screen showing content.
- [ ] **Consent.** On camera there are only:
  - the MEVA dataset's people (CC BY 4.0, credited in `CREDITS.md` and on the sources slide);
  - you on the phone take;
  - anyone else who signed the consent form (`docs/footage/consent.md`). The signed forms stay outside the repo.
- [ ] **Honesty:**
  - The results beat says the agent saw every zone through Meta's SAM, and says the limitation once: BOPTEST's occupant heat follows its own schedule.
  - The card says "SpotCool saved (SAM 3.1 perception, in simulation)", and the dashboard take's label says "SpotCool's agent, in simulation".
  - Nothing claims the agent counted people exactly: only occupied or empty is scored.
- [ ] **The numbers on screen are the narration's.** Nothing on the dashboard take is read out as SpotCool's own saving.
- [ ] **The takes:**
  - The YABE take shows 23 at priority 8 with slot 10 empty, and the dashboard's "backed off from writing 28.0" message.
  - The replay plays the whole day, 07:00 to 19:00 in 16 s, inside the beat: it starts just after the dashboard appears and ends before the cut.
  - The louvers slew; they don't jump.
- [ ] **The sound:**
  - The voice is clear over the music, with no clipping and no clicks at paragraph starts.
  - The music fades out by the close.
- [ ] **The captions.** Proofread them against the recording. Each one appears with its words and none is cut off.
- [ ] **The labels.** On the takes, the top-left labels don't cover anything that matters.
- [ ] **The close.** The sources slide is on screen long enough to be read when paused, and the repo address is right.

## Ship it

- [ ] `CREDITS.md` lists the music track (title, URL, licence, attribution) and every clip used.
- [ ] Upload `video/renders/spotcool.mp4`. Attach the `.srt` only if the form asks for captions.
- [ ] Watch the uploaded copy once, at full quality.
