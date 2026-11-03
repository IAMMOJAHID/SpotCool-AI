"""End-to-end eval runs: the world in process on RC without its stub, and the real agent.

``run_with_agent`` starts the agent first (``--vision blob``, on the world's synthetic frames),
then the whole ``World`` on loopback, on ports the operating system has just handed out, the run
recorded into a temp ``run_dir``; the world's occupants post their complaints to the agent's
own server. When the world's run ends, the agent is stopped. It returns the record read back
by the world's report (``load_run``), and what the agent's dashboard publisher dialled.

``run_with_stub`` is the world's golden-eval run (``packages/world/evals/conftest.py``, copied,
not imported): the same day with the stub agent, the policy's result with perfect perception.
"""

import asyncio
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from spotcool_agent.runner.agent import Agent
from spotcool_agent.settings import AgentSettings
from spotcool_world.runner.assembly import World
from spotcool_world.runner.report import RecordedRun, load_run
from spotcool_world.services import WorldSettings

HOST = "127.0.0.1"
AGENT_SPEED = 300.0
"""The agent run's speed: a 15-minute step every 3 s, the RC day in about 2.5 minutes."""
AGENT_SETTLE_S = 0.3
"""The agent's settle at ``AGENT_SPEED``: a camera renders 5 frames a second."""
STUB_SPEED = 1800.0
"""The stub run's speed, the world's golden evals': every stub write lands a step later."""
TIMEOUT_S = 400.0


@dataclass(frozen=True)
class AgentRun:
    """An agent run, read back from its record.

    Parameters:
        run: The record, as the report reads it.
        dialled: Every URL the agent's dashboard publisher dialled.
        client: The agent's BACnet ``ip:port``, as the BMS records its writes.
        answered: Complaints the agent answered.
    """

    run: RecordedRun
    dialled: list[str]
    client: str
    answered: int


def free_ports(*kinds: socket.SocketKind) -> list[int]:
    """One port per kind on 127.0.0.1, all free a moment ago and all different."""
    probes = [socket.socket(socket.AF_INET, kind) for kind in kinds]
    try:
        for probe in probes:
            probe.bind((HOST, 0))
        return [probe.getsockname()[1] for probe in probes]
    finally:
        for probe in probes:
            probe.close()


def _world_settings(scenario: str, run_dir: Path, **changes: Any) -> WorldSettings:
    tcp, udp = socket.SOCK_STREAM, socket.SOCK_DGRAM
    http, dashboard, bms, vrf = free_ports(tcp, tcp, udp, udp)
    values: dict[str, Any] = {
        "plant": "rc",
        "scenario": scenario,
        "run_dir": run_dir,
        "http_host": HOST,
        "http_port": http,
        "dashboard_port": dashboard,
        "bms_address": HOST,
        "bms_port": bms,
        "vrf_port": vrf,
        "agent_base_url": f"http://{HOST}:{http}",
    }
    return WorldSettings(**(values | changes))


def run_with_agent(
    scenario: str, run_dir: Path, run_id: str, *, speed: float = AGENT_SPEED
) -> AgentRun:
    """Run a library scenario with the agent at ``speed``, and read the record back.

    Args:
        scenario: A library scenario.
        run_dir: Where to record.
        run_id: The run's id.
        speed: The world's speed.
    """
    listen, bacnet = free_ports(socket.SOCK_STREAM, socket.SOCK_DGRAM)
    with pytest.MonkeyPatch.context() as patch:
        for key in [
            key for key in os.environ if key.startswith(("SPOTCOOL_WORLD_", "SPOTCOOL_AGENT_"))
        ]:
            patch.delenv(key)
        patch.chdir(run_dir)
        world_settings = _world_settings(
            scenario,
            run_dir,
            speed=speed,
            stub_agent=False,
            agent_base_url=f"http://{HOST}:{listen}",
        )
        settings = AgentSettings(
            world_host=HOST,
            bms_port=world_settings.bms_port,
            http_port=world_settings.http_port,
            dashboard_port=world_settings.dashboard_port,
            listen=f"{HOST}:{listen}",
            vision="blob",
            settle_s=AGENT_SETTLE_S,
            bacnet_address=HOST,
            bacnet_port=bacnet,
        )

        async def both() -> Agent:
            agent = Agent(settings)
            running = asyncio.create_task(agent.run())
            await asyncio.sleep(0.5)  # the agent starts first, as in the scored run
            try:
                world = World(world_settings, run_id=run_id)
                await world.run(handle_sigint=False)
            finally:
                agent.stop()
                await running
            return agent

        agent = asyncio.run(asyncio.wait_for(both(), TIMEOUT_S))
    return AgentRun(
        load_run(run_dir, run_id),
        agent.publisher.dialled,
        f"{HOST}:{bacnet}",
        agent.complaint_loop.answered,
    )


def run_with_stub(scenario: str, run_dir: Path, run_id: str) -> RecordedRun:
    """Run a library scenario with the stub agent at ``STUB_SPEED``, and read the record back."""
    with pytest.MonkeyPatch.context() as patch:
        for key in [key for key in os.environ if key.startswith("SPOTCOOL_WORLD_")]:
            patch.delenv(key)
        patch.chdir(run_dir)
        settings = _world_settings(scenario, run_dir, speed=STUB_SPEED, stub_agent=True)
        world = World(settings, run_id=run_id)
        asyncio.run(asyncio.wait_for(world.run(handle_sigint=False), TIMEOUT_S))
    return load_run(run_dir, run_id)


@pytest.fixture(scope="session")
def agent_eval_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The session's run directory for the agent's evals."""
    return tmp_path_factory.mktemp("agent-eval-runs")


@pytest.fixture(scope="session")
def agent_day(agent_eval_dir: Path) -> AgentRun:
    """``hybrid_tuesday`` on RC with the agent, at ``AGENT_SPEED``."""
    return run_with_agent("hybrid_tuesday", agent_eval_dir, "hybrid_tuesday-agent")


@pytest.fixture(scope="session")
def stub_day(agent_eval_dir: Path) -> RecordedRun:
    """``hybrid_tuesday`` on RC with the stub, at ``STUB_SPEED``."""
    return run_with_stub("hybrid_tuesday", agent_eval_dir, "hybrid_tuesday-stub")
