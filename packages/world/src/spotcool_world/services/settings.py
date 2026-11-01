"""WorldSettings: every knob of a world run, from arguments, environment or ``world.yaml``."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

ClockText = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
"""Time of day as ``HH:MM`` (24-hour)."""

Port = Annotated[int, Field(ge=1, le=65_535)]
"""A TCP or UDP port number."""


class WorldSettings(BaseSettings):
    """Settings of a world run.

    Sources, strongest first: constructor arguments, ``SPOTCOOL_WORLD_*`` environment
    variables (``SPOTCOOL_WORLD_SPEED=none`` means no pacing), then ``world.yaml`` in the
    current directory if it exists, then the defaults below.

    Parameters:
        plant: Plant provider.
        boptest_url: Hosted BOPTEST service (plain HTTP: its HTTPS certificate expired).
        boptest_local_url: Local BOPTEST Docker stack.
        testcase: BOPTEST test case.
        time_period: BOPTEST scenario time period.
        scenario: World scenario name.
        step_s: Simulation seconds per tick.
        speed: Simulation seconds per wall second, or ``None`` to run as fast as possible.
        start_clock: Simulated time of day the run starts, ``HH:MM``.
        end_clock: Simulated time of day the run ends, ``HH:MM``.
        bms_port: UDP port of the virtual BMS (BACnet/IP).
        vrf_port: UDP port of the VRF gateway (BACnet/IP).
        http_port: HTTP port for the camera catalog and the room calendar.
        dashboard_port: Port of the dashboard event stream.
        agent_base_url: Base URL of Person A's agent (``/chat``, ``/voice``).
        run_dir: Directory for recorded runs.
        vrf_profile: VRF gateway brand profile.
        log_level: Loguru level.
    """

    model_config = SettingsConfigDict(
        env_prefix="SPOTCOOL_WORLD_",
        env_parse_none_str="none",
        yaml_file="world.yaml",
        extra="forbid",
    )

    plant: Literal["boptest-hosted", "boptest-local", "rc", "replay"] = "rc"
    boptest_url: str = "http://api.boptest.net"
    boptest_local_url: str = "http://127.0.0.1:80"
    testcase: str = "multizone_office_simple_air"
    time_period: str = "peak_cool_day"
    scenario: str = "hybrid_tuesday"
    step_s: float = Field(default=900.0, gt=0)
    speed: float | None = Field(default=300.0, gt=0)
    start_clock: ClockText = "07:00"
    end_clock: ClockText = "19:00"
    bms_port: Port = 47808
    vrf_port: Port = 47810
    http_port: Port = 8081
    dashboard_port: Port = 8090
    agent_base_url: str = "http://127.0.0.1:8002"
    run_dir: Path = Path("runs")
    vrf_profile: Literal["mitsubishi", "daikin", "lg"] = "mitsubishi"
    log_level: str = "INFO"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Use arguments, then environment variables, then ``world.yaml``."""
        return init_settings, env_settings, YamlConfigSettingsSource(settings_cls)
