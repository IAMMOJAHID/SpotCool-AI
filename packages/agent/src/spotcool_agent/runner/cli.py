"""The ``spotcool-agent`` command: ``run``, ``probe`` and ``version``.

- ``run``: the agent against the world (or a real building) until Ctrl+C, which releases every
  slot it holds. ``--dry-run`` prints the resolved settings, the key masked, and exits.
- ``probe``: one snapshot of every zone camera, counted by the chosen provider, with each
  count's latency, then one transcription of a second of tone and one short answer from the
  language model, with their lengths and latencies: a go/no-go check of the cameras and the
  models.
- ``version``: the agent's and the contracts' versions.

The run log goes to stderr in ASCII, without tracebacks' local variables (frames and audio
are in scope).
"""

import argparse
import asyncio
import io
import sys
import time
import wave
from pathlib import Path

import numpy as np
from loguru import logger
from pydantic import ValidationError

from spotcool_agent import __version__
from spotcool_agent.runner.agent import Agent, create_language, create_vision
from spotcool_agent.services.llm import LLMError, LLMService
from spotcool_agent.services.stt import STTError, STTService
from spotcool_agent.services.vision import VisionService
from spotcool_agent.settings import AgentSettings, load_settings
from spotcool_agent.transports.world_http import WorldHttp, WorldHttpError
from spotcool_contracts import CONTRACTS_VERSION, ZONES, Zone
from spotcool_contracts.occupants import VOICE_FORMAT

