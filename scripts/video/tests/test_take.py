import json
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import take
from storyboard import load_storyboard
from websockets.asyncio.server import serve

SALT = "lM1GncleQOaCu9lT1yeUZhFYnqhsLLP1G5lAGo3ixaI="
CHALLENGE = "+IxH4CnCiqpX1rM9scsNynZzbOe4KhDeYcTNS3PDaeY="
SCENES = ("Dashboard", "Dashboard+Camera", "YABE", "PhoneCam")


def test_authentication_matches_the_protocols_example():
    # The worked example in obs-websocket's protocol.md.
    expected = "1Ct943GAT+6YQUUX47Ia/ncufilbe6+oD6lY+5kaCu4="
    assert take.authentication("supersecretpassword", SALT, CHALLENGE) == expected


class FakeObs:
    """An obs-websocket v5 server: authenticates, answers requests, records them."""

    def __init__(self, record_dir: Path, password: str | None = "pw", scenes=SCENES) -> None:
        self.record_dir, self.password, self.scenes = record_dir, password, scenes
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.fail: set[str] = set()
        self.recordings = 0

    async def handler(self, socket) -> None:
        hello: dict[str, Any] = {"obsWebSocketVersion": "5.6.2", "rpcVersion": 1}
        if self.password:
            hello["authentication"] = {"challenge": CHALLENGE, "salt": SALT}
        await socket.send(json.dumps({"op": 0, "d": hello}))
        identify = json.loads(await socket.recv())["d"]
        if self.password and identify.get("authentication") != take.authentication(
            self.password, SALT, CHALLENGE
        ):
            await socket.close(4009, "Authentication failed.")
            return
        await socket.send(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
        async for message in socket:
            request = json.loads(message)["d"]
            kind, data = request["requestType"], request.get("requestData") or {}
            self.requests.append((kind, data))
            # An event before the answer: the client must skip it.
            await socket.send(
                json.dumps({"op": 5, "d": {"eventType": "CurrentProgramSceneChanged"}})
            )
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


def hooks(clock: Clock, launched: list, ready: list) -> tuple[take.Hooks, Process]:
    process = Process()

    def launch(args):
        launched.append(args)
        return process

    async def wait(url, proc):
        ready.append(url)

    return take.Hooks(
        sleep=clock.sleep, clock=clock.clock, say=clock.said.append, launch=launch, ready=wait
    ), process


@pytest.mark.asyncio
async def test_a_take_switches_scenes_on_time_and_keeps_the_recording(obs, tmp_path, monkeypatch):
    monkeypatch.delenv("SPOTCOOL_WORLD_LIVE_CAMERA_URL", raising=False)
    beat = load_storyboard().take_beat("yabe_override")
    clock, launched, ready = Clock(), [], []
    hook, _ = hooks(clock, launched, ready)
    client = await take.ObsClient.connect(obs.url, "pw")
    try:
        path = await take.record(client, beat, tmp_path / "takes", hooks=hook)
    finally:
        await client.close()
    assert path == tmp_path / "takes" / "yabe_override.mkv" and path.read_bytes() == b"take 1"
    assert obs.kinds() == [
        "GetSceneList", "GetVideoSettings", "SetVideoSettings", "SetCurrentProgramScene", "StartRecord",
        "SetCurrentProgramScene", "SetCurrentProgramScene", "StopRecord",
    ]  # fmt: skip
    assert obs.requests[2][1] == {**take.VIDEO, **take.FPS}
    scenes = [data["sceneName"] for kind, data in obs.requests if kind == "SetCurrentProgramScene"]
    assert scenes == ["YABE", "Dashboard", "YABE"]
    assert "  12.0 s  scene Dashboard" in clock.said and "  18.0 s  scene YABE" in clock.said
    assert clock.now - 1000.0 == pytest.approx(beat.source.in_s + beat.duration + take.POSTROLL_S)
    assert launched == [] and ready == []


@pytest.mark.asyncio
async def test_a_replay_take_starts_the_replay_and_reloads_the_dashboard(
    obs, tmp_path, monkeypatch
):
    monkeypatch.delenv("SPOTCOOL_WORLD_LIVE_CAMERA_URL", raising=False)
    beat = load_storyboard().take_beat("dashboard_replay")
    clock, launched, ready = Clock(), [], []
    hook, process = hooks(clock, launched, ready)
    client = await take.ObsClient.connect(obs.url, "pw")
    try:
        await take.record(
            client, beat, tmp_path / "takes", agent="http://10.0.0.5:8002", hooks=hook
        )
    finally:
        await client.close()
    assert launched[0][-6:] == [
        "replay",
        "eval-live-hybrid-20261103-122941",
        "--wait-viewer",
        "60",
        "--speed",
        "1800",
    ]
    assert ready == ["http://127.0.0.1:8090/healthz"]
    settings = dict(obs.requests)["SetInputSettings"]
    assert settings == {
        "inputName": take.DASHBOARD_INPUT,
        "inputSettings": {"url": "http://127.0.0.1:8090/?mode=video&agent=http://10.0.0.5:8002"},
    }
    kinds = obs.kinds()
    assert (
        kinds.index("StartRecord")
        < kinds.index("PressInputPropertiesButton")
        < kinds.index("StopRecord")
    )
    assert process.terminated


@pytest.mark.asyncio
async def test_a_failure_mid_take_still_stops_recording_and_the_replay(obs, tmp_path, monkeypatch):
    monkeypatch.setenv("SPOTCOOL_WORLD_LIVE_CAMERA_URL", "http://192.168.43.1:8080/shot.jpg")
    obs.fail = {"PressInputPropertiesButton"}
    beat = load_storyboard().take_beat("dashboard_replay")
    clock, launched, ready = Clock(), [], []
    hook, process = hooks(clock, launched, ready)
    client = await take.ObsClient.connect(obs.url, "pw")
    try:
        with pytest.raises(take.ObsError, match="PressInputPropertiesButton: refused"):
            await take.record(client, beat, tmp_path / "takes", hooks=hook)
    finally:
        await client.close()
    assert obs.kinds()[-1] == "StopRecord" and process.terminated
    assert not (tmp_path / "takes").exists()


@pytest.mark.asyncio
async def test_a_phone_take_points_the_phone_camera_at_its_stream(obs, tmp_path, monkeypatch):
    monkeypatch.setenv("SPOTCOOL_WORLD_LIVE_CAMERA_URL", "http://192.168.43.1:8080/shot.jpg")
    clock, launched, ready = Clock(), [], []
    hook, _ = hooks(clock, launched, ready)
    client = await take.ObsClient.connect(obs.url, "pw")
    try:
        first = await take.record(
            client, load_storyboard().take_beat("phone_vrf"), tmp_path, hooks=hook
        )
        await take.record(client, load_storyboard().take_beat("phone_vrf"), tmp_path, hooks=hook)
    finally:
        await client.close()
    phone = [
        d for k, d in obs.requests if k == "SetInputSettings" and d["inputName"] == take.PHONE_INPUT
    ]
    assert phone[0]["inputSettings"] == {"input": "http://192.168.43.1:8080/video"}
    assert (
        first.read_bytes() == b"take 2"
        and (tmp_path / "phone_vrf.previous.mkv").read_bytes() == b"take 1"
    )


@pytest.mark.asyncio
async def test_missing_scenes_and_a_wrong_password_are_refused(obs, tmp_path):
    obs.scenes = ("Dashboard",)
    client = await take.ObsClient.connect(obs.url, "pw")
    try:
        with pytest.raises(take.ObsError, match="no scene YABE: import"):
            await take.record(client, load_storyboard().take_beat("yabe_override"), tmp_path)
    finally:
        await client.close()
    with pytest.raises(take.ObsError, match="password"):
        await take.ObsClient.connect(obs.url, "wrong")
    with pytest.raises(take.ObsError, match="OBS_WS_PASSWORD"):
        await take.ObsClient.connect(obs.url, None)


def test_dry_run_prints_the_schedule(capsys):
    assert take.main(["dashboard_replay", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "replay" in out and "--speed 1800" in out and "stop" in out
    with pytest.raises(SystemExit):
        take.main(["no_such_take", "--dry-run"])
