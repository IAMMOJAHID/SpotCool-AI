"""AgentSettings: the agent's configuration, from flags, ``SPOTCOOL_AGENT_*`` and an env file.

Precedence: arguments (the CLI's flags) over environment variables over the ``--env-file``.
The Meta key is read from ``SPOTCOOL_AGENT_META_API_KEY`` or ``META_API_KEY`` (the key file's
own name), kept as a ``SecretStr``: its ``repr``, its JSON and the dry run show it masked.
Other keys in the env file are ignored.
"""

import ipaddress
import socket
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from spotcool_contracts.priorities import HEARTBEAT_PERIOD_S
from spotcool_contracts.vrf import VrfVendor


class AgentSettings(BaseSettings):
    """Everything the agent needs to know (see the module docstring).

    Parameters:
        world_host: The world's address (BACnet, HTTP, dashboard), an IPv4 address; by default
            this machine's primary one, where the world's BMS binds by default.
        bms_port: The BMS's BACnet/IP UDP port.
        http_port: The world's camera and calendar server.
        dashboard_port: The world's dashboard server, which has ``/ws/agent``.
        listen: Where the agent serves ``/chat`` and ``/voice``, as ``host:port``.
        vision: The people counter: ``meta`` (SAM 3.1), ``blob`` (synthetic frames) or
            ``scripted`` (``scripted_counts``).
        meta_api_key: The Meta AI API key, needed by ``meta``.
        meta_base_url: The Meta AI API.
        sam_model: The SAM model.
        sam_min_confidence: Boxes below this confidence are dropped.
        sam_call_cap: Meta calls the run may make in all; then everything is released.
        sam_concurrency: Meta calls in flight at once.
        settle_s: Wall seconds from a step's change to its snapshots.
        bacnet_address: The agent's own BACnet IPv4 address; by default the one that reaches
            ``world_host``.
        bacnet_port: The agent's own BACnet/IP UDP port; 0 takes any free one.
        heartbeat_s: Wall seconds between two heartbeat toggles.
        scripted_counts: People per camera id for ``scripted``.
        log_level: loguru's level for the run log on stderr.
        stt: The voice transcriber: ``meta`` (``muse-voice-transcribe-1.0``) or ``fake``; by
            default ``meta`` with ``--vision meta``, else ``fake``.
        llm: The language model for intents and replies: ``meta`` (``muse-wonder-1.0``),
            ``fake`` or ``off`` (keywords and templates only); by default ``meta`` with
            ``--vision meta``, else ``off``.
        stt_model: The transcription model.
        llm_model: The language model.
        vrf: Run the meeting room's spot cooling and let complaints act on its VRF unit.
        vrf_port: The VRF gateway's BACnet/IP UDP port, on ``world_host``.
        vrf_camera: The camera that watches the meeting room.
        vrf_brand: The unit's brand, when the gateway's ``Model_Name`` does not say.
        vrf_camera_height_m: That camera's mount height.
        vrf_camera_tilt_deg: Its tilt below horizontal.
        vrf_camera_hfov_deg: Its horizontal field of view.
        vrf_mirror: The camera faces the unit: its right is the unit's left.
        vrf_hold_s: Wall seconds with nobody seen before the vanes are released.
        draft_hold_s: Wall seconds a draft complaint pauses the aiming.
        complaint_hold_s: Wall seconds a complaint's setpoint and fan override lasts.
    """

    model_config = SettingsConfigDict(env_prefix="SPOTCOOL_AGENT_", extra="ignore")

    world_host: str = Field(default_factory=lambda: primary_ipv4())
    bms_port: int = Field(default=47808, ge=1, le=65535)
    http_port: int = Field(default=8081, ge=1, le=65535)
    dashboard_port: int = Field(default=8090, ge=1, le=65535)
    listen: str = "127.0.0.1:8002"
    vision: Literal["meta", "blob", "scripted"] = "meta"
    meta_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "meta_api_key", "SPOTCOOL_AGENT_META_API_KEY", "META_API_KEY"
        ),
    )
    meta_base_url: str = "https://api.meta.ai/v1"
    sam_model: str = "sam-3.1"
    sam_min_confidence: float = Field(default=0.3, ge=0, le=1)
    sam_call_cap: int = Field(default=600, ge=0)
    sam_concurrency: int = Field(default=5, ge=1)
    settle_s: float = Field(default=1.0, ge=0)
    bacnet_address: str | None = None
    bacnet_port: int = Field(default=47820, ge=0, le=65535)
    heartbeat_s: float = Field(default=HEARTBEAT_PERIOD_S, gt=0, le=60)
    scripted_counts: dict[str, int] = Field(default_factory=dict)
    log_level: Literal["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR"] = "INFO"
    stt: Literal["meta", "fake"] | None = None
    llm: Literal["meta", "fake", "off"] | None = None
    stt_model: str = "muse-voice-transcribe-1.0"
    llm_model: str = "muse-wonder-1.0"
    vrf: bool = False
    vrf_port: int = Field(default=47810, ge=1, le=65535)
    vrf_camera: str = "cam-meeting-live"
    vrf_brand: VrfVendor | None = None
    vrf_camera_height_m: float = Field(default=2.8, gt=0)
    vrf_camera_tilt_deg: float = Field(default=30.0, gt=0, lt=90)
    vrf_camera_hfov_deg: float = Field(default=90.0, gt=0, lt=180)
    vrf_mirror: bool = False
    vrf_hold_s: float = Field(default=10.0, gt=0)
    draft_hold_s: float = Field(default=600.0, ge=0)
    complaint_hold_s: float = Field(default=900.0, ge=0)

    @model_validator(mode="after")
    def _offline_follows_vision(self) -> "AgentSettings":
        """Unset ``stt`` and ``llm`` follow ``vision``: Meta with Meta, offline otherwise."""
        online = self.vision == "meta"
        if self.stt is None:
            self.stt = "meta" if online else "fake"
        if self.llm is None:
            self.llm = "meta" if online else "off"
        return self

    @field_validator("world_host", "bacnet_address")
    @classmethod
    def _ipv4(cls, value: str | None) -> str | None:
        if value is not None:
            ipaddress.IPv4Address(value)  # BACnet/IP addresses devices by IPv4
        return value

    @field_validator("listen")
    @classmethod
    def _host_port(cls, value: str) -> str:
        host, _, port = value.rpartition(":")
        if not host or not port.isdigit() or not 0 < int(port) < 65536:
            raise ValueError(f"listen must be host:port, got {value!r}")
        return value

    @property
    def listen_host(self) -> str:
        """The host part of ``listen``."""
        return self.listen.rpartition(":")[0]

    @property
    def listen_port(self) -> int:
        """The port part of ``listen``."""
        return int(self.listen.rpartition(":")[2])


def primary_ipv4() -> str:
    """This machine's primary IPv4 address (its default route's), or 127.0.0.1 if it has none.

    The world's BMS binds the same address by default (its ``server.primary_ipv4``, copied).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("10.255.255.255", 1))  # a UDP connect sends nothing; it picks a route
            return str(probe.getsockname()[0])
        except OSError:
            return "127.0.0.1"


def load_settings(env_file: Path | None = None, **overrides: object) -> AgentSettings:
    """The settings: ``overrides`` (the flags), then the environment, then ``env_file``.

    Args:
        env_file: A dotenv file, such as the key file; ``None`` reads none.
        **overrides: Settings given on the command line; ``None`` values are left out.

    Raises:
        pydantic.ValidationError: If a value is invalid.
    """
    given = {name: value for name, value in overrides.items() if value is not None}
    return AgentSettings(_env_file=env_file, **given)  # type: ignore[call-arg]