PROBE_TIMEOUT_S = 8.0
"""The longest one probe count or model answer may take."""


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; return the exit code."""
    parser = argparse.ArgumentParser(prog="spotcool-agent", description="SpotCool's agent.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="drive the building until Ctrl+C")
    probe = commands.add_parser("probe", help="count people on every zone camera once")
    commands.add_parser("version", help="print the versions")
    for sub in (run, probe):
        sub.add_argument("--world-host", help="the world's IPv4 address (127.0.0.1)")
        sub.add_argument("--http-port", type=int, help="its camera and calendar port (8081)")
        sub.add_argument("--vision", choices=["meta", "blob", "scripted"], help="(meta)")
        sub.add_argument("--stt", choices=["meta", "fake"], help="(meta with --vision meta)")
        sub.add_argument("--llm", choices=["meta", "fake", "off"], help="(meta with --vision meta)")
        sub.add_argument("--env-file", type=Path, help="a dotenv file, such as the key file")
        sub.add_argument("--sam-call-cap", type=int, help="the building's SAM calls (600)")
        sub.add_argument("--llm-call-cap", type=int, help="the STT and LLM calls (200)")
        sub.add_argument(
            "--scene", choices=["meta", "fake", "off"], help="Muse Spark (meta with --vision meta)"
        )
        sub.add_argument("--scene-call-cap", type=int, help="the scene calls (300)")
        sub.add_argument(
            "--no-meta-trace",
            dest="meta_trace",
            action="store_false",
            default=None,
            help="no META line per Meta call",
        )
    run.add_argument("--bms-port", type=int, help="the BMS's BACnet/IP port (47808)")
    run.add_argument("--dashboard-port", type=int, help="the dashboard's port (8090)")
    run.add_argument("--listen", help="where to serve /chat and /voice (127.0.0.1:8002)")
    run.add_argument("--settle-s", type=float, help="seconds from a step to its snapshots (1.0)")
    vrf = run.add_argument_group("the meeting room's VRF unit (off by default)")
    vrf.add_argument("--vrf", action="store_true", default=None, help="aim its louvers")
    vrf.add_argument("--vrf-port", type=int, help="its gateway's BACnet/IP port (47810)")
    vrf.add_argument("--vrf-bacnet-port", type=int, help="the agent's own port for it (47821)")
    vrf.add_argument("--vrf-call-cap", type=int, help="its own SAM calls (3000)")
    vrf.add_argument("--vrf-camera", help="the camera watching it (cam-meeting-live)")
    vrf.add_argument(
        "--vrf-brand", choices=["mitsubishi", "daikin", "lg"], help="if its gateway names another"
    )
    vrf.add_argument("--vrf-camera-height-m", type=float, help="the camera's height (2.8)")
    vrf.add_argument("--vrf-camera-tilt-deg", type=float, help="its tilt down (30)")
    vrf.add_argument("--vrf-camera-hfov-deg", type=float, help="its field of view (90)")
    vrf.add_argument("--vrf-mirror", action="store_true", default=None, help="it faces the unit")
    vrf.add_argument("--vrf-hold-s", type=float, help="nobody seen, then release (10)")
    vrf.add_argument("--draft-hold-s", type=float, help="a draft pauses the aim (600)")
    vrf.add_argument("--complaint-hold-s", type=float, help="a complaint's override (900)")
    vrf.add_argument(
        "--no-comfort",
        dest="comfort",
        action="store_false",
        default=None,
        help="no comfort setpoint from Muse Spark",
    )
    run.add_argument("--dry-run", action="store_true", help="print the settings and exit")
    args = parser.parse_args(argv)
    if args.command == "version":
        print(f"spotcool-agent {__version__} (contracts {CONTRACTS_VERSION})")
        return 0
    flags = {
        "world_host": args.world_host,
        "http_port": args.http_port,
        "vision": args.vision,
        "stt": args.stt,
        "llm": args.llm,
        "sam_call_cap": args.sam_call_cap,
        "llm_call_cap": args.llm_call_cap,
        "scene": args.scene,
        "scene_call_cap": args.scene_call_cap,
        "meta_trace": args.meta_trace,
    }
    if args.command == "run":
        flags |= {
            "bms_port": args.bms_port,
            "dashboard_port": args.dashboard_port,
            "listen": args.listen,
            "settle_s": args.settle_s,
            "vrf": args.vrf,
            "vrf_port": args.vrf_port,
            "vrf_bacnet_port": args.vrf_bacnet_port,
            "vrf_call_cap": args.vrf_call_cap,
            "vrf_camera": args.vrf_camera,
            "vrf_brand": args.vrf_brand,
            "vrf_camera_height_m": args.vrf_camera_height_m,
            "vrf_camera_tilt_deg": args.vrf_camera_tilt_deg,
            "vrf_camera_hfov_deg": args.vrf_camera_hfov_deg,
            "vrf_mirror": args.vrf_mirror,
            "vrf_hold_s": args.vrf_hold_s,
            "draft_hold_s": args.draft_hold_s,
            "complaint_hold_s": args.complaint_hold_s,
            "comfort": args.comfort,
        }
    try:
        settings = load_settings(args.env_file, **flags)
    except ValidationError as exc:
        print(f"{args.command} failed: invalid settings: {exc.error_count()} errors")
        for error in exc.errors(include_url=False, include_input=False):
            print(f"  {'.'.join(map(str, error['loc']))}: {error['msg']}")
        return 1
    if args.command == "run" and args.dry_run:
        print(settings.model_dump_json(indent=2))  # SecretStr dumps as "**********"
        return 0
    handler = _log_to_stderr(settings.log_level)
    try:
        if args.command == "probe":
            return asyncio.run(_probe(settings))
        asyncio.run(Agent(settings).run())
    except KeyboardInterrupt:
        logger.info("agent: stopped (Ctrl+C)")
    except (OSError, ValueError) as exc:
        print(f"{args.command} failed: {type(exc).__name__}: {exc}")
        return 1
    finally:
        logger.remove(handler)  # waits for the log thread to write what it holds
    return 0


async def _probe(settings: AgentSettings) -> int:
    """Count people on each zone camera once, then try the transcriber and the language model.

    Returns:
        0 if every camera gave a count and each model answered, else 1.
    """
    vision, _ = create_vision(settings)
    stt, llm, language = create_language(settings)
    try:
        failed = await _probe_cameras(settings, vision)
        failed += await _probe_language(stt, llm)
        return 1 if failed else 0
    finally:
        await vision.close()
        await stt.close()
        if language is not None:
            await language.close()


async def _probe_language(stt: STTService, llm: LLMService | None) -> int:
    """One transcription of a second of tone, and one short answer; the failures counted.

    Only lengths are printed, never what a model said.
    """
    failed = 0
    began = time.perf_counter()
    try:
        heard = await stt.transcribe(tone_wav(1.0), PROBE_TIMEOUT_S)
        print(f"stt {stt.name}: {len(heard)} characters in {time.perf_counter() - began:.2f} s")
    except STTError as exc:
        print(f"stt {stt.name}: failed after {time.perf_counter() - began:.2f} s: {exc}")
        failed += 1
    if llm is None:
        print("llm off: keywords and template replies")
        return failed
    began = time.perf_counter()
    try:
        said = await llm.complete(
            "Reply with the one word: ready.", PROBE_TIMEOUT_S, purpose="probe"
        )
        print(f"llm {llm.name}: {len(said)} characters in {time.perf_counter() - began:.2f} s")
    except LLMError as exc:
        print(f"llm {llm.name}: failed after {time.perf_counter() - began:.2f} s: {exc}")
        failed += 1
    return failed


def tone_wav(seconds: float, hz: float = 440.0) -> bytes:
    """A sine tone as a 16 kHz mono 16-bit WAV, in memory (the voice format)."""
    rate = VOICE_FORMAT.sample_rate_hz
    samples = np.sin(2 * np.pi * hz * np.arange(int(seconds * rate)) / rate)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes((samples * 8000).astype("<i2").tobytes())
    return buffer.getvalue()


async def _probe_cameras(settings: AgentSettings, vision: VisionService) -> int:
    """Count people on each zone camera once; the cameras that gave no count."""
    world = WorldHttp(f"http://{settings.world_host}:{settings.http_port}")
    try:
        catalog = await world.cameras()
        failed = 0
        for zone in Zone:
            camera = next(
                (c for c in catalog.for_zone(zone) if c.id == ZONES[zone].camera_id), None
            )
            if camera is None:
                print(f"{zone.value}: no camera in the catalog")
                failed += 1
                continue
            began = time.perf_counter()
            try:
                jpeg = await world.snapshot(camera.snapshot_url)
                found = await vision.detect(jpeg, "humans", PROBE_TIMEOUT_S, camera_id=camera.id)
            except Exception as exc:  # report every camera, whatever fails
                print(f"{camera.id}: failed after {time.perf_counter() - began:.2f} s: {exc}")
                failed += 1
                continue
            sure = [d.confidence for d in found.boxes if d.confidence is not None]
            confidence = f", confidence {min(sure):.2f}-{max(sure):.2f}" if sure else ""
            print(
                f"{camera.id} ({camera.source}): {len(found.boxes)} people in "
                f"{time.perf_counter() - began:.2f} s{confidence}"
            )
        return failed
    except WorldHttpError as exc:
        print(f"probe failed: {exc}")
        return 1
    finally:
        await world.aclose()


def _log_to_stderr(level: str) -> int:
    """Log to stderr from a thread of its own; return the handler's id.

    A console paused by QuickEdit then holds up the log thread, never the event loop.
    """
    logger.remove()
    fmt = "<green>{time:HH:mm:ss}</green> <level>{level: <7}</level> {message}"  # short: the demo's pane
    return logger.add(
        sys.stderr, level=level, format=fmt, diagnose=False, backtrace=False, enqueue=True
    )


if __name__ == "__main__":
    raise SystemExit(main())
