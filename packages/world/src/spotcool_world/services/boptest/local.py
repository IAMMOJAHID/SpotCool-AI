"""The BOPTEST plant on a local stack: Docker Desktop, or a GitHub Codespace.

Start the stack from a BOPTEST clone with ``docker compose up web worker provision``; it serves
``http://127.0.0.1:8000``. Every running test needs its own worker and a world runs two
(SpotCool and baseline), so add ``--scale worker=2``. For a Codespace, forward its port with
``gh codespace ports forward 8000:8000``.
"""

from spotcool_world.services.boptest.client import BoptestClient
from spotcool_world.services.boptest.plant import BoptestPlant

LOCAL_URL = "http://127.0.0.1:8000"
"""Where BOPTEST's compose file publishes its ``web`` service."""


class LocalBoptestPlant(BoptestPlant):
    """``BoptestPlant`` on a local BOPTEST stack.

    A local test only queues when every worker is busy, so ``select`` gives up after 180 s
    rather than the hosted 600 s.

    Args:
        base_url: Service URL.
        testcase: Test case name.
    """

    def __init__(
        self, base_url: str = LOCAL_URL, testcase: str = "multizone_office_simple_air"
    ) -> None:
        super().__init__(BoptestClient(base_url, select_wait_s=180.0), testcase)
