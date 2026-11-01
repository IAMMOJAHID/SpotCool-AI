"""The BOPTEST plant on the public service, ``http://api.boptest.net``.

The service is shared and free, and its queue can be slow (a select queued past 900 s on
1 November 2026): ``select`` waits up to 600 s, then stops the queued test and fails.
"""

from spotcool_world.services.boptest.client import BoptestClient
from spotcool_world.services.boptest.plant import BoptestPlant

HOSTED_URL = "http://api.boptest.net"
"""Plain HTTP: the service's HTTPS certificate expired on 26 October 2026."""


class HostedBoptestPlant(BoptestPlant):
    """``BoptestPlant`` on the hosted BOPTEST service.

    Args:
        base_url: Service URL.
        testcase: Test case name.
    """

    def __init__(
        self, base_url: str = HOSTED_URL, testcase: str = "multizone_office_simple_air"
    ) -> None:
        super().__init__(BoptestClient(base_url, select_wait_s=600.0), testcase)
