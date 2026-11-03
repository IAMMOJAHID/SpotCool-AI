# Agent work packages

SpotCool's real agent, built from Person A's MVP (`origin/third_level_improvement` `b653cbf`) on the trunk `agent/main`. The status and the next actions are in `docs/agent/HANDOFF.md`. A brief records its WP as decided at kickoff; the handoff says what is true now.

| WP | Brief | Priority | Branch |
|---|---|---|---|
| AG0 | pre-flight (lead; no brief) | P0 | none |
| D1 | real footage: MEVA clips and a dataset index (lead) | P0 | `fix/meva-footage` |
| AG1 | [agent core](AG1-agent-core.md) | P0 | `ag/ag1-agent-core` |
| AG2 | perception rehearsal (lead; an AG2b brief only if fixes are needed) | P0 | none |
| AG3 | the scored BOPTEST run (lead) | P0 | `docs/ag3-scored-run` |
| AG4 | [meeting room, voice and chat](AG4-meeting-room.md) | P0 | `ag/ag4-meeting-room` |
| AG5 | video and release (lead plus user) | P0 | `docs/ag5-release` |

P1, only if P0 is on schedule:
1. an `all_hands_day` control run;
2. the VRF comfort path (PMV, muse-spark clo/met);
3. LLM-written explanations;
4. more agent evals in CI;
5. open-door anomalies.

P2: Docker and compose, a RabbitMQ scale-out transport, a shared `packages/core`, Haystack tags.
