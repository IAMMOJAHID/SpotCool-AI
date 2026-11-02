# WP1.2: A BOPTEST run survives a dropped connection

**Tool:** Claude Code. **Branch:** `wp/wp12-boptest-resilience`, cut from `world/main` at `6159284` (WP6.1 merged).
**Depends on:** WP1 (the BOPTEST client and plant), WP6.1 (`BoptestClient.results`, `power_fallbacks` and the report's verdict) and WP11 (the run and its recording).

## Why this exists
The lead's first paced BOPTEST checkpoint (Nov 2, 11:45) died four steps in.
- **What happened:** the world reaches BOPTEST in the GitHub Codespace through `gh codespace ports forward`. On the laptop's mobile hotspot (IPv6 with NAT64), the tunnel dropped and the forward exited. The `POST /advance` in flight hung until its 120 s `ReadTimeout`, the plant step failed, and the run was cancelled.
- **The stop calls failed too.** The world could not reach `PUT /stop` through the dead tunnel, so both tests were left running until the lead stopped them by hand.
- **Why it matters:**
  - Person A's agent must drive the world over BACnet, which is UDP, and a Codespace forward carries only TCP. So every run with A's agent (the Nov 3 integration and the Nov 4 takes) runs the world on the laptop, with BOPTEST through this tunnel, for 12 minutes or more.
  - Today one dropped connection, or even the 3 s gap while a supervised forward restarts, ends the run. `advance` is never retried, not even after a `ConnectError`.

## Your lane (only these paths)
- `packages/world/src/spotcool_world/services/boptest/client.py` and `services/boptest/plant.py`.
- `processors/plant_processor.py`: only to surface recoveries (decision 4).
- `processors/recorder.py` and `runner/cli.py`: only for decision 6.
- `transports/bacnet/bms.py`: only for decision 9.
- The matching tests under `packages/world/tests/` (`plants/`, `assembly/`, `telemetry/`), the relevant lines of `packages/world/AGENTS.md`, and the changelog fragment `packages/world/changelog/+boptest-dropped-connection.fixed.md`.

`packages/contracts/` is read-only. If you think a contract must change, stop and report it.

## Decisions already made (binding)
1. **A step whose answer was lost is recovered from BOPTEST's own stored results, never guessed.**
   - The step's end time is known before it is sent: `t_next = t + step`.
   - BOPTEST stores results every 30 s, including the step's end (WP6.1's review: 31 samples, both ends). `PUT /results` serves measurements and inputs alike, and filters without interpolating.
   - **If `t_next` is in the stored results, the step happened.** Rebuild the step's answer from `PUT /results` over `[t_next, t_next]` for every key the advance answer carries (the measurements and the `_u` read-backs), then continue as if `advance` had answered. WP6.1's step means follow as usual.
2. **Resend an advance only when BOPTEST certainly never got it.**
   - **`unsent` errors** (`ConnectError`, `ConnectTimeout`: the request never reached BOPTEST): resend once the connection is back.
   - **Any other failure** (a read timeout, a dropped or reset connection, a 502, 503 or 504 from the tunnel): the request may have reached BOPTEST. Its web layer finishes a queued job even when the client is gone, so a received advance lands later. Poll the stored results for `t_next`, with backoff:
     - `t_next` appears: decision 1;
     - still absent in a successful `/results` answer received at least `ADVANCE_TIMEOUT_S` after the lost request was sent: BOPTEST never got it, so resend it;
     - otherwise keep polling.
   - Resending the same inputs is safe: inputs latch, so the resend is exactly the step that was meant.
   - Verify the web layer's behaviour in the clone (`C:/Users/hussa/dev/boptest`, `service/web`): a client disconnect must not cancel the queued job. If it does, stop and report: the rule above depends on it.
