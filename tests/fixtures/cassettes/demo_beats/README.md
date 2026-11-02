# Demo-beat cassettes

Hand-written raw model responses for four 25-second turns at 1 FPS (640x360), replayed by
`--mode replay --cassette tests/fixtures/cassettes/demo_beats` and by `tests/test_pipeline_replay.py`.

1. One seated occupant, comfortable (neutral baseline).
2. The occupant rubs their arms and puts on a jacket while still in the jet; pair it with the typed
   complaint "It's freezing in this meeting room." (`--complaint`). Expect a draft override.
3. The door is propped open from 2 s to the end (23 s, confirmed by Spark). Expect `fan_only`.
4. The door is closed again. Expect the compressor restart to be deferred by the lockout.
