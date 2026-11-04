import asyncio
import json
import socketserver
import threading
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import take
from storyboard import Storyboard, load_storyboard
from websockets.asyncio.server import serve

SALT = "lM1GncleQOaCu9lT1yeUZhFYnqhsLLP1G5lAGo3ixaI="
CHALLENGE = "+IxH4CnCiqpX1rM9scsNynZzbOe4KhDeYcTNS3PDaeY="
SCENES = ("Dashboard", "Dashboard+Camera", "YABE", "PhoneCam")
# The video's own takes are recorded by hand now (AG10), so these tests keep the three kinds of
# take that take.py records: scene switches, a replay, and the phone camera.
TAKES_BOARD = """
video: {title: Takes, fps: 30, width: 1920, height: 1080, max_s: 178}
beats:
  - id: bms_yabe
    start: 0
    end: 20
    type: screen
    source:
      take: yabe_override
      in_s: 2
      scenes: [{at: 0, scene: YABE}, {at: 10, scene: Dashboard}, {at: 16, scene: YABE}]
    narration_id: null
    on_screen_text: {}
    sources: []
  - id: dashboard
    start: 20
    end: 41
    type: screen
    source:
      take: dashboard_replay
      in_s: 2
      replay: {run_id: ag3-boptest-agent-s60, speed: 2700}
      dashboard_url: http://127.0.0.1:8090/?mode=video
      scenes: [{at: 0, scene: Dashboard}]
    narration_id: null
    on_screen_text: {}
    sources: []
  - id: louvers
    start: 41
    end: 58
    type: phone
    source:
      take: phone_vrf
      in_s: 2
      dashboard_url: http://127.0.0.1:8090/?mode=video
      scenes: [{at: 0, scene: PhoneCam}, {at: 7, scene: Dashboard+Camera}]
    narration_id: null
    on_screen_text: {}
    sources: []
"""


@pytest.fixture(autouse=True)
def board(tmp_path_factory, monkeypatch) -> Storyboard:
    """The takes above, which take.py reads in place of the video's storyboard."""
    path = tmp_path_factory.mktemp("board") / "storyboard.yaml"
    path.write_text(TAKES_BOARD, "utf-8")
    loaded = load_storyboard(path)
    monkeypatch.setattr(take, "load_storyboard", lambda: loaded)
    return loaded


def test_authentication_matches_the_protocols_example():
    # The worked example in obs-websocket's protocol.md.
    expected = "1Ct943GAT+6YQUUX47Ia/ncufilbe6+oD6lY+5kaCu4="
    assert take.authentication("supersecretpassword", SALT, CHALLENGE) == expected


