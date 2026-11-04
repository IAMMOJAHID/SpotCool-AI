"""``Agent.run`` releases every slot on a cancel (Ctrl+C) and on a crash, on a real BMS."""

import asyncio
import socket
import time

import pytest

from spotcool_agent.processors.policy import EMPTY_SETPOINT_C, UNOCCUPIED, Command
from spotcool_agent.runner.agent import Agent
from spotcool_agent.settings import AgentSettings
from spotcool_contracts import Zone, load_point_map
from spotcool_world.transports.bacnet.bms import BacnetBmsTransport

HOST = "127.0.0.1"
POINTS = load_point_map("office_bms")
HELD = [POINTS.by_name(n) for n in ("ZN-SOU-OCC-SENSOR", "ZN-SOU-CLG-SP", "SPOTCOOL-HEARTBEAT")]
EMPTY = Command(UNOCCUPIED, EMPTY_SETPOINT_C, "the zone is empty")


def free_port(kind: socket.SocketKind) -> int:
    with socket.socket(socket.AF_INET, kind) as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


def settings(bms_port: int) -> AgentSettings:
    return AgentSettings(
        world_host=HOST,
        bms_port=bms_port,
        http_port=free_port(socket.SOCK_STREAM),  # nothing serves it: no cameras
        dashboard_port=free_port(socket.SOCK_STREAM),  # nor the dashboard
        listen=f"{HOST}:{free_port(socket.SOCK_STREAM)}",
        vision="blob",
        render="fake",  # its background task must neither end the run nor outlive it
        bacnet_address=HOST,
        bacnet_port=0,
    )


async def holding(agent: Agent) -> None:
    """Wait until the heartbeat runs, then command South as an empty zone."""
    deadline = time.perf_counter() + 10
    while "SPOTCOOL-HEARTBEAT" not in agent.actuator.held:
        assert time.perf_counter() < deadline, "no heartbeat"
        await asyncio.sleep(0.02)
    await agent.actuator.apply({Zone.SOU: EMPTY}, 199 * 86_400.0)
    assert set(agent.actuator.held) == {point.name for point in HELD}


async def slot_10(agent: Agent) -> list[object]:
    await agent.bacnet.open()
    try:
        return [(await agent.bacnet.slots(point) or [None] * 16)[9] for point in HELD]
    finally:
        await agent.bacnet.close()


@pytest.mark.bacnet
@pytest.mark.asyncio
async def test_a_cancel_like_ctrl_c_releases_every_slot_then_the_heartbeat(
    writes: list[str],
) -> None:
    bms_port = free_port(socket.SOCK_DGRAM)
    bms = BacnetBmsTransport(address=HOST, port=bms_port)
    await bms.start()
    try:
        agent = Agent(settings(bms_port))
        running = asyncio.create_task(agent.run())
        await holding(agent)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert agent.actuator.held == {}
        assert await slot_10(agent) == [None, None, None]
    finally:
        await bms.stop()
    assert writes == []  # no file written by the agent, its server included


@pytest.mark.bacnet
@pytest.mark.asyncio
async def test_a_crash_releases_every_slot_and_is_raised(writes: list[str]) -> None:
    bms_port = free_port(socket.SOCK_DGRAM)
    bms = BacnetBmsTransport(address=HOST, port=bms_port)
    await bms.start()
    try:
        agent = Agent(settings(bms_port))
        crash = asyncio.Event()

        async def read():  # SIM-CLOCK's reader fails once the zone is held
            await crash.wait()
            raise RuntimeError("the clock broke")

        agent.clock._read = read  # type: ignore[method-assign]
        running = asyncio.create_task(agent.run())
        await holding(agent)
        crash.set()
        with pytest.raises(RuntimeError, match="the clock broke"):
            await asyncio.wait_for(running, 10)
        assert await slot_10(agent) == [None, None, None]
    finally:
        await bms.stop()
    assert writes == []  # no file written by the agent, its server included


@pytest.mark.bacnet
@pytest.mark.asyncio
async def test_the_bms_is_released_first_and_a_stuck_vrf_release_cannot_hold_it_up() -> None:
    bms_port = free_port(socket.SOCK_DGRAM)
    bms = BacnetBmsTransport(address=HOST, port=bms_port)
    await bms.start()
    try:
        vrf_settings = settings(bms_port).model_copy(
            update={"vrf": True, "vrf_port": free_port(socket.SOCK_DGRAM), "vrf_bacnet_port": 0}
        )
        agent = Agent(vrf_settings)
        assert agent.vrf is not None
        order: list[str] = []
        bms_stop, vrf_stop = agent.actuator.stop, agent.vrf.stop

        async def released_bms() -> None:
            await bms_stop()
            order.append("bms")

        async def stuck_vrf() -> None:
            order.append("vrf")
            await vrf_stop()
            await asyncio.sleep(60)  # a gateway that never lets go

        agent.actuator.stop = released_bms  # type: ignore[method-assign]
        agent.vrf.stop = stuck_vrf  # type: ignore[method-assign]
        running = asyncio.create_task(agent.run())
        await holding(agent)
        began = time.perf_counter()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert time.perf_counter() - began < 8.0  # the VRF gets 5 s at most
        assert order == ["bms", "vrf"]
        assert await slot_10(agent) == [None, None, None]
    finally:
        await bms.stop()
