"""``spotcool-world`` command line.

Subcommands so far: ``version``, ``run --dry-run`` (print the resolved settings), ``probe``
(the go/no-go check of a plant) and ``lint`` (check scenarios). Running a world, ``replay`` and
``report`` arrive with later work packages.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from spotcool_contracts import CONTRACTS_VERSION
from spotcool_world import __version__
from spotcool_world.runner.probe import probe
from spotcool_world.scenarios import library_names, lint
from spotcool_world.services.settings import WorldSettings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spotcool-world", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version", help="print package and contract versions")
    run = commands.add_parser("run", help="run the world")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="print the resolved settings (arguments, SPOTCOOL_WORLD_* env, world.yaml) and exit",
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
    return parser


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
    settings = WorldSettings()
    if args.dry_run:
        print(settings.model_dump_json(indent=2))
        return 0
    print("spotcool-world run: running a world is not implemented yet; use --dry-run")
    return 2


if __name__ == "__main__":
    sys.exit(main())
