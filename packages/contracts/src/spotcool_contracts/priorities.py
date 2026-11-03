"""BACnet command priorities and safety timing that both sides honour.

BACnet commandable points hold 16 priority slots; the most urgent non-NULL slot wins and a
NULL write releases a slot. Levels 1-2 (life safety), 5 (critical equipment) and 6 (minimum
on/off) are never written by SpotCool.
"""

OPERATOR_PRIORITY = 8
"""Manual operator overrides. The agent never overwrites a value held at this level or above."""

AGENT_PRIORITY = 10
"""SpotCool supervisory writes: above schedules, below human operators."""

SCHEDULE_PRIORITY = 16
"""Lowest slot. Building schedules conventionally write at 14-16."""

HEARTBEAT_TIMEOUT_S = 120.0
"""Wall-clock seconds without an agent heartbeat toggle before the BMS releases every
priority-10 command (fail back to the BMS's own control)."""

HEARTBEAT_PERIOD_S = 30.0
"""How often the agent is expected to toggle the heartbeat point."""

RECOMMENDED_MIN_WRITE_INTERVAL_S = 300.0
"""Simulation seconds the agent should leave between writes to the same point."""
