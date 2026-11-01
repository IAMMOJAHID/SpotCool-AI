Plant services (WP1), all behind `PlantService` and picked by `create_plant(settings, run)`:

- `BoptestClient` and `BoptestPlant`, with hosted and local providers. They handle BOPTEST's REST rules:
  - `Content-Type` is sent only with a body;
  - `_activate` goes explicitly for the ten managed inputs on every step, because inputs latch;
  - override names are checked against the catalog;
  - `select-async` is retried, with backoff and jitter, only when it created nothing, and `advance` is never retried;
  - a test is always stopped, and the stop survives a second Ctrl+C;
  - an idle test gets a keep-alive, but not while a call is in flight.
- `RcPlant`: a numpy five-zone model whose VAV boxes are calibrated to BOPTEST (minimum flows, damper overrides, leakage).
- `ReplayPlant`, which refuses a file that mixes two runs.
- The run-record format, `serializers.jsonl`: `RawRecord` (finite values only), `EventRecord`, `RunWriter` and `read_records` (which skips a torn last line).
- `PlantProcessor`, which stamps `PlantMeasurementsFrame` with the end of the step its values describe.
- `spotcool-world probe`.
- `WorldSettings.replay_run`.
- Example `05_boptest_step.py`.
