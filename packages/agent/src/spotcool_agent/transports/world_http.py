"""WorldHttp: the building's HTTP side, as a real site would serve it: cameras and the calendar.

- ``GET /cameras``: the camera catalog (``CameraCatalog``), its snapshot URLs absolute;
- ``GET <snapshot_url>``: one JPEG, kept in memory only and never written anywhere;
- ``GET /calendar/rooms/{room}/schedule``: a room's bookings (``ScheduleInformation``).

Every answer is checked against its contract model.
"""

from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from spotcool_contracts.calendar import SCHEDULE_PATH, ScheduleInformation
from spotcool_contracts.cameras import CATALOG_PATH, CameraCatalog

Model = TypeVar("Model", bound=BaseModel)
TIMEOUT_S = 5.0
"""The default timeout of a request, in wall seconds."""


class WorldHttpError(Exception):
    """A request to the world's HTTP server failed or answered something unusable."""


class WorldHttp:
    """A client of the world's camera and calendar server.

    Args:
        base_url: The server, such as ``http://127.0.0.1:8081``.
        client: The HTTP client to use; one is made (and closed by ``aclose()``) by default.
    """

    def __init__(self, base_url: str, *, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_S)
        self._owned = client is None

    async def cameras(self) -> CameraCatalog:
        """The camera catalog.

        Raises:
            WorldHttpError: If the request failed or the answer is not a catalog.
        """
        return _parsed(CameraCatalog, await self._get(self.base_url + CATALOG_PATH))

    async def snapshot(self, url: str, timeout_s: float = TIMEOUT_S) -> bytes:
        """One camera snapshot, as JPEG bytes in memory.

        Raises:
            WorldHttpError: If the request failed, or the camera has no frame yet (503).
        """
        return await self._get(url, timeout_s)

    async def schedule(self, room: str) -> ScheduleInformation:
        """A room's bookings.

        Raises:
            WorldHttpError: If the request failed or the answer is not a schedule.
        """
        url = self.base_url + SCHEDULE_PATH.format(room=room)
        return _parsed(ScheduleInformation, await self._get(url))

    async def aclose(self) -> None:
        """Close the HTTP client, if this object made it."""
        if self._owned:
            await self._client.aclose()

    async def _get(self, url: str, timeout_s: float = TIMEOUT_S) -> bytes:
        try:
            response = await self._client.get(url, timeout=timeout_s)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise WorldHttpError(f"GET {url}: HTTP {exc.response.status_code}") from None
        except httpx.HTTPError as exc:
            raise WorldHttpError(f"GET {url}: {type(exc).__name__}") from None
        return response.content


def _parsed(model: type[Model], body: bytes) -> Model:
    """An answer's body as its contract model.

    Raises:
        WorldHttpError: If it does not match.
    """
    try:
        return model.model_validate_json(body)
    except ValidationError as exc:
        raise WorldHttpError(f"not a {model.__name__}: {exc.error_count()} errors") from None
