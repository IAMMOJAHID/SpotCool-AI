# WiseNET in a GitHub Codespace

WiseNET ships as one 10.7 GB `data.zip`, and the laptop's disk is nearly full. So the download, the extraction and the normalizing run in a Codespace, and only the small normalized clips come down (expect a few hundred MB).

- **Dataset:** [4TU.ResearchData](https://data.4tu.nl/datasets/5f790109-8369-4207-a3ee-35174c144483), DOI `10.4121/uuid:c1fb5962-e939-4c51-bfd5-eac6f2935d44`.
- **Licence:** CC0, according to the record's citation metadata. Confirm it on the 4TU page when you download, and note the date in `CREDITS.md`.
- **Free quota:** GitHub Free gives 120 core-hours and 15 GB-month of storage per month; at the limit Codespaces stop, with no bill.
  - On a 2-core machine, this job takes about 1-2 core-hours (30-60 minutes).
  - The job peaks at about 20 GB of disk for an hour, which is about 0.03 GB-month.
  - A stopped codespace still counts against storage, so **delete it** when you are done (step 5).
- **Unverified from this laptop:** 4TU refused connections from here on 1 November. The script's 4TU API lookup of the zip's link is therefore untested. If it fails, copy the `data.zip` link from the 4TU page and pass it as `WISENET_URL`.

## Commands

Run these locally in Git Bash, with `gh` logged in. The codespace needs a branch that contains `scripts/footage/`: `wp/wp8-footage` once the lead has pushed it, or the integration branch after the merge.

1. **Create the codespace:** 2 cores, 32 GB disk, and an idle timeout long enough for the download.

   ```bash
   CS=$(gh codespace create -R IAMMOJAHID/SpotCool-AI -b wp/wp8-footage \
       -m basicLinux32gb --idle-timeout 120m --display-name wisenet)
   echo "$CS"
   ```

   If `$CS` is empty, set it to the codespace's name from `gh codespace list`.

2. **Start the job** in the background, so it survives a dropped SSH session.
   - The default sets are 1, 2, 5 and 11; `wisenet_mapping.yaml` explains why. Prefix the command with `SETS="..."` to change them.

   ```bash
   gh codespace ssh -c "$CS" -- 'cd /workspaces/SpotCool-AI && nohup bash scripts/footage/wisenet_codespace.sh > /workspaces/wisenet.log 2>&1 < /dev/null &'
   gh codespace ssh -c "$CS" -- tail -f /workspaces/wisenet.log   # Ctrl-C stops watching, not the job
   ```

3. **Check the log** before copying anything.
   - **Camera locations:** each camera's space and doors, from its calibration file. If any differ from the assumptions A1-A5 in `docs/footage/wisenet_mapping.yaml`, fix the mapping and rerun only the index step (see "Rerun only the index" below).
   - **`<file>: no manual annotations, skipped`:** the annotation files were not found. The zip listing is in `/workspaces/wisenet/contents.txt`.
   - **`cam-<zone> <scene>: ...`:** the segment chosen for each camera and scene, or `none`. A `none` scene falls back to our own clips.

4. **Copy the clips down.**
   - `MSYS_NO_PATHCONV=1` stops Git Bash from rewriting the remote path.
   - The copy creates `C:/Users/hussa/dev/footage/datasets/wisenet/`, holding `index.yaml` and `video<set>_<camera>.mp4`.

   ```bash
   MSYS_NO_PATHCONV=1 gh codespace cp -r -c "$CS" \
       remote:/workspaces/wisenet/wisenet C:/Users/hussa/dev/footage/datasets/
   ```

5. **Delete the codespace.** This frees the storage quota.

   ```bash
   gh codespace delete -c "$CS" --force
   ```

### Rerun only the index

To change the mapping or the sets without downloading again (the zip and the extracted files stay in `/workspaces/wisenet` until you delete the codespace):

```bash
gh codespace ssh -c "$CS" -- 'cd /workspaces/SpotCool-AI && python3 scripts/footage/wisenet_index.py /workspaces/wisenet/data --out /workspaces/wisenet/wisenet'
```

## Notes

- **Requirements:** the scripts use only the standard library, ffmpeg and ffprobe, so the codespace's stock `python3` (3.10 or newer) runs them. The script installs ffmpeg with apt.
- **What gets extracted:** Python's `zipfile` extracts `video<set>_<camera>.avi` for each chosen set, plus every `.json` file.
  - The log line `set videos found: [...]` must list every set you asked for. If one is missing, look up the real file names in `/workspaces/wisenet/contents.txt`.
  - If the automatic annotations turn out to be large, narrow the `.json` filter in the script to the manual-annotation folder.
- **Dry run:** the post-download steps were dry-run on 1 November against a small synthetic zip: extraction, camera locations, index and normalizing. The 4TU download itself has not run.
- **Sets 5-11 are 640x480 at 25 fps.** `normalize.py` pillarboxes them to 1280x720 and converts them to 30 fps, so their segment times stay valid.
