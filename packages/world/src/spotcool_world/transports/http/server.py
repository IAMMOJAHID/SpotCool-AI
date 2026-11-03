"""The world's HTTP app: cameras and calendar in one FastAPI app (``fastapi`` is in both extras).

``create_http_app(routers)`` builds it; ``HttpServerWorker`` (``workers/http_server.py``) serves
it, on ``WorldSettings.http_host:http_port`` in a world run.
"""

import asyncio
import ipaddress
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp

from spotcool_world import __version__

LOCAL_ORIGINS = r"http://(127\.0\.0\.1|localhost)(:\d+)?"
"""Browser origins allowed to read the API, such as the dashboard: any port on this machine."""
_RFC1918 = tuple(map(ipaddress.ip_network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))


def create_http_app(routers: Iterable[APIRouter], *, dashboard_port: int | None = None) -> FastAPI:
    """The world's one HTTP app: the routers, CORS for the dashboard and ``GET /healthz``.

    WP11 passes ``[cameras_router(cameras), calendar_router(store)]``. Pages served from
    ``http://127.0.0.1:<port>`` or ``http://localhost:<port>`` may read every GET, including the
    ``X-Camera-Stale`` header. So may the dashboard's own page on a private address
    (``is_dashboard_origin``), so a dashboard opened from another PC on the LAN reads the camera
    catalog. ``app.state.closing`` is an ``asyncio.Event`` that ``HttpServerWorker.stop()`` sets;
    streaming routes (MJPEG) end on it, so the server can stop while a dashboard is watching.

    Args:
        routers: The routers to include, in order.
        dashboard_port: The dashboard server's port (``WorldSettings.dashboard_port``), or
            ``None`` when no dashboard is served beside this app.
    """
    app = FastAPI(title="SpotCool World", version=__version__)
    app.state.closing = asyncio.Event()
    app.add_middleware(
        _PageCors,
        dashboard_port=dashboard_port,
        allow_origin_regex=LOCAL_ORIGINS,
        allow_methods=["GET"],
        expose_headers=["X-Camera-Stale"],
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Answers 200 while the server is up."""
        return {"status": "ok"}

    for router in routers:
        app.include_router(router)
    return app


def is_dashboard_origin(origin: str, dashboard_port: int | None) -> bool:
    """Whether ``origin`` is a dashboard page served on a private address of this network.

    That is ``http://<host>:<dashboard_port>`` with ``<host>`` a loopback, RFC 1918 or link-local
    IP address (IPv4 or IPv6). Host names are refused: whether one is private needs a lookup.

    Args:
        origin: A request's ``Origin`` header.
        dashboard_port: The dashboard server's port; ``None`` refuses every origin.
    """
    if dashboard_port is None:
        return False
    parts = urlsplit(origin)
    try:
        port = parts.port
    except ValueError:  # not a port number
        return False
    if parts.scheme != "http" or port != dashboard_port or parts.path or parts.username:
        return False
    try:
        address = ipaddress.ip_address(parts.hostname or "")
    except ValueError:
        return False
    return address.is_loopback or address.is_link_local or any(address in net for net in _RFC1918)


class _PageCors(CORSMiddleware):
    """Starlette's CORS, also allowing the dashboard's page on a private address."""

    def __init__(self, app: ASGIApp, *, dashboard_port: int | None, **options: Any) -> None:
        super().__init__(app, **options)
        self._dashboard_port = dashboard_port

    def is_allowed_origin(self, origin: str) -> bool:
        """``LOCAL_ORIGINS``, or ``is_dashboard_origin``."""
        return super().is_allowed_origin(origin) or is_dashboard_origin(
            origin, self._dashboard_port
        )
