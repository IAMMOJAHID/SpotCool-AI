Plant services (WP1), all behind `PlantService` and picked by `create_plant(settings, run)`:

- `BoptestClient` and `BoptestPlant`, with hosted and local providers. They handle BOPTEST's REST rules:
  - `Content-Type` is sent only with a body;
  - `_activate` goes explicitly for the ten managed inputs on every step, because inputs latch;
  - override names are checked against the catalog;
  - `select-async` is retried with backoff and jitter, and a test is always stopped;
  - an idle test gets a keep-alive.
- `RcPlant`: a numpy five-zone model whose VAV boxes are calibrated to BOPTEST (minimum flows, damper overrides, leakage).
- `ReplayPlant`.
- The run-record format, `serializers.jsonl`: `RawRecord`, `EventRecord`, `RunWriter` and `read_records`.
- `PlantProcessor`.
- `spotcool-world probe`.
- `WorldSettings.replay_run`.
- Example `05_boptest_step.py`.
