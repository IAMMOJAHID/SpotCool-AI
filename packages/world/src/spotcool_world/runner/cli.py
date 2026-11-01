"""``spotcool-world`` command line.

Subcommands so far: ``version``, ``run --dry-run`` (print the resolved settings) and ``probe``
(the go/no-go check of a plant). Running a world, ``replay``, ``lint`` and ``report`` arrive
with later work packages.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence

from spotcool_contracts import CONTRACTS_VERSION
from spotcool_world import __version__
from spotcool_world.runner.probe import probe
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
    return parser


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
    settings = WorldSettings()
    if args.dry_run:
        print(settings.model_dump_json(indent=2))
        return 0
    print("spotcool-world run: running a world is not implemented yet; use --dry-run")
    return 2


if __name__ == "__main__":
    sys.exit(main())
