"""The ``spotcool-agent`` command: ``run``, ``probe`` and ``version``.

- ``run``: the agent against the world (or a real building) until Ctrl+C, which releases every
  slot it holds. ``--dry-run`` prints the resolved settings, the key masked, and exits.
- ``probe``: one snapshot of every zone camera, counted by the chosen provider, with each
  count's latency: a go/no-go check of the cameras and the model.
- ``version``: the agent's and the contracts' versions.

The run log goes to stderr in ASCII, without tracebacks' local variables (frames and audio
are in scope).
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

from loguru import logger
from pydantic import ValidationError

from spotcool_agent import __version__
from spotcool_agent.runner.agent import Agent, create_vision
from spotcool_agent.settings import AgentSettings, load_settings
from spotcool_agent.transports.world_http import WorldHttp, WorldHttpError
from spotcool_contracts import CONTRACTS_VERSION, ZONES, Zone

PROBE_TIMEOUT_S = 8.0
"""The longest one probe count may take."""


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
        sub.add_argument("--env-file", type=Path, help="a dotenv file, such as the key file")
        sub.add_argument("--sam-call-cap", type=int, help="Meta calls the run may make (600)")
    run.add_argument("--bms-port", type=int, help="the BMS's BACnet/IP port (47808)")
    run.add_argument("--dashboard-port", type=int, help="the dashboard's port (8090)")
    run.add_argument("--listen", help="where to serve /chat and /voice (127.0.0.1:8002)")
    run.add_argument("--settle-s", type=float, help="seconds from a step to its snapshots (1.0)")
    run.add_argument("--dry-run", action="store_true", help="print the settings and exit")
    args = parser.parse_args(argv)
    if args.command == "version":
        print(f"spotcool-agent {__version__} (contracts {CONTRACTS_VERSION})")
        return 0
    flags = {
        "world_host": args.world_host,
        "http_port": args.http_port,
        "vision": args.vision,
        "sam_call_cap": args.sam_call_cap,
    }
    if args.command == "run":
        flags |= {
            "bms_port": args.bms_port,
            "dashboard_port": args.dashboard_port,
            "listen": args.listen,
            "settle_s": args.settle_s,
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
    """Count people on each zone camera once; 0 if every camera gave a count."""
    vision, _ = create_vision(settings)
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
        return 1 if failed else 0
    except WorldHttpError as exc:
        print(f"probe failed: {exc}")
        return 1
    finally:
        await vision.close()
        await world.aclose()


def _log_to_stderr(level: str) -> int:
    """Log to stderr from a thread of its own; return the handler's id.

    A console paused by QuickEdit then holds up the log thread, never the event loop.
    """
    logger.remove()
    return logger.add(sys.stderr, level=level, diagnose=False, backtrace=False, enqueue=True)


if __name__ == "__main__":
    raise SystemExit(main())