class FakeObs:
    """An obs-websocket v5 server: authenticates, answers requests, records them, and reports
    the recording's state as RecordStateChanged events to a client subscribed to Outputs."""

    def __init__(self, record_dir: Path, password: str | None = "pw", scenes=SCENES) -> None:
        self.record_dir, self.password, self.scenes = record_dir, password, scenes
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.arrivals: list[float] = []
        self.fail: set[str] = set()  # answered with a failure
        self.mute: set[str] = set()  # never answered
        self.start_delay = 0.0  # seconds between StartRecord's answer and OUTPUT_STARTED
        self.announce_start = True
        self.started_at: float | None = None
        self.subscriptions = 0
        self.recordings = 0
        self.url = ""
        self.tasks: list[asyncio.Task] = []

    async def handler(self, socket) -> None:
        hello: dict[str, Any] = {"obsWebSocketVersion": "5.6.2", "rpcVersion": 1}
        if self.password:
            hello["authentication"] = {"challenge": CHALLENGE, "salt": SALT}
        await socket.send(json.dumps({"op": 0, "d": hello}))
        identify = json.loads(await socket.recv())["d"]
        answer = take.authentication(self.password, SALT, CHALLENGE) if self.password else None
        if self.password and identify.get("authentication") != answer:
            await socket.close(4009, "Authentication failed.")
            return
        self.subscriptions = identify.get("eventSubscriptions", 0)
        await socket.send(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
        loop = asyncio.get_running_loop()
        async for message in socket:
            request = json.loads(message)["d"]
            kind, data = request["requestType"], request.get("requestData") or {}
            self.requests.append((kind, data))
            self.arrivals.append(loop.time())
            if kind in self.mute:
                continue
            # An event before the answer: the client must keep it aside, not take it as one.
            await self._event(socket, "CurrentProgramSceneChanged", {"sceneName": "x"})
            ok = kind not in self.fail
            status = {
                "result": ok,
                "code": 100 if ok else 600,
                "comment": None if ok else "refused",
            }
            reply = {
                "requestType": kind,
                "requestId": request["requestId"],
                "requestStatus": status,
            }
            reply["responseData"] = self.respond(kind) if ok else None
            await socket.send(json.dumps({"op": 7, "d": reply}))
            if kind == "StartRecord" and ok:
                self.tasks.append(loop.create_task(self._started(socket)))

    async def _event(self, socket, event_type: str, data: dict[str, Any]) -> None:
        event = {"eventType": event_type, "eventIntent": take.OUTPUTS_EVENTS, "eventData": data}
        await socket.send(json.dumps({"op": 5, "d": event}))

    async def _started(self, socket) -> None:
        if not self.subscriptions & take.OUTPUTS_EVENTS:
            return
        state = {"outputActive": False, "outputState": "OBS_WEBSOCKET_OUTPUT_STARTING"}
        await self._event(socket, "RecordStateChanged", state)
        await asyncio.sleep(self.start_delay)
        if self.announce_start:
            self.started_at = asyncio.get_running_loop().time()
            state = {"outputActive": True, "outputState": take.RECORD_STARTED, "outputPath": None}
            await self._event(socket, "RecordStateChanged", state)

    def respond(self, kind: str) -> dict[str, Any]:
        if kind == "GetSceneList":
            return {"scenes": [{"sceneName": name} for name in self.scenes]}
        if kind == "GetVideoSettings":
            return {
                "baseWidth": 1280,
                "baseHeight": 720,
                "outputWidth": 1280,
                "outputHeight": 720,
                "fpsNumerator": 60,
                "fpsDenominator": 1,
            }
        if kind == "StopRecord":
            self.recordings += 1
            path = self.record_dir / f"2026-11-03 10-00-0{self.recordings}.mkv"
            path.write_bytes(b"take %d" % self.recordings)
            return {"outputPath": str(path)}
        return {}

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.requests]


class Clock:
    """A clock that moves only when the take sleeps."""

    def __init__(self) -> None:
        self.now, self.said = 1000.0, []

    async def sleep(self, seconds: float) -> None:
        self.now += seconds

    def clock(self) -> float:
        return self.now


class Process:
    def __init__(self) -> None:
        self.returncode, self.terminated = None, False

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated, self.returncode = True, -15

    def wait(self, timeout=None) -> int:
        return self.returncode


