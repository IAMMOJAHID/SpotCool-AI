# AG9: Muse Image, a privacy view of the cameras (the fifth Meta model)

**Branch:** `ag/ag9-muse-image`, from `agent/main` `2f84cfc`. **Worktree:** `C:\Users\hussa\dev\wt\ag9-muse-image`. **Venv:** `C:\Users\hussa\dev\venvs\ag9-muse-image`.

**Why:** the user asked for "all other meta models, wherever possible". Person A's MVP used `muse-image-1.0` for a privacy render (`origin/third_level_improvement:app/spotcool/media.py`, `render_privacy_snapshot`, and `PRIVACY_RENDER_PROMPT`). It redraws a CCTV frame as a flat, faceless illustration, so an operator can follow the building without seeing anyone's face.

## The lead's probe (Nov 4)

`images.edit` with A's prompt on one MEVA frame took **24 s** and returned a 280 KB `b64_json`; it gave no URL.

## Binding decisions

1. **An image service, provider-based like the others.**
   - Files: `services/render.py` (the base, `RenderError`), `services/meta/render.py` (`MuseImageRender` on its own `MetaClient`: 1 at a time, `--render-call-cap` 60) and a fake (it returns a small solid JPEG).
   - **The call:** `images.edit(model="muse-image-1.0", image=("frame.jpg", jpeg, "image/jpeg"), prompt=A's PRIVACY_RENDER_PROMPT, n=1)`, with a 45 s timeout.
   - **The trace:** AG7's `META ->` and `<-` lines. `in:` is the JPEG's size; `out:` is the rendered image's size in KB.
   - **Never log** image bytes or base64.
2. **A render loop, display only.**
   - File: `loops/render.py`, a background task the agent starts with `--render meta|fake|off`. The default is `off`, and the flag never follows `--vision`, because the renders cost 24 s each.
   - Every `render_every_s` (180 s of wall time), it renders the zone cameras one at a time, plus the meeting room's. It uses a fresh snapshot each time, held in memory, and keeps **only the latest render per camera, in memory**.
   - **Never on any actuation path.** It never touches the BMS, the VRF, the policy or a step's deadline.
3. **Serving the renders.**
   - The agent's server (`transports/server.py`, port 8002) answers `GET /privacy/{camera_id}.jpg` with the latest render, or 404 before the first.
   - Send CORS the way `/chat` does, plus `Cache-Control: no-store`.
   - Nothing is ever written to disk. Add it to the audit-hook test.
4. **Dashboard: `?privacy=on`** (with `&agent=`).
   - The camera tiles show the agent's `/privacy/<id>.jpg`, refreshed every 10 s, in place of the raw stream. Keep SAM's boxes on top, and label each tile "Muse Image render".
   - Without a render yet, the tile shows "rendering…".
   - Add Vitest and e2e cases (mock the endpoint).
5. **Honesty.**
   - A render is an illustration, never evidence. It's display only, and the policy never sees it.
   - Write it that way in `docs/META_MODELS.md`: a new "Muse Image" section, plus the probe.
   - Update the README's models table (now five), `packages/agent/README.md` (the flags) and `packages/agent/AGENTS.md`.

## Tests

- **Fakes for everything.**
- **The BMS writes are identical with and without `--render`.** Use the AG8 test pattern: `test_the_scene_notes_and_narration_never_change_a_bms_write` in `tests/test_building_loop.py`.
- **Server:** a 404 before the first render, then the latest render; CORS.
- **Budget:** the loop stops at its call cap.
- **Privacy:** no file written.

## Checks, commits, report

- The full set in `CLAUDE.md`, including the dashboard's `npm ci`, build, lint, test and e2e.
- Commits on `ag/ag9-muse-image`:
  - `feat(agent): Muse Image privacy renders`
  - `feat(dashboard): the privacy view`
  - `docs: ...`
  - Each ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No push. A report under 400 words.
