"""Loopback BACnet fixtures: the BMS device and a BACpypes3 client, each on its own port.

Ports start at 47900, clear of 47808-47810 where a real device or YABE may listen; a test is
skipped when its ports cannot be bound.
"""

import asyncio
import itertools
import socket
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from bacpypes3.ipv4.app import NormalApplication
from bacpypes3.local.device import DeviceObject
from bacpypes3.pdu import IPv4Address

from spotcool_world.transports.bacnet.bms import BacnetBmsTransport

HOST = "127.0.0.1"
_PORTS = itertools.count(47900, 2)


@pytest.fixture
def ports() -> tuple[int, int]:
    """A device port and a client port on 127.0.0.1, both free now."""
    device = next(_PORTS)
    for port in (device, device + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            try:
                probe.bind((HOST, port))
            except OSError as exc:
                pytest.skip(f"cannot bind UDP {HOST}:{port}: {exc}")
    return device, device + 1


@pytest_asyncio.fixture
async def bms(ports: tuple[int, int]) -> AsyncIterator[BacnetBmsTransport]:
    """The BMS serving on 127.0.0.1, with the default /24 prefix."""
    transport = BacnetBmsTransport(address=HOST, port=ports[0])
    await transport.start()
    yield transport
    await transport.stop()


@pytest_asyncio.fixture
async def client(ports: tuple[int, int]) -> AsyncIterator[NormalApplication]:
    """A BACnet client like the agent's, on the client port."""
    device = DeviceObject(
        objectIdentifier=("device", 1999), objectName="test-client", vendorIdentifier=999
    )
    app = NormalApplication(device, IPv4Address(f"{HOST}:{ports[1]}"))
    await asyncio.gather(*app.normal.server._transport_tasks)
    yield app
    app.close()
