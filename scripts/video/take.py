"""Record one scripted OBS take of a storyboard beat, over obs-websocket v5.

    uv run python scripts/video/take.py TAKE_ID [--agent URL] [--url WS] [--yes] [--dry-run]

TAKE_ID is a beat's ``source.take`` in ``video/storyboard.yaml`` (``yabe_override``,
``dashboard_replay``, ``phone_vrf``). The take is the beat plus ``in_s`` of pre-roll and
``POSTROLL_S`` after. The script:

1. prints what must be ready (the beat's ``before``) and waits for Enter (``--yes`` skips);
2. checks OBS has the scenes (import ``video/obs/scene_collection.json`` once), sets the
   canvas and output to 1920x1080 at 30 fps, points the dashboard browser source at the beat's
   ``dashboard_url`` (plus ``&agent=`` with ``--agent``) and the phone camera at
   ``$SPOTCOOL_WORLD_LIVE_CAMERA_URL``'s video stream;
3. for a replay beat, starts ``spotcool-world replay <run_id> --wait-viewer 60 --speed <x>``
   (``$SPOTCOOL_WORLD_RUN_DIR`` must name the runs) and waits for its dashboard;
4. starts recording, reloads the dashboard (the replay starts when it connects), switches the
   scenes and prints the cues at the storyboard's times, and stops recording;
5. moves the recording to ``video/takes/<TAKE_ID>.<ext>``, keeping the one it replaces as
   ``<TAKE_ID>.previous.<ext>``.

OBS: Tools > WebSocket Server Settings > Enable WebSocket server; the password goes in
``$OBS_WS_PASSWORD``. ``--dry-run`` prints the schedule without OBS.
"""

import argparse
import asyncio
import base64
import hashlib
import itertools
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from storyboard import OBS_COLLECTION, TAKES, Beat, Replay, load_storyboard
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, WebSocketException

OBS_URL = "ws://127.0.0.1:4455"
PASSWORD_ENV = "OBS_WS_PASSWORD"
DASHBOARD_INPUT = "Dashboard browser"
PHONE_INPUT = "Phone camera"
PHONE_SCENES = ("PhoneCam", "Dashboard+Camera")
POSTROLL_S = 1.0
READY_TIMEOUT_S = 60.0
RECV_TIMEOUT_S = 10.0
"""The longest the take waits for any message from OBS."""
RECORD_START_TIMEOUT_S = 10.0
"""The longest the take waits for OBS to report that the recording has started."""
OUTPUTS_EVENTS = 1 << 6
"""obs-websocket's ``EventSubscription::Outputs``, which carries ``RecordStateChanged``."""
RECORD_STARTED = "OBS_WEBSOCKET_OUTPUT_STARTED"
BLANK = "about:blank"
"""Where a replay take's dashboard waits, so no viewer starts the replay before the beat."""
VIDEO = {"baseWidth": 1920, "baseHeight": 1080, "outputWidth": 1920, "outputHeight": 1080}
FPS = {"fpsNumerator": 30, "fpsDenominator": 1}


class ObsError(RuntimeError):
    """OBS refused a request, or the take cannot run."""


def authentication(password: str, salt: str, challenge: str) -> str:
    """obs-websocket v5's answer to its challenge: base64 SHA-256, twice."""
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