3. **The recovery is bounded.** One step waits at most `RECOVERY_S = 300` s of wall time, after its first failure, for its state. Past that, the step fails as today, with a message that says what was tried. At speed 60 a recovered step only delays the run: the pacing resumes with the next tick.
4. **Every recovery is visible, and none blocks quoting.** A recovered step's state is BOPTEST's own, exactly.
   - `PlantService` gets `connection_recoveries` (default 0); BOPTEST overrides it.
   - When the count rises, `PlantProcessor` pushes a non-fatal error naming the run and the step, plus a `MetricsFrame` sample with the running count, the same way WP6.1 surfaces `power_fallbacks`.
   - The verdict (`quote_blockers`) doesn't change.
5. **`stop()` gets through a short outage.**
   - On connection errors, retry for up to 60 s with backoff.
   - If it still fails, log a warning naming the test and the exact command that stops it later: `curl -X PUT <boptest_url>/stop/<testid>`.
   - The plant's bounded `stop()` wait (`STOP_WAIT_S`) still holds.
6. **A run that ends on a fatal error is `failed` everywhere.**
   - Today the `run_status` event says `failed`, but the manifest's `ended` and `run`'s printout say `cancelled`.
   - `cancelled` means stopped from outside (Ctrl+C); `failed` means a fatal error. The manifest and the printout follow the same rule as `RunStatusProcessor`.
7. **Transport errors say what timed out.** httpx timeouts have empty messages, which printed as "ReadTimeout: ". Error text names the exception and the timeout in force, e.g. "ReadTimeout after 120 s".
8. **The forward is supervised outside the world.** AGENTS.md (BOPTEST section) gives the loop the lead uses, and says why:
   `while :; do gh codespace ports forward 8000:8000 -c <codespace>; sleep 3; done`
   No new code for it.
   - The same section states the rule the second checkpoint broke: **the PC stays on one network for the whole run.**
     - The BMS binds the PC's IPv4 when the run starts, and the tunnel dies with the network.
     - On Nov 2, Windows moved the laptop from one saved Wi-Fi network (192.168.234.x) to another (192.168.1.x) mid-run.
     - Before a run, set the other saved networks to connect manually: `netsh wlan set profileparameter name="<profile>" connectionmode=manual`.
9. **The BMS says so when the PC's address changes.**
   - On each tick, check that the BMS's bound IPv4 is still one of the PC's addresses. A plain check (for example `socket.getaddrinfo` on the host name, or `psutil` if it's already a dependency) is enough; don't add a dependency for it.
   - The first time it isn't, push a non-fatal error: "the PC's network changed: the BMS at <ip>:<port> is unreachable; restart the run on one network".
   - Never fatal: the plants and the recording go on.

## Tests
Use `httpx.MockTransport` fakes. No live BOPTEST; the lead runs the live check.
- **The answer is lost after the step happened:** the fake applies the advance, then raises `ReadTimeout`. The plant rebuilds the state from `/results`, and it equals what `advance` would have answered. BOPTEST advanced exactly once, and the recovery count is 1.
- **The request never got there:** `ConnectError` before the fake applies anything. The advance is resent once, and BOPTEST advanced once.
- **Ambiguous, and never arrived:** a `ReadTimeout` where the fake never applied the advance. With an injected clock, it's resent only after `ADVANCE_TIMEOUT_S`, and BOPTEST advanced once.
- **Ambiguous, and arrives late:** the fake applies the advance 5 s after the drop. It's recovered, not resent.
- **Outage past `RECOVERY_S`:** the step fails with a clear message.
- **`stop()`:** it retries through a 10 s outage, and logs the curl command after a longer one.
- **A world run** with the fake BOPTEST dropping one answer: it finishes, the recording holds one non-fatal error and the recovery count, and the report is unaffected.
- **Decision 6:** a run with a fatal plant error records `ended: failed`, and the printout says so.
- **Decision 7:** the error text names the exception and its timeout.
- **Decision 9:** with the address check faked, a vanished address gives one non-fatal error, not one per tick.

Tests never wait in real time for backoff: inject the clock and sleep functions.

## Done when
- **The checks are green** with all extras installed: `uv run ruff check packages`, `uv run ruff format --check packages`, `uv run pyright packages`, `uv run pytest packages -W error::RuntimeWarning` and `uv lock --check`.
- The changelog fragment is written.
- The lead re-runs the paced BOPTEST checkpoint over the supervised forward.
