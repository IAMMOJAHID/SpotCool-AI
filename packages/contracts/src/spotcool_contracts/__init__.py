"""Interface contracts between the SpotCool agent (Person A) and the simulated world (Person B).

Everything that crosses the boundary between the two sides is defined here and nowhere else:
BACnet point maps, VRF vendor profiles, the camera catalog, the room calendar, occupant
complaints and the dashboard event envelope. Either side may be replaced by real equipment
or a real agent as long as it honours these models.
"""

from spotcool_contracts.cameras import CameraCatalog, CameraInfo, CameraSource
from spotcool_contracts.events import EventEnvelope, EventSource, EventType
from spotcool_contracts.points import DevicePointMap, PointSpec, load_point_map
from spotcool_contracts.priorities import AGENT_PRIORITY, OPERATOR_PRIORITY
from spotcool_contracts.vrf import VrfProfile, load_vrf_profiles
from spotcool_contracts.zones import ZONES, Zone, ZoneInfo

CONTRACTS_VERSION = "0.1.0"

__all__ = [
    "AGENT_PRIORITY",
    "CONTRACTS_VERSION",
    "OPERATOR_PRIORITY",
    "ZONES",
    "CameraCatalog",
    "CameraInfo",
    "CameraSource",
    "DevicePointMap",
    "EventEnvelope",
    "EventSource",
    "EventType",
    "PointSpec",
    "VrfProfile",
    "Zone",
    "ZoneInfo",
    "load_point_map",
    "load_vrf_profiles",
]
