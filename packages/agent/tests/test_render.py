"""Muse Image's privacy renders, the render loop and the server's ``/privacy``, on fakes."""

import asyncio
import base64
import io
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from loguru import logger
from openai import AsyncOpenAI
from PIL import Image
from pydantic import SecretStr

from spotcool_agent.loops.render import RenderLoop
from spotcool_agent.runner.agent import create_render
from spotcool_agent.services.fake.render import FakeRender, solid_jpeg
from spotcool_agent.services.meta.client import MetaClient
from spotcool_agent.services.meta.render import PRIVACY_RENDER_PROMPT, MuseImageRender
from spotcool_agent.services.render import RenderError
from spotcool_agent.settings import AgentSettings
from spotcool_agent.transports.server import create_app, dashboard_origins
from spotcool_agent.transports.world_http import WorldHttp, WorldHttpError
from spotcool_contracts import ZONES, Zone
from spotcool_contracts.cameras import CameraCatalog, CameraInfo, CameraSource

CAMERAS = [*(ZONES[zone].camera_id for zone in Zone), "cam-meeting-live"]


def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (80, 45), (200, 180, 120)).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeImages:
    def __init__(self, b64: str | None, delay_s: float = 0.0) -> None:
        self.b64 = b64
        self.delay_s = delay_s
        self.requests: list[dict[str, Any]] = []

    async def edit(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        await asyncio.sleep(self.delay_s)
        answer = SimpleNamespace(data=[SimpleNamespace(b64_json=self.b64, url=None)])
        headers = {"x-request-id": "rid-image"}
        return SimpleNamespace(headers=headers, status_code=200, parse=lambda: answer)

    @property
    def with_raw_response(self) -> "FakeImages":
        return self


async def _none() -> None:
    return None


def muse(images: FakeImages, call_cap: int | None = None) -> MuseImageRender:
    fake = SimpleNamespace(images=images, close=_none)
    client = MetaClient(
        SecretStr("sk-x"),
        base_url="https://api.test/v1",
        max_concurrency=1,
        call_cap=call_cap,
        make_openai=lambda: cast(AsyncOpenAI, fake),
    )
    return MuseImageRender(client)


@pytest.mark.asyncio
async def test_muse_image_sends_as_prompt_and_traces_only_sizes() -> None:
    images = FakeImages(base64.b64encode(png()).decode())
    logged: list[str] = []
    sink = logger.add(lambda message: logged.append(message.record["message"]), level="INFO")
    try:
        render = await muse(images).render(solid_jpeg((64, 48)), 30.0, camera_id="cam-sou")
    finally:
        logger.remove(sink)
    (request,) = images.requests
    assert request["model"] == "muse-image-1.0" and request["n"] == 1
    assert request["prompt"] == PRIVACY_RENDER_PROMPT
    assert "faceless" in PRIVACY_RENDER_PROMPT
    assert request["image"][0] == "frame.jpg" and request["image"][2] == "image/jpeg"
    with Image.open(io.BytesIO(render)) as kept:  # a PNG answer is kept as a JPEG
        assert (kept.format, kept.size) == ("JPEG", (80, 45))
    assert logged[0] == (
        "META -> muse-image-1.0 | POST https://api.test/v1/images/edits | privacy render of "
        "cam-sou | in: JPEG 64x48, 1 KB + prompt 324 characters"
    )
    assert logged[1].startswith("META <- muse-image-1.0 | 200 in ")
    assert "request-id rid-image" in logged[1] and "out: PNG 80x45, 0 KB" in logged[1]
    assert not any("/9j/" in line or "iVBOR" in line for line in logged)


@pytest.mark.asyncio
async def test_an_answer_without_an_image_or_too_late_is_a_render_error() -> None:
    with pytest.raises(RenderError, match="has no image"):
        await muse(FakeImages(None)).render(solid_jpeg(), 5.0, camera_id="c")
    with pytest.raises(RenderError, match="not base64"):
        await muse(FakeImages("not base64!")).render(solid_jpeg(), 5.0, camera_id="c")
    with pytest.raises(RenderError, match="not an image"):
        await muse(FakeImages(base64.b64encode(b"text").decode())).render(
            solid_jpeg(), 5.0, camera_id="c"
        )
    with pytest.raises(RenderError, match="no answer within 0.1 s"):
        slow = FakeImages(base64.b64encode(png()).decode(), delay_s=2.0)
        await muse(slow).render(solid_jpeg(), 0.1, camera_id="c")
    with pytest.raises(RenderError, match="not an image"):
        await muse(FakeImages(None)).render(b"no", 5.0, camera_id="c")


class FakeWorld:
    """The cameras listed (a snapshot that fails for ``broken``)."""

    def __init__(self, listed: list[str], broken: str | None = None) -> None:
        self.listed = listed
        self.broken = broken
        self.snapshots: list[str] = []

    async def cameras(self) -> CameraCatalog:
        cameras = [
            CameraInfo(
                id=camera,
                name=camera,
                zone=None,
                snapshot_url=f"http://world/{camera}",
                width=64,
                height=48,
                fps_hint=5,
                source=CameraSource.SCENARIO,
            )
            for camera in self.listed
        ]
        return CameraCatalog(cameras=cameras)

    async def snapshot(self, url: str, timeout_s: float = 5.0) -> bytes:
        camera = url.rsplit("/", 1)[1]
        self.snapshots.append(camera)
        if camera == self.broken:
            raise WorldHttpError("HTTP 503")
        return solid_jpeg()


@pytest.mark.asyncio
async def test_a_round_renders_each_camera_once_in_order_from_a_fresh_snapshot() -> None:
    world = FakeWorld([c for c in CAMERAS if c != "cam-nor"], broken="cam-eas")
    render = FakeRender()
    loop = RenderLoop(render, cast(WorldHttp, world), cameras=CAMERAS)
    assert await loop.round()
    unlisted_and_broken = {"cam-nor", "cam-eas"}
    assert render.asked == [c for c in CAMERAS if c not in unlisted_and_broken]
    assert render.asked[-1] == "cam-meeting-live"  # the meeting room's last
    assert set(loop.latest) == set(render.asked)
    assert world.snapshots == [c for c in CAMERAS if c != "cam-nor"]


@pytest.mark.asyncio
async def test_a_round_that_sent_nothing_retries_soon_and_a_failed_call_waits_the_full_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("spotcool_agent.loops.render.RETRY_S", 0.01)

    class NoFrames(FakeWorld):  # the agent starts first: no camera has a frame yet
        async def snapshot(self, url: str, timeout_s: float = 5.0) -> bytes:
            self.snapshots.append(url)
            if len(self.snapshots) <= 2:
                raise WorldHttpError("HTTP 503")
            return solid_jpeg()

    render = FakeRender()
    loop = RenderLoop(
        render, cast(WorldHttp, NoFrames(["cam-sou"])), cameras=["cam-sou"], every_s=60
    )
    running = asyncio.create_task(loop.run())
    await asyncio.sleep(0.2)  # far under every_s
    assert render.asked == ["cam-sou"]  # two rounds sent nothing, the third rendered, then it waits
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)

    images = FakeImages(None)  # every call fails, and every call costs budget
    world = cast(WorldHttp, FakeWorld(["cam-sou"]))
    loop = RenderLoop(muse(images), world, cameras=["cam-sou"], every_s=60)
    running = asyncio.create_task(loop.run())
    await asyncio.sleep(0.2)
    assert len(images.requests) == 1 and loop.latest == {}
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
async def test_the_loop_stops_at_its_call_cap_and_keeps_the_renders() -> None:
    images = FakeImages(base64.b64encode(png()).decode())
    loop = RenderLoop(
        muse(images, call_cap=3), cast(WorldHttp, FakeWorld(CAMERAS)), cameras=CAMERAS, every_s=0
    )
    await asyncio.wait_for(loop.run(), 5)  # it ends, it doesn't spin
    assert len(images.requests) == 3
    assert list(loop.latest) == CAMERAS[:3]


