# BOPTEST in the Codespace

BOPTEST runs in the GitHub Codespace `spotcool-boptest-qxxjjj94x672rp`. The world runs on the laptop and reaches it through a forwarded port, because BACnet is UDP and a Codespace forward carries only TCP. Why each step matters: [AGENTS.md, Running the world](../../packages/world/AGENTS.md#running-the-world).

The Codespace's free quota is 120 core-hours a month, so always do step 8.

1. **One network for the whole session.** A network change kills the tunnel, and the BMS keeps its old address. List the saved Wi-Fi profiles, then set every one except the demo network to connect manually (PowerShell):

   ```powershell
   netsh wlan show profiles
   netsh wlan set profileparameter name="<profile>" connectionmode=manual
   ```

2. **Start the Codespace and the stack.** The first command boots the Codespace too:

   ```bash
   gh codespace ssh -c spotcool-boptest-qxxjjj94x672rp -- 'cd /workspaces/project1-boptest && docker compose up -d --scale worker=2 web worker provision && docker compose ps'
   ```

   If it says `Cannot connect to the Docker daemon`, Docker is still starting: wait 10 s and run it again. `docker compose ps` should list `web`, two `worker`s and `provision`.

3. **Forward port 8000, supervised.** In a Git Bash window of its own, and leave it running. The loop restarts a dropped tunnel; the world rides out the gap:

   ```bash
   while :; do gh codespace ports forward 8000:8000 -c spotcool-boptest-qxxjjj94x672rp; sleep 3; done
   ```

4. **Check the service** in another window. It should print `1.0.0-dev`:

   ```bash
   curl http://127.0.0.1:8000/version
   ```

5. **Probe the plant.** It starts a test, steps it, prints its KPIs and stops it. Both workers must be free:

   ```bash
   uv run spotcool-world probe --plant boptest-local
   ```

6. **The checkpoint run** (the lead runs it; what to expect is in [AGENTS.md, The BOPTEST checkpoint](../../packages/world/AGENTS.md#running-the-world)):

   ```bash
   uv run spotcool-world run --plant boptest-local --speed 0 --stub-agent --run-id boptest-checkpoint
   uv run spotcool-world report boptest-checkpoint
   ```

7. **The live eval** (the lead runs it; CI never does). It runs `hybrid_tuesday` once on BOPTEST at speed 60, about 15 minutes, judges the saved run, and keeps it in the run directory as `eval-live-hybrid-<date>-<time>`. It prints the run's path, its saving and both energies, how late each stub write was taken, and the report command:

   ```bash
   export SPOTCOOL_LIVE_BOPTEST_URL=http://127.0.0.1:8000 SPOTCOOL_LIVE_RUN_DIR="$SPOTCOOL_WORLD_RUN_DIR"
   uv run pytest -m "eval and live" -rs
   uv run spotcool-world report eval-live-hybrid-<date>-<time>   # the id it printed
   ```

8. **Stop.** Press Ctrl+C in the forward loop's window (twice if it starts again), then stop the Codespace:

   ```bash
   gh codespace stop -c spotcool-boptest-qxxjjj94x672rp
   ```

   Stop any run with Ctrl+C, never by closing its window: the world stops both BOPTEST tests on its way out. If a test is ever left running, the log names the command that stops it (`curl -X PUT <boptest_url>/stop/<testid>`).
