"""Generate the results card's text from a saved run, so nobody types its numbers.

    uv run python scripts/video/results_card.py [RUN_ID] [--run-dir DIR] [--out FILE]

RUN_ID defaults to the run the storyboard's results beat names. The run is read through the
world's own report (``spotcool_world.runner.report``: ``load_run``, ``check_energy``,
``quote_blockers``), from ``--run-dir`` or ``$SPOTCOOL_WORLD_RUN_DIR``; the run directory is
only read. The card is written to ``video/renders/results_card.json``, where
``render_motion.py`` takes it as the ``results_card`` composition's variables.

The label follows the report's verdict. A run whose verdict is "Quotable" says "SpotCool saved".
A run blocked only by the stub agent says "Upper bound with perfect perception (stub agent, in
simulation)". Any other blocker (not BOPTEST, unpaced, unfinished, dropped events, a power
fallback, an integration off its plant's own) refuses the card.
"""

import argparse
import dataclasses
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from storyboard import RENDERS, load_storyboard

from spotcool_world.runner.report import (
    HONESTY_NOTE,
    RecordedRun,
    check_energy,
    load_run,
    quote_blockers,
)

CARD_FILE = RENDERS / "results_card.json"
STUB_LABEL = "Upper bound with perfect perception (stub agent, in simulation)"
QUOTABLE_LABEL = "SpotCool saved"
CARD_KEYS = (
    "label",
    "saved",
    "saved_caption",
    "spotcool_kwh",
    "baseline_kwh",
    "spotcool_energy",
    "baseline_energy",
    "comfort_caption",
    "comfort_spotcool",
    "comfort_baseline",
    "run_line",
    "caveat",
)
"""The variables the card gives the ``results_card`` composition."""
RUN_DIR_ENV = "SPOTCOOL_WORLD_RUN_DIR"


class CardRefused(ValueError):
    """The run may not be shown at all: its verdict has a blocker besides the stub agent."""


@dataclass(frozen=True)
class ResultsCard:
    """The results card of one run.

    Parameters:
        run_id: The run.
        stub: Whether the stub agent drove it.
        label: The card's label, from the verdict.
        variables: The composition's variables, by ``CARD_KEYS``.
        figures: The numbers the card shows, in the order ``video/sources.yaml`` lists a run's.
        verdict: The blockers the verdict listed (empty for a quotable run).
    """

    run_id: str
    stub: bool
    label: str
    variables: dict[str, str | float]
    figures: tuple[str, ...]
    verdict: tuple[str, ...]

    def to_json(self) -> str:
        """The card as the JSON ``CARD_FILE`` holds."""
        return json.dumps(dataclasses.asdict(self), indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "ResultsCard":
        """Read a card written by ``to_json``.

        Raises:
            ValueError: If it is not a card.
        """
        data = json.loads(text)
        try:
            return cls(
                run_id=str(data["run_id"]),
                stub=bool(data["stub"]),
                label=str(data["label"]),
                variables=dict(data["variables"]),
                figures=tuple(data["figures"]),
                verdict=tuple(data["verdict"]),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"not a results card: {exc}") from exc


def build_card(run: RecordedRun) -> ResultsCard:
    """The card of a recorded run.

    Args:
        run: The run, from ``load_run``.

    Raises:
        CardRefused: If the verdict has a blocker other than the stub agent.
    """
    manifest = run.manifest
    stub = bool(manifest.settings.get("stub_agent"))
    blockers = quote_blockers(run)
    # The same run without the stub flag: whatever still blocks it is a real problem.
    others = quote_blockers(_without_stub(run)) if stub else blockers
    if others:
        raise CardRefused(f"run {manifest.run_id} may not be shown: " + " ".join(others))
    check = check_energy(run)
    saved, own = check.saved_own, check.own
    spotcool, baseline = own["spotcool"], own["baseline"]
    assert saved is not None and spotcool is not None and baseline is not None  # no blockers
    kpis = manifest.final_kpis
    comfort = (kpis["spotcool"].comfort_where_people_kh, kpis["baseline"].comfort_where_people_kh)
    saved_text = f"{saved:.2f} %"
    energy = (f"{spotcool:.1f}", f"{baseline:.1f}")
    comfort_text = (f"{comfort[0]:.2f}", f"{comfort[1]:.2f}")
    testcase = manifest.settings.get("testcase", "")
    variables: dict[str, str | float] = {
        "label": STUB_LABEL if stub else QUOTABLE_LABEL,
        "saved": saved_text,
        "saved_caption": (
            "less HVAC energy than the baseline" if stub else "HVAC energy, against the baseline"
        ),
        "spotcool_kwh": round(spotcool, 1),
        "baseline_kwh": round(baseline, 1),
        "spotcool_energy": f"{energy[0]} kWh",
        "baseline_energy": f"{energy[1]} kWh",
        "comfort_caption": "Discomfort where people were",
        "comfort_spotcool": f"{comfort_text[0]} K h",
        "comfort_baseline": f"{comfort_text[1]} K h",
        "run_line": f"Run {manifest.run_id} · {manifest.scenario} · BOPTEST {testcase}".strip(),
        "caveat": ". ".join(HONESTY_NOTE.split(". ")[:2]) + ".",  # its first two sentences
    }
    return ResultsCard(
        run_id=manifest.run_id,
        stub=stub,
        label=str(variables["label"]),
        variables=variables,
        figures=(saved_text, *energy, *comfort_text),
        verdict=tuple(blockers),
    )


def _without_stub(run: RecordedRun) -> RecordedRun:
    settings = {**run.manifest.settings, "stub_agent": False}
    return dataclasses.replace(run, manifest=run.manifest.model_copy(update={"settings": settings}))


def run_dir_from_env() -> Path:
    """The saved runs' directory, ``$SPOTCOOL_WORLD_RUN_DIR``.

    Raises:
        ValueError: If it is not set.
    """
    value = os.environ.get(RUN_DIR_ENV)
    if not value:
        raise ValueError(f"set {RUN_DIR_ENV} to the saved runs' directory, or pass --run-dir")
    return Path(value)


def results_run_id() -> str:
    """The run the storyboard's results card is generated from.

    Raises:
        ValueError: If no beat shows the ``results_card`` composition with a run.
    """
    for beat in load_storyboard().beats:
        if beat.source.composition == "results_card" and beat.source.run_id:
            return beat.source.run_id
    raise ValueError("no storyboard beat shows results_card with a run_id")


def load_card(path: Path = CARD_FILE) -> ResultsCard | None:
    """The card last generated, or ``None`` if there is none."""
    return ResultsCard.from_json(path.read_text("utf-8")) if path.is_file() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("run_id", nargs="?", help="the run (default: the storyboard's)")
    parser.add_argument("--run-dir", type=Path, help=f"default: ${RUN_DIR_ENV}")
    parser.add_argument("--out", type=Path, default=CARD_FILE)
    args = parser.parse_args(argv)
    try:
        run_id = args.run_id or results_run_id()
        card = build_card(load_run(args.run_dir or run_dir_from_env(), run_id))
    except (OSError, ValueError) as exc:
        print(f"results card: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(card.to_json(), "utf-8")
    print(f"{card.label}: {card.variables['saved']} {card.variables['saved_caption']}")
    print(f"figures {', '.join(card.figures)} -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
