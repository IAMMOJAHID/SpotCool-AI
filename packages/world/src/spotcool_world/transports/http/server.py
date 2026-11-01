"""The world's HTTP app: cameras and calendar in one FastAPI app (``fastapi`` is in both extras).

``create_http_app(routers)`` builds it; ``HttpServerWorker`` (``workers/http_server.py``) serves
it, on ``WorldSettings.http_host:http_port`` in a world run.
"""

import asyncio
from collections.abc import Iterable

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from spotcool_world import __version__

LOCAL_ORIGINS = r"http://(127\.0\.0\.1|localhost)(:\d+)?"
"""Browser origins allowed to read the API, such as the dashboard: any port on this machine."""


def create_http_app(routers: Iterable[APIRouter]) -> FastAPI:
    """The world's one HTTP app: the routers, CORS for the dashboard and ``GET /healthz``.

    WP11 passes ``[cameras_router(cameras), calendar_router(store)]``. Pages served from
    ``http://127.0.0.1:<port>`` or ``http://localhost:<port>`` may read every GET, including the
    ``X-Camera-Stale`` header. ``app.state.closing`` is an ``asyncio.Event`` that
    ``HttpServerWorker.stop()`` sets; streaming routes (MJPEG) end on it, so the server can stop
    while a dashboard is watching.

    Args:
        routers: The routers to include, in order.
    """
    app = FastAPI(title="SpotCool World", version=__version__)
    app.state.closing = asyncio.Event()
    app.add_middleware(
        CORSMiddleware,
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
