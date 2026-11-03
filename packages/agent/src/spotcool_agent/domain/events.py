"""The data of the agent's dashboard events: perception, decision, explanation and chat.

The shapes are the stub agent's, without its ``stub`` flag, so the dashboard reads both. Each
event travels in a contract ``EventEnvelope`` with ``source: "agent"``.
"""

from pydantic import BaseModel, ConfigDict

from spotcool_contracts import Zone

Value = str | float | None
"""A point's value in BMS units: a state text or a setpoint in degC; ``None`` is NULL."""

Polygon = list[list[float]]
"""A polygon's corners as ``[x, y]`` pairs, in the camera frame's pixels."""


class _Data(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Perception(_Data):
    """The data of a ``perception`` event: what the cameras showed at the step's close.

    Parameters:
        people: People counted in each zone; a zone the agent could not count is left out.
        masks: Each counted camera's people as rectangles, in the snapshot's pixels.
    """

    people: dict[Zone, int]
    masks: dict[str, list[Polygon]]


class Decision(_Data):
    """The data of a ``decision`` event: one BACnet command, or a back-off from one.

    Parameters:
        zone: The zone commanded.
        point: The BMS point.
        value: The state text or setpoint (degC) written, or ``None`` for a release (NULL).
        priority: The priority slot written.
        reason: Why.
    """

    zone: Zone
    point: str
    value: Value
    priority: int
    reason: str


class Explanation(_Data):
    """The data of an ``explanation`` event.

    Parameters:
        text: One sentence for the agent panel.
    """

    text: str


class Chat(_Data):
    """The data of a ``chat`` event: the agent's reply to a complaint.

    Parameters:
        text: The reply.
    """

    text: str