@pytest_asyncio.fixture
async def obs(tmp_path):
    recorded = tmp_path / "obs"
    recorded.mkdir()
    fake = FakeObs(recorded)
    async with serve(fake.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        fake.url = f"ws://127.0.0.1:{port}"
        yield fake


def hooks(clock: Clock) -> tuple[take.Hooks, Process, list, list]:
    process, launched, ready = Process(), [], []

    def launch(args):
        launched.append(args)
        return process

    async def wait(url, proc):
        ready.append(url)

    hook = take.Hooks(
        sleep=clock.sleep, clock=clock.clock, say=clock.said.append, launch=launch, ready=wait
    )
    return hook, process, launched, ready


async def record(obs, take_id, out_dir, client_class=take.ObsClient, timeout_s=10.0, **kwargs):
    client = await client_class.connect(obs.url, "pw", timeout_s=timeout_s)
    try:
        return await take.record(
            client, take.load_storyboard().take_beat(take_id), out_dir, **kwargs
        )
    finally:
        await client.close()


@pytest.fixture(autouse=True)
def no_phone(monkeypatch):
    monkeypatch.delenv("SPOTCOOL_WORLD_LIVE_CAMERA_URL", raising=False)


@pytest.mark.asyncio
async def test_a_take_switches_scenes_on_time_and_keeps_the_recording(obs, tmp_path, board):
    beat = board.take_beat("yabe_override")
    clock = Clock()
    hook, _, launched, ready = hooks(clock)
    path = await record(obs, "yabe_override", tmp_path / "takes", hooks=hook)
    assert path == tmp_path / "takes" / "yabe_override.mkv" and path.read_bytes() == b"take 1"
    assert obs.subscriptions & take.OUTPUTS_EVENTS
    assert obs.kinds() == [
        "GetSceneList",
        "GetVideoSettings",
        "SetVideoSettings",
        "SetCurrentProgramScene",
        "StartRecord",
        "SetCurrentProgramScene",
        "SetCurrentProgramScene",
        "StopRecord",
    ]
    assert obs.requests[2][1] == {**take.VIDEO, **take.FPS}
    scenes = [data["sceneName"] for kind, data in obs.requests if kind == "SetCurrentProgramScene"]
    assert scenes == ["YABE", "Dashboard", "YABE"]
    assert "  12.0 s  scene Dashboard" in clock.said and "  18.0 s  scene YABE" in clock.said
    assert clock.now - 1000.0 == pytest.approx(beat.source.in_s + beat.duration + take.POSTROLL_S)
    assert launched == [] and ready == []


@pytest.mark.asyncio
async def test_the_cue_clock_starts_when_obs_reports_the_recording(obs, tmp_path):
    obs.start_delay = 0.3
    hook, *_ = hooks(Clock())
    await record(obs, "yabe_override", tmp_path / "takes", hooks=hook)
    after_start = obs.kinds().index("StartRecord") + 1
    assert obs.started_at is not None and obs.arrivals[after_start] >= obs.started_at


@pytest.mark.asyncio
async def test_no_record_started_event_stops_the_take(obs, tmp_path, monkeypatch):
    monkeypatch.setattr(take, "RECORD_START_TIMEOUT_S", 0.3)
    obs.announce_start = False
    hook, *_ = hooks(Clock())
    with pytest.raises(take.ObsError, match="no RecordStateChanged within 0.3 s"):
        await record(obs, "yabe_override", tmp_path / "takes", hooks=hook)
    assert obs.kinds()[-1] == "StopRecord"


@pytest.mark.asyncio
async def test_a_silent_obs_times_out(obs, tmp_path):
    obs.mute = {"GetVideoSettings"}
    with pytest.raises(take.ObsError, match="nothing for 0.3 s; waiting for GetVideoSettings"):
        await record(obs, "yabe_override", tmp_path / "takes", timeout_s=0.3)


@pytest.mark.asyncio
async def test_a_replay_take_loads_the_dashboard_as_its_beat_starts(obs, tmp_path, board):
    beat = board.take_beat("dashboard_replay")
    replay = beat.source.replay
    clock = Clock()
    hook, process, launched, ready = hooks(clock)
    agent = "http://10.0.0.5:8002"
    await record(obs, "dashboard_replay", tmp_path / "takes", agent=agent, hooks=hook)
    expected = ["replay", replay.run_id, "--wait-viewer", "60", "--speed", f"{replay.speed:g}"]
    assert launched[0][-6:] == expected
    assert ready == ["http://127.0.0.1:8090/healthz"]
    pages = [d["inputSettings"]["url"] for k, d in obs.requests if k == "SetInputSettings"]
    url = "http://127.0.0.1:8090/?mode=video&agent=http%3A%2F%2F10.0.0.5%3A8002"
    assert pages == [take.BLANK, url]  # blank until the beat, so no viewer starts it early
    kinds = obs.kinds()
    load = len(kinds) - 1 - kinds[::-1].index("SetInputSettings")
    assert kinds.index("StartRecord") < load < kinds.index("StopRecord")
    assert f"{beat.source.in_s:6.1f} s  the dashboard loads; the replay starts" in clock.said
    assert process.terminated


class LoadFails(take.ObsClient):
    """A client whose dashboard load fails: the blank page goes through, the real one not."""

    async def request(self, request_type, data=None):
        if request_type == "SetInputSettings" and data["inputSettings"].get("url") != take.BLANK:
            raise take.ObsError("SetInputSettings: the page would not load")
        return await super().request(request_type, data)


@pytest.mark.asyncio
async def test_a_failure_mid_take_stops_the_replay_even_if_stop_record_fails(obs, tmp_path):
    obs.fail = {"StopRecord"}
    clock = Clock()
    hook, process, launched, _ = hooks(clock)
    with pytest.raises(take.ObsError, match="the page would not load"):
        await record(obs, "dashboard_replay", tmp_path, client_class=LoadFails, hooks=hook)
    assert launched and process.terminated
    assert obs.kinds()[-1] == "StopRecord"  # tried, refused; the replay was stopped anyway
    assert any("could not stop OBS's recording" in line for line in clock.said)


@pytest.mark.asyncio
async def test_a_phone_take_points_the_phone_camera_at_its_stream(obs, tmp_path, monkeypatch):
    monkeypatch.setenv("SPOTCOOL_WORLD_LIVE_CAMERA_URL", "http://192.168.43.1:8080/shot.jpg")
    hook, *_ = hooks(Clock())
    first = await record(obs, "phone_vrf", tmp_path, hooks=hook)
    await record(obs, "phone_vrf", tmp_path, hooks=hook)
    phone = [
        d for k, d in obs.requests if k == "SetInputSettings" and d["inputName"] == take.PHONE_INPUT
    ]
    assert phone[0]["inputSettings"] == {"input": "http://192.168.43.1:8080/video"}
    assert first.read_bytes() == b"take 2"
    assert (tmp_path / "phone_vrf.previous.mkv").read_bytes() == b"take 1"


@pytest.mark.asyncio
async def test_missing_scenes_and_a_wrong_password_are_refused(obs, tmp_path):
    obs.scenes = ("Dashboard",)
    with pytest.raises(take.ObsError, match="no scene YABE: import"):
        await record(obs, "yabe_override", tmp_path)
    with pytest.raises(take.ObsError, match="password"):
        await take.ObsClient.connect(obs.url, "wrong")
    with pytest.raises(take.ObsError, match="OBS_WS_PASSWORD"):
        await take.ObsClient.connect(obs.url, None)


class _NotObs(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.recv(4096)
        self.request.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")


def test_main_reports_a_server_that_is_not_obs_in_one_line(capsys, monkeypatch):
    monkeypatch.setenv(take.PASSWORD_ENV, "pw")
    with socketserver.TCPServer(("127.0.0.1", 0), _NotObs) as server:
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        port = server.server_address[1]
        assert take.main(["yabe_override", "--yes", "--url", f"ws://127.0.0.1:{port}"]) == 1
        thread.join(5)
    err = capsys.readouterr().err.strip()
    assert err.startswith("take: InvalidStatus") and "\n" not in err


def test_dry_run_prints_the_schedule(capsys):
    assert take.main(["dashboard_replay", "--dry-run", "--agent", "http://10.0.0.5:8002"]) == 0
    out = capsys.readouterr().out
    assert "replay" in out and "--speed 2700" in out and "stop" in out
    assert "load http://127.0.0.1:8090/?mode=video&agent=http%3A%2F%2F10.0.0.5%3A8002" in out
    with pytest.raises(SystemExit):
        take.main(["no_such_take", "--dry-run"])
