"""``spotcool-world`` command line.

Subcommands so far: ``version`` and ``run --dry-run`` (print the resolved settings). Running a
world, ``replay``, ``lint``, ``probe`` and ``report`` arrive with later work packages.
"""

import argparse
import sys
from collections.abc import Sequence

from spotcool_contracts import CONTRACTS_VERSION
from spotcool_world import __version__
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line.

    Args:
        argv: Arguments without the program name; defaults to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    args = _parser().parse_args(argv)
    if args.command == "version":
        print(f"spotcool-world {__version__} (contracts {CONTRACTS_VERSION})")
        return 0
    settings = WorldSettings()
    if args.dry_run:
        print(settings.model_dump_json(indent=2))
        return 0
    print("spotcool-world run: running a world is not implemented yet; use --dry-run")
    return 2


if __name__ == "__main__":
    sys.exit(main())