@pytest.mark.asyncio
async def test_privacy_is_404_before_the_first_render_then_the_latest_with_cors() -> None:
    renders: dict[str, bytes] = {}
    origins = dashboard_origins("192.168.1.20", 8090)
    app = create_app(asyncio.Queue(), origins=origins, renders=renders)
    transport = httpx.ASGITransport(app=app)
    origin = {"Origin": "http://192.168.1.20:8090"}
    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as http:
        assert (await http.get("/privacy/cam-sou.jpg", headers=origin)).status_code == 404
        renders["cam-sou"] = solid_jpeg()
        response = await http.get("/privacy/cam-sou.jpg", headers=origin)
        assert response.status_code == 200 and response.content == solid_jpeg()
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["access-control-allow-origin"] == origin["Origin"]
        renders["cam-sou"] = newer = solid_jpeg(colour=(10, 10, 10))
        assert (await http.get("/privacy/cam-sou.jpg")).content == newer
        assert (await http.get("/privacy/cam-wes.jpg")).status_code == 404
    bare = create_app(asyncio.Queue(), origins=origins)  # no renderer: never a render
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=bare), base_url="http://agent"
    ) as http:
        assert (await http.get("/privacy/cam-sou.jpg")).status_code == 404


def test_render_is_off_by_default_even_with_meta_vision_and_meta_needs_the_key() -> None:
    settings = AgentSettings(vision="meta", world_host="127.0.0.1")
    assert (settings.render, settings.render_call_cap, settings.render_every_s) == (
        "off",
        60,
        180.0,
    )
    assert create_render(settings) == (None, None)
    with pytest.raises(ValueError, match="--render meta needs META_API_KEY"):
        create_render(AgentSettings(render="meta", world_host="127.0.0.1"))