class ObsClient:
    """A minimal obs-websocket v5 client: identify, requests one at a time, and the Outputs
    events (``RecordStateChanged``), kept until ``wait_event`` asks for them."""

    def __init__(self, socket: ClientConnection, timeout_s: float = RECV_TIMEOUT_S) -> None:
        self._socket = socket
        self._ids = itertools.count(1)
        self._events: list[dict[str, Any]] = []
        self._timeout_s = timeout_s

    @classmethod
    async def connect(
        cls, url: str, password: str | None, timeout_s: float = RECV_TIMEOUT_S
    ) -> "ObsClient":
        """Connect and identify, subscribed to the Outputs events.

        Raises:
            ObsError: If OBS wants a password and none is given, refuses the one given, or
                says nothing for ``timeout_s``.
            OSError: If nothing listens at ``url``.
            websockets.exceptions.WebSocketException: If the server is not obs-websocket.
        """
        socket = await connect(url, max_size=None, open_timeout=timeout_s)
        client = cls(socket, timeout_s)
        try:
            hello = await client._receive("its hello")
            identify: dict[str, Any] = {"rpcVersion": 1, "eventSubscriptions": OUTPUTS_EVENTS}
            auth = hello.get("d", {}).get("authentication")
            if auth:
                if not password:
                    raise ObsError(f"OBS asks for a password: set {PASSWORD_ENV}")
                identify["authentication"] = authentication(
                    password, auth["salt"], auth["challenge"]
                )
            await socket.send(json.dumps({"op": 1, "d": identify}))
            identified = await client._receive("its identification")
        except ConnectionClosed as exc:
            await socket.close()
            raise ObsError(
                f"OBS closed the connection ({exc.rcvd}): is the password right?"
            ) from exc
        except ObsError:
            await socket.close()
            raise
        if identified.get("op") != 2:
            await socket.close()
            raise ObsError(f"OBS did not identify the client: {identified}")
        return client

    async def _receive(self, waiting_for: str, timeout_s: float | None = None) -> dict[str, Any]:
        """The next message, an event kept aside; waits at most ``timeout_s`` (default 10 s)."""
        limit = self._timeout_s if timeout_s is None else timeout_s
        try:
            message = json.loads(await asyncio.wait_for(self._socket.recv(), limit))
        except TimeoutError as exc:
            raise ObsError(f"OBS sent nothing for {limit:g} s; waiting for {waiting_for}") from exc
        if message.get("op") == 5:
            self._events.append(message.get("d") or {})
        return message

    async def request(
        self, request_type: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send one request and return its response data.

        Raises:
            ObsError: If OBS reports a failure, or does not answer within the timeout.
        """
        request_id = str(next(self._ids))
        message = {"requestType": request_type, "requestId": request_id, "requestData": data or {}}
        await self._socket.send(json.dumps({"op": 6, "d": message}))
        while True:
            reply = await self._receive(request_type)
            if reply.get("op") == 7 and reply["d"].get("requestId") == request_id:
                break
        status = reply["d"]["requestStatus"]
        if not status.get("result"):
            raise ObsError(f"{request_type}: {status.get('comment') or status.get('code')}")
        return reply["d"].get("responseData") or {}

    async def wait_event(
        self, event_type: str, match: Callable[[dict[str, Any]], bool], timeout_s: float
    ) -> dict[str, Any]:
        """Wait for a subscribed event whose data matches; one that came during a request counts.

        Raises:
            ObsError: If none comes within ``timeout_s``.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            for index, event in enumerate(self._events):
                if event.get("eventType") == event_type and match(event.get("eventData") or {}):
                    del self._events[: index + 1]
                    return event
            self._events.clear()
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ObsError(f"OBS sent no {event_type} within {timeout_s:g} s")
            try:
                await self._receive(event_type, remaining)
            except ObsError as exc:
                raise ObsError(f"OBS sent no {event_type} within {timeout_s:g} s") from exc

    async def close(self) -> None:
        """Close the connection."""
        await self._socket.close()


def replay_command(replay: Replay) -> list[str]:
    """``spotcool-world replay`` for a take: waits up to 60 s for the dashboard to connect."""
    exe = shutil.which("spotcool-world")
    base = [exe] if exe else [sys.executable, "-m", "spotcool_world.runner.cli"]
    return [*base, "replay", replay.run_id, "--wait-viewer", "60", "--speed", f"{replay.speed:g}"]


def health_url(dashboard_url: str) -> str:
    """The dashboard server's ``/healthz``."""
    parts = urlsplit(dashboard_url)
    return f"{parts.scheme}://{parts.netloc}/healthz"


def _status(url: str) -> int:
    with urllib.request.urlopen(url, timeout=2) as response:
        return response.status


async def wait_ready(url: str, process: Any, timeout_s: float = READY_TIMEOUT_S) -> None:
    """Wait until ``url`` answers 200.

    Raises:
        ObsError: If the process exits first, or ``timeout_s`` passes.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise ObsError(f"the replay exited ({process.returncode}) before its dashboard was up")
        try:
            if await asyncio.to_thread(_status, url) == 200:
                return
        except OSError:
            pass
        await asyncio.sleep(0.5)
    raise ObsError(f"no dashboard at {url} after {timeout_s:g} s")


@dataclass
class Hooks:
    """What a take does to the world outside OBS; the tests replace each.

    Parameters:
        sleep: Waits so many seconds.
        clock: Seconds, monotonic.
        say: Prints a line for the person at the keyboard.
        launch: Starts a process from its arguments.
        ready: Waits until a URL answers, or the process dies.
    """

    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    clock: Callable[[], float] = time.monotonic
    say: Callable[[str], None] = print
    launch: Callable[[list[str]], Any] = subprocess.Popen
    ready: Callable[[str, Any], Awaitable[None]] = wait_ready


_ORDER = {"load": 0, "scene": 1, "cue": 2, "stop": 3}


def schedule(beat: Beat, url: str | None = None) -> list[tuple[float, str, str]]:
    """The take's events as ``(seconds after the recording started, kind, value)``.

    The scenes and cues, the stop ``POSTROLL_S`` after the beat, and, for a replay take, the
    dashboard loading as the beat starts: the replay starts when that page connects, so the
    whole recorded day plays inside the beat (``storyboard_check`` checks the speed allows it).
    """
    source = beat.source
    events = [(source.in_s + s.at, "scene", s.scene) for s in source.scenes[1:]]
    events += [(source.in_s + c.at, "cue", c.say) for c in source.cues]
    if source.replay and url:
        events.append((source.in_s, "load", url))
    events.append((source.in_s + beat.duration + POSTROLL_S, "stop", ""))
    return sorted(events, key=lambda e: (e[0], _ORDER[e[1]]))


def dashboard_url(beat: Beat, agent: str | None) -> str | None:
    """The beat's dashboard URL, with ``&agent=`` (URL-encoded) for SpotCool's agent."""
    url = beat.source.dashboard_url
    if url and agent:
        url += "&agent=" + quote(agent, safe="")
    return url


def stop_replay(process: Any) -> None:
    """End the replay if it still runs (it holds the dashboard's port)."""
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()


def phone_stream(environ: Mapping[str, str] = os.environ) -> str | None:
    """IP Webcam's video stream, from the snapshot URL the world reads (``/shot.jpg``)."""
    shot = environ.get("SPOTCOOL_WORLD_LIVE_CAMERA_URL")
    return f"{shot.rsplit('/', 1)[0]}/video" if shot else None


async def record(
    client: ObsClient,
    beat: Beat,
    out_dir: Path = TAKES,
    agent: str | None = None,
    hooks: Hooks | None = None,
) -> Path:
    """Record the beat's take and move it to ``out_dir``; return its path.

    Raises:
        ObsError: If OBS lacks a scene, refuses a request, or the replay fails to start.
    """
    hooks = hooks or Hooks()
    source = beat.source
    take = source.take
    assert take is not None
    scenes = {scene["sceneName"] for scene in (await client.request("GetSceneList"))["scenes"]}
    missing = sorted({s.scene for s in source.scenes} - scenes)
    if missing:
        raise ObsError(
            f"OBS has no scene {', '.join(missing)}: import {OBS_COLLECTION} "
            "(Scene Collection > Import)"
        )
    current = await client.request("GetVideoSettings")
    if any(current.get(key) != value for key, value in {**VIDEO, **FPS}.items()):
        await client.request("SetVideoSettings", {**VIDEO, **FPS})
    url = dashboard_url(beat, agent)
    if source.replay and not url:
        raise ObsError(f"take {take} replays a run but has no dashboard_url")
    if url:  # a replay take's dashboard waits on a blank page until its beat starts
        page = BLANK if source.replay else url
        await client.request(
            "SetInputSettings", {"inputName": DASHBOARD_INPUT, "inputSettings": {"url": page}}
        )
    stream = phone_stream()
    if stream and any(s.scene in PHONE_SCENES for s in source.scenes):
        await client.request(
            "SetInputSettings", {"inputName": PHONE_INPUT, "inputSettings": {"input": stream}}
        )
    await client.request("SetCurrentProgramScene", {"sceneName": source.scenes[0].scene})

    events = schedule(beat, url)
    process = None
    recording = False
    try:
        if source.replay and url:
            process = hooks.launch(replay_command(source.replay))
            await hooks.ready(health_url(url), process)
        await client.request("StartRecord")
        recording = True
        await client.wait_event(
            "RecordStateChanged",
            lambda data: data.get("outputState") == RECORD_STARTED,
            RECORD_START_TIMEOUT_S,
        )
        started = hooks.clock()  # the cue clock starts once OBS is writing
        hooks.say(
            f"recording: the beat starts in {source.in_s:g} s, the take lasts {events[-1][0]:g} s"
        )
        for at, kind, value in events:
            await hooks.sleep(max(0.0, started + at - hooks.clock()))
            if kind == "scene":
                await client.request("SetCurrentProgramScene", {"sceneName": value})
                hooks.say(f"{at:6.1f} s  scene {value}")
            elif kind == "cue":
                hooks.say(f"{at:6.1f} s  {value}")
            elif kind == "load":
                await client.request(
                    "SetInputSettings",
                    {"inputName": DASHBOARD_INPUT, "inputSettings": {"url": value}},
                )
                hooks.say(f"{at:6.1f} s  the dashboard loads; the replay starts")
        stopped = await client.request("StopRecord")
        recording = False
    finally:
        stop_replay(process)  # first, whatever happens to the recording
        if recording:
            try:
                await client.request("StopRecord")
            except (ObsError, WebSocketException) as exc:
                hooks.say(f"could not stop OBS's recording ({exc}): stop it in OBS")
    return await asyncio.to_thread(keep, Path(stopped["outputPath"]), out_dir, take)


def keep(
    recorded: Path, out_dir: Path, take: str, wait: Callable[[float], None] = time.sleep
) -> Path:
    """Move OBS's recording to ``out_dir/<take>.<ext>``; the one it replaces is ``.previous``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{take}{recorded.suffix}"
    if target.exists():
        target.replace(out_dir / f"{take}.previous{recorded.suffix}")
    for attempt in range(20):  # OBS may hold the file for a moment after it stops
        try:
            shutil.move(recorded, target)
            break
        except PermissionError:
            if attempt == 19:
                raise
            wait(0.5)
    return target


async def run(take: str, url: str, agent: str | None, hooks: Hooks | None = None) -> Path:
    """Connect to OBS and record one take."""
    beat = load_storyboard().take_beat(take)
    if beat.source.replay and not os.environ.get("SPOTCOOL_WORLD_RUN_DIR"):
        raise ObsError("set SPOTCOOL_WORLD_RUN_DIR to the saved runs' directory for the replay")
    client = await ObsClient.connect(url, os.environ.get(PASSWORD_ENV))
    try:
        return await record(client, beat, TAKES, agent, hooks)
    finally:
        await client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("take", help="a beat's source.take in video/storyboard.yaml")
    parser.add_argument("--url", default=OBS_URL, help=f"obs-websocket (default {OBS_URL})")
    parser.add_argument("--agent", help="SpotCool's agent, e.g. http://192.168.1.20:8002")
    parser.add_argument("--yes", action="store_true", help="do not wait for Enter")
    parser.add_argument("--dry-run", action="store_true", help="print the schedule; no OBS")
    args = parser.parse_args(argv)
    try:
        beat = load_storyboard().take_beat(args.take)
    except KeyError:
        parser.error(f"no beat shows take {args.take}")
    print(
        f"take {args.take} for beat {beat.id} "
        f"({beat.duration:g} s, pre-roll {beat.source.in_s:g} s)"
    )
    print(f"     0.0 s  scene {beat.source.scenes[0].scene}" if beat.source.scenes else "")
    for at, kind, value in schedule(beat, dashboard_url(beat, args.agent)):
        print(f"{at:6.1f} s  {kind} {value}".rstrip())
    if beat.source.replay:
        print("replay: " + " ".join(replay_command(beat.source.replay)))
    if args.dry_run:
        return 0
    for line in beat.source.before:
        print(f"before: {line}")
    if not args.yes:
        input("Press Enter to start the take (Ctrl+C to stop)... ")
    try:
        path = asyncio.run(run(args.take, args.url, args.agent))
    except (ObsError, OSError, WebSocketException) as exc:
        print(f"take: {type(exc).__name__}: {exc}".splitlines()[0], file=sys.stderr)
        return 1
    print(f"take {args.take} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
