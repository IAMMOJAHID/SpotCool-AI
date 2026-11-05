"""``spotcool-world`` command line.

Subcommands: ``version``; ``run`` (the world, or ``--dry-run`` to print the resolved settings);
``replay`` (a recorded run's events on the dashboard and cameras, for video takes); ``probe``
(the go/no-go check of a plant); ``lint`` (check scenarios); ``report`` (a recorded run's
report). ``run`` and ``replay`` need the world's extras, imported only when they start.
"""

import argparse
import asyncio
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic_settings import SettingsConfigDict

from spotcool_contracts import CONTRACTS_VERSION
from spotcool_world import __version__
from spotcool_world.processors.kpi import RUNS
from spotcool_world.runner.probe import probe
from spotcool_world.runner.report import (
    STUB_AGENT_NOTE,
    check_energy,
    load_run,
    verdict,
    write_report,
)
from spotcool_world.scenarios import library_names, lint
from spotcool_world.services.settings import WorldSettings
from spotcool_world.workers import WorkerState

WORLD_MODULES = frozenset({"bacpypes3", "cv2", "fastapi", "httpx", "numpy", "starlette", "uvicorn"})
"""The extras' modules ``run`` and ``replay`` import; a missing one means an extra is missing."""
INSTALL_EXTRAS = "uv sync --all-packages --all-extras"
"""How to install every extra."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spotcool-world", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version", help="print package and contract versions")
    run = commands.add_parser(
        "run",
        help="run the world: both plants, the BMS, cameras, calendar, occupants, recorder and "
        "dashboard",
    )
    run.add_argument(
        "--config",
        type=Path,
        metavar="FILE",
        help="settings file (default: world.yaml in the current directory, if there is one)",
    )
    run.add_argument(
        "--plant",
        choices=["rc", "boptest-local", "boptest-hosted"],
        help="plant provider (default: the settings' plant)",
    )
    run.add_argument(
        "--scenario",
        metavar="NAME",
        help="a library scenario or a .yaml file (default: the settings' scenario)",
    )
    run.add_argument(
        "--speed",
        type=float,
        metavar="S",
        help="simulation seconds per wall second; 0 runs as fast as possible "
        "(default: the settings' speed)",
    )
    run.add_argument(
        "--run-id",
        metavar="ID",
        help="the run's directory in run_dir (default: the date, time and scenario)",
    )
    run.add_argument(
        "--stub-agent",
        action="store_true",
        help="also run the stub agent: a BACnet client with ground-truth perception that stands "
        "in for SpotCool's agent",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="print the resolved settings (arguments, SPOTCOOL_WORLD_* env, world.yaml) and exit",
    )
    replay = commands.add_parser(
        "replay",
        help="play a recorded run's events on the dashboard and cameras (no physics, no BACnet)",
    )
    replay.add_argument("run_id", help="the run: a directory in the settings' run_dir")
    replay.add_argument(
        "--config",
        type=Path,
        metavar="FILE",
        help="settings file (default: world.yaml in the current directory, if there is one)",
    )
    replay.add_argument(
        "--speed",
        type=float,
        metavar="S",
        help="simulation seconds per wall second; 0 runs as fast as possible "
        "(default: the settings' speed)",
    )
    replay.add_argument(
        "--wait-viewer",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="wait up to this long for a dashboard on /ws/events before the first event, "
        "e.g. for an OBS take (default 0: start at once)",
    )
    check = commands.add_parser(
        "probe", help="start a plant, step it with a 28 degC core override, print KPIs, stop it"
    )
    check.add_argument(
        "--plant",
        choices=["boptest-hosted", "boptest-local", "rc"],
        help="plant provider (default: the settings' plant)",
    )
    check.add_argument("--steps", type=int, default=4, help="steps to take (default 4)")
    lint_command = commands.add_parser(
        "lint", help="check scenarios: print every finding, exit 1 if any is an error"
    )
    lint_command.add_argument(
        "scenarios",
        nargs="*",
        metavar="SCENARIO",
        help="a library name or a .yaml file (default: the whole library)",
    )
    lint_command.add_argument(
        "--clips",
        type=Path,
        metavar="DIR",
        help="also check that each <zone>_<scene>.mp4 is in DIR",
    )
    lint_command.add_argument(
        "--step-s",
        type=float,
        metavar="S",
        help="step grid in simulation seconds (default: the settings' step_s)",
    )
    report = commands.add_parser(
        "report",
        help="write a recorded run's report.md and PNG charts into its directory (extra report)",
    )
    report.add_argument("run_id", help="the run: a directory in the settings' run_dir")
    return parser


def configure_logging(level: str) -> None:
    """Replace loguru's default handler: stderr at ``level``, tracebacks without variables.

    ``diagnose=False`` keeps a traceback from printing the values of local variables, which may
    hold audio or camera frames (Law 6); ``backtrace=False`` keeps it to the frames that ran.

    Args:
        level: A loguru level, such as ``WorldSettings.log_level``.

    Raises:
        ValueError: If ``level`` is not a loguru level; the handlers are then left as they are.
    """
    try:
        logger.level(level)
    except ValueError:
        raise ValueError(f"log_level {level!r} is not a log level, such as INFO or DEBUG") from None
    logger.remove()
    logger.add(sys.stderr, level=level, diagnose=False, backtrace=False)


def _settings(
    parser: argparse.ArgumentParser, config: Path | None, **overrides: Any
) -> WorldSettings:
    """The settings, with ``config`` read in place of ``./world.yaml`` when given.

    Raises:
        ValueError: If a setting is invalid (pydantic's ``ValidationError``).
    """
    if config is None:
        return WorldSettings(**overrides)
    if not config.is_file():
        parser.error(f"--config: {config} is not a file")

    class _FromFile(WorldSettings):
        model_config = SettingsConfigDict(yaml_file=config)

    return _FromFile(**overrides)


def _speed(parser: argparse.ArgumentParser, speed: float) -> float | None:
    """``--speed`` as a setting: 0 means as fast as possible (``None``)."""
    if not speed >= 0:
        parser.error("--speed must be 0 (as fast as possible) or more")
    return speed or None


def _missing_extra(command: str, exc: ModuleNotFoundError) -> int:
    """Say which of the world's extras is missing, and return 1; re-raise any other ``exc``."""
    missing = (exc.name or "").partition(".")[0]
    if missing not in WORLD_MODULES:
        raise exc  # not an extra's module: a bug
    print(f"{command} needs the world's extras ({missing} is missing): {INSTALL_EXTRAS}")
    return 1


def _run(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """Run the world the arguments describe; 0 if its pipeline finished, else 1."""
    overrides: dict[str, Any] = {}
    if args.plant is not None:
        overrides["plant"] = args.plant
    if args.scenario is not None:
        overrides["scenario"] = args.scenario
    if args.speed is not None:
        overrides["speed"] = _speed(parser, args.speed)
    if args.stub_agent:
        overrides["stub_agent"] = True
    try:
        settings = _settings(parser, args.config, **overrides)
        if args.dry_run:
            print(settings.model_dump_json(indent=2))
            return 0
        configure_logging(settings.log_level)
        from spotcool_world.runner.assembly import World

        world = World(settings, run_id=args.run_id)
    except ModuleNotFoundError as exc:
        return _missing_extra("run", exc)
    except (OSError, ValueError) as exc:  # pydantic's ValidationError included
        print(f"run failed: {exc}")
        return 1
    speed = "as fast as possible" if settings.speed is None else f"speed {settings.speed:g}"
    agent = ", stub agent (ground-truth perception)" if settings.stub_agent else ""
    print(
        f"spotcool-world run {world.run_id}: {world.compiled.name} on the {settings.plant} plant, "
        f"{speed}{agent}"
    )
    _print_urls(world.urls())
    print("Ctrl+C stops the run gracefully; a second Ctrl+C cancels it.", flush=True)
    try:
        asyncio.run(world.run())
    except (OSError, ValueError) as exc:
        print(f"run failed: {exc}")
        return 1
    except KeyboardInterrupt:
        print("run interrupted")
        return 130
    _print_outcome(settings, world.run_id, args.config)
    return 0 if world.pipeline.state is WorkerState.FINISHED else 1


def _print_urls(urls: dict[str, str]) -> None:
    for label, url in urls.items():
        print(f"  {label:<13} {url}", flush=True)  # shown at start-up, even when piped


def _print_outcome(settings: WorldSettings, run_id: str, config: Path | None) -> None:
    """Print how a run ended, from its recording, and its report and replay commands.

    Each plant run's energy (the step-mean integration, its plant's own and their difference)
    and comfort, energy saved both ways, then the verdict (``verdict``) on quoting it. After a
    run with ``--config``, the commands carry its run directory and file, since they otherwise
    read the environment and ``./world.yaml``.
    """
    run_dir = settings.run_dir
    try:
        recorded = load_run(run_dir, run_id)
    except (OSError, ValueError) as exc:
        print(f"run {run_id} left no complete record: {exc}")
        return
    manifest, check = recorded.manifest, check_energy(recorded)
    print(f"run {run_id} {manifest.ended} ({manifest.end_reason}); recorded in {run_dir / run_id}")
    if manifest.settings.get("stub_agent"):
        print(f"  {STUB_AGENT_NOTE}")
    for run in RUNS:
        kpi = manifest.final_kpis.get(run)
        comfort = kpi.comfort_where_people_kh if kpi is not None else None
        print(
            f"  {run:<9} energy {_amount(check.integrated.get(run), 'kWh')} (plant's own "
            f"{_amount(check.own[run], 'kWh')}, difference {_amount(check.differences[run], '%', 3)}"
            f"), comfort where people were {_amount(comfort, 'K h', 2)}"
        )
    print(
        f"  energy saved {_amount(check.saved_own, '%', 2)} by the plants' own energy, "
        f"{_amount(check.saved_integrated, '%', 2)} by the step-mean integration"
    )
    head, *blockers = verdict(recorded)
    print(f"  {head}")
    for blocker in blockers:
        print(f"    - {blocker}")
    report, replay = f"spotcool-world report {run_id}", f"spotcool-world replay {run_id}"
    if config is not None:
        report = f"SPOTCOOL_WORLD_RUN_DIR={_quoted(run_dir)} {report}"
        replay += f" --config {_quoted(config)}"
    print(f"report: {report}")
    print(f"replay: {replay}")


def _amount(value: float | None, unit: str, digits: int = 1) -> str:
    # + 0.0 turns a rounded -0.0 into 0.0
    return "n/a" if value is None else f"{round(value, digits) + 0.0:.{digits}f} {unit}"


def _quoted(path: Path) -> str:
    """A path for a shell command line: absolute, with forward slashes, quoted if need be."""
    return shlex.quote(path.resolve().as_posix())


def _replay(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """Replay a recorded run; 0 once it has played to its end, else 1."""
    if not args.wait_viewer >= 0:
        parser.error("--wait-viewer must be 0 (no wait) or more")
    try:
        settings = _settings(parser, args.config)
        speed = settings.speed if args.speed is None else _speed(parser, args.speed)
        configure_logging(settings.log_level)
        from spotcool_world.runner.assembly import Replay

        replay = Replay(settings, args.run_id, speed=speed, wait_viewer_s=args.wait_viewer)
    except ModuleNotFoundError as exc:
        return _missing_extra("replay", exc)
    except (OSError, ValueError) as exc:
        print(f"replay failed: {exc}")
        return 1
    pace = "as fast as possible" if speed is None else f"speed {speed:g}"
    print(f"spotcool-world replay {args.run_id}: {len(replay.envelopes)} events, {pace}")
    _print_urls(replay.urls())
    try:
        asyncio.run(replay.run())
    except (OSError, ValueError) as exc:
        print(f"replay failed: {exc}")
        return 1
    except KeyboardInterrupt:
        print("replay interrupted")
        return 130
    state = replay.pipeline.state
    print(f"replay {args.run_id} {state.value}")
    return 0 if state is WorkerState.FINISHED else 1


def _lint(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """Lint the scenarios ``args`` names; 1 if any finding is an error, else 0."""
    if args.step_s is not None and not args.step_s > 0:
        parser.error("--step-s must be positive")
    if args.clips is not None and not args.clips.is_dir():
        parser.error(f"--clips: {args.clips} is not a directory")
    settings = WorldSettings()
    step_s = settings.step_s if args.step_s is None else args.step_s
    sources = args.scenarios or library_names()
    errors = warnings = 0
    for source in sources:
        try:
            findings = lint(
                source, step_s=step_s, footage_dir=settings.footage_dir, clips_dir=args.clips
            )
        except (OSError, ValueError) as exc:
            print(f"{source}: error: {exc}")
            errors += 1
            continue
        for finding in findings:
            print(finding)
        errors += sum(finding.level == "error" for finding in findings)
        warnings += sum(finding.level == "warning" for finding in findings)
    print(f"{len(sources)} scenario(s): {errors} error(s), {warnings} warning(s)")
    return 1 if errors else 0


def _report(run_id: str) -> int:
    """Write the report of a run in the settings' ``run_dir``; 1 if it cannot be written."""
    try:
        path = write_report(WorldSettings().run_dir, run_id)
    except ModuleNotFoundError as exc:
        if exc.name is None or exc.name.partition(".")[0] != "matplotlib":
            raise
        print("report needs the report extra: uv sync --all-packages --extra report")
        return 1
    except (OSError, ValueError) as exc:
        print(f"report failed: {exc}")
        return 1
    print(f"wrote {path} and its charts")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line.

    Args:
        argv: Arguments without the program name; defaults to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "version":
        print(f"spotcool-world {__version__} (contracts {CONTRACTS_VERSION})")
        return 0
    if args.command == "probe":
        if args.steps < 1:
            parser.error("--steps must be at least 1")
        settings = WorldSettings(plant=args.plant) if args.plant else WorldSettings()
        try:
            asyncio.run(probe(settings, args.steps))
        except KeyboardInterrupt:
            print("probe interrupted")
            return 130
        except Exception as exc:
            print(f"probe failed: {type(exc).__name__}: {exc}")
            return 1
        return 0
    if args.command == "lint":
        return _lint(parser, args)
    if args.command == "report":
        return _report(args.run_id)
    if args.command == "replay":
        return _replay(parser, args)
    return _run(parser, args)


if __name__ == "__main__":
    sys.exit(main())
