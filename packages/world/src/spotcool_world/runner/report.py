"""The run report: a recorded run as ``runs/<run_id>/report.md`` and PNG charts.

``spotcool-world report <run_id>`` calls ``write_report``. The charts need the ``report`` extra
(matplotlib, drawn with its Agg backend); this module imports without it.

The series come from the record's raw measurements, through the ``TelemetryProcessor``'s own
``metered_summary`` (a state lacking a value is left out, its power still counted), and the
people from its ``scenario_state`` events. The KPIs are the final ones in the manifest. The KPI
table shows each run's energy two ways, the plant's own (``ener_tot`` times the floor area) and
the world's step-mean integration, energy saved both ways, and the steps whose power fell back
to its end-of-step value. Below it, ``verdict`` says whether the plants' own saving may be
quoted (``quote_blockers``). The report adds recovery times, which the contract's ``KpiData``
has no field for. A stub run's report (``stub_agent`` in the manifest's settings) opens with
``STUB_AGENT_NOTE``.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from spotcool_contracts import ZONES, Zone
from spotcool_contracts.events import (
    EventType,
    MetricsData,
    PlantMeasurementsData,
    Run,
    ScenarioStateData,
    ZoneMeasurement,
)
from spotcool_world.clocks import SimClock
from spotcool_world.processors.kpi import (
    CROSS_CHECK_PCT,
    RUNS,
    cross_check_pct,
    energy_saved_pct,
    plant_energy_kwh,
)
from spotcool_world.processors.plant_processor import POWER_FALLBACKS_METRIC
from spotcool_world.processors.telemetry import EnergyMeter, metered_summary
from spotcool_world.scenarios import sim_datetime
from spotcool_world.serializers.jsonl import (
    EVENTS_FILE,
    MANIFEST_FILE,
    RUN_ID_PATTERN,
    RawRecord,
    RunManifest,
    read_records,
)
from spotcool_world.services.boptest.catalog import load_catalog

REPORT_FILE = "report.md"
"""The report's name inside the run directory."""
RECOVERY_MARGIN_K = 0.5
"""A zone has recovered once it is no warmer than the baseline's cooling setpoint plus this."""
HONESTY_NOTE = (
    "BOPTEST's occupant heat follows its fixed schedule. Savings in zones that the cameras show "
    "empty may therefore be overstated by up to the occupants' heat. The RC plant and the "
    "`all_hands_day` scenario bracket this. BOPTEST's own discomfort KPIs (`tdis_tot`, "
    "`idis_tot`) follow its fixed schedule, which counts every zone as occupied all day, so they "
    "include zones the scenario leaves empty."
)
"""The report's honesty note, word for word."""
STUB_AGENT_NOTE = "(stub agent: perfect perception from the scenario script, not Person A's agent)"
"""How a stub run is labelled: at the top of its report and in ``spotcool-world run``'s output."""
POWER_CHART = "power_energy.png"
ZONES_CHART = "zone_temperatures.png"
OCCUPANCY_CHART = "occupancy.png"
BOPTEST_PLANTS = ("boptest-local", "boptest-hosted")
"""The plants whose own energy is independent of the world's integration: only theirs is quoted."""

_LABELS: dict[Run, str] = {"spotcool": "SpotCool", "baseline": "Baseline"}
_COLORS: dict[Run, str] = {"spotcool": "#2a78d6", "baseline": "#eb6834"}  # dataviz slots 1, 2
_SURFACE, _INK, _MUTED, _GRID, _OCCUPIED = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df", "#eeeeea"


@dataclass(frozen=True)
class Sample:
    """One plant run's state at a stamp.

    Parameters:
        t_s: The stamp: the end of the step the state describes.
        summary: The state in BMS units, with the run's cumulative energy.
    """

    t_s: float
    summary: PlantMeasurementsData


@dataclass(frozen=True)
class RecordedRun:
    """A recorded run, read back.

    Parameters:
        manifest: The run's manifest.
        samples: Each plant run's states in time order, the initial state first.
        states: The scenario state of each tick, by the tick's time.
        fallbacks: Each plant run's steps whose power fell back to its end-of-step value (the
            last ``POWER_FALLBACKS_METRIC`` sample; 0 without one).
    """

    manifest: RunManifest
    samples: dict[Run, list[Sample]]
    states: list[tuple[float, ScenarioStateData]]
    fallbacks: dict[Run, int] = field(default_factory=lambda: dict.fromkeys(RUNS, 0))


@dataclass(frozen=True)
class EnergyCheck:
    """A recorded run's energy two ways, and its energy saved two ways.

    Parameters:
        integrated: Each plant run's energy by the world's step-mean integration (kWh).
        own: Each plant run's energy by its plant's own account, ``ener_tot`` times the floor
            area (kWh); ``None`` if the plant reported none.
        differences: Each plant run's integrated energy against its plant's own (%); ``None``
            without both, or when the plant used no energy.
        saved_own: Energy saved by the plants' own energy (%): the figure to quote.
        saved_integrated: Energy saved by the step-mean integration (%): its check.
    """

    integrated: dict[Run, float]
    own: dict[Run, float | None]
    differences: dict[Run, float | None]
    saved_own: float | None
    saved_integrated: float | None


@dataclass(frozen=True)
class Arrival:
    """People entering a zone that was empty for at least a step, and how fast it recovered.

    Parameters:
        zone: The zone.
        at_s: The tick the people arrived at.
        people: How many arrived.
        minutes: Per plant run, the minutes until the zone was no warmer than the baseline's
            cooling setpoint plus ``RECOVERY_MARGIN_K``; ``None`` if it never was.
    """

    zone: Zone
    at_s: float
    people: int
    minutes: dict[Run, float | None]


def load_run(run_dir: Path, run_id: str) -> RecordedRun:
    """Read a recorded run.

    Args:
        run_dir: Where the runs are.
        run_id: The run.

    Raises:
        ValueError: If the run id is unsafe, the run has no record or no manifest (its record is
            incomplete), or a record is invalid.
        OSError: If a file cannot be read.
    """
    if not re.fullmatch(RUN_ID_PATTERN, run_id):
        raise ValueError(f"{run_id!r} is not a run id: letters, digits, _ and -")
    folder = run_dir / run_id
    if not (folder / EVENTS_FILE).is_file():
        raise ValueError(f"no recorded run {run_id} in {run_dir}")
    if not (folder / MANIFEST_FILE).is_file():
        raise ValueError(f"run {run_id} has no {MANIFEST_FILE}: its record is incomplete")
    manifest = RunManifest.model_validate_json((folder / MANIFEST_FILE).read_text("utf-8"))
    raws: dict[Run, list[RawRecord]] = {run: [] for run in RUNS}
    states: list[tuple[float, ScenarioStateData]] = []
    fallbacks: dict[Run, int] = dict.fromkeys(RUNS, 0)
    for record in read_records(folder / EVENTS_FILE):
        if isinstance(record, RawRecord):
            raws[record.run].append(record)
        elif record.envelope.type is EventType.SCENARIO_STATE:
            t_s = record.envelope.sim_time_s
            if t_s is not None:
                states.append((t_s, ScenarioStateData.model_validate(record.envelope.data)))
        elif record.envelope.type is EventType.METRICS:
            metric = MetricsData.model_validate(record.envelope.data)
            name = metric.labels.get("run")
            if metric.name == POWER_FALLBACKS_METRIC and name in fallbacks:
                fallbacks[name] = int(metric.value)
    samples: dict[Run, list[Sample]] = {
        run: drawn for run, records in raws.items() if (drawn := _samples(run, records))
    }
    return RecordedRun(manifest, samples, sorted(states, key=lambda entry: entry[0]), fallbacks)


def check_energy(run: RecordedRun) -> EnergyCheck:
    """Each plant run's energy two ways, and energy saved two ways.

    Args:
        run: The recorded run.
    """
    kpis = run.manifest.final_kpis
    integrated: dict[Run, float] = {
        name: samples[-1].summary.energy_kwh for name, samples in run.samples.items()
    }
    own: dict[Run, float | None] = {
        name: plant_energy_kwh(kpis[name].boptest) if name in kpis else None for name in RUNS
    }
    differences: dict[Run, float | None] = dict.fromkeys(RUNS)
    for name, plant in own.items():
        if name in integrated and plant is not None:
            differences[name] = cross_check_pct(integrated[name], plant)
    return EnergyCheck(integrated, own, differences, _saved(own), _saved(integrated))


def _saved(energy: Mapping[Run, float | None]) -> float | None:
    """Energy saved by these energies, or ``None`` without both runs'."""
    spotcool, baseline = energy.get("spotcool"), energy.get("baseline")
    return None if spotcool is None or baseline is None else energy_saved_pct(baseline, spotcool)


def quote_blockers(run: RecordedRun) -> list[str]:
    """Why the run's energy saved may not be quoted, one sentence each; empty when it may.

    The figure to quote is energy saved by the plants' own energy; the world's step-mean
    integration is its check (``check_energy``). It takes a BOPTEST run that finished, lost no
    events, ran paced and was not driven by the stub agent, with both runs measured, both
    plants' energy, no power that fell back to its end-of-step value, and each run's
    integration within ``CROSS_CHECK_PCT`` of its plant's own.

    Args:
        run: The recorded run.
    """
    manifest, check = run.manifest, check_energy(run)
    blockers: list[str] = []
    if manifest.plant == "rc":
        blockers.append(
            "The plant is rc, our own reduced model, and its cross-check is circular: both "
            "energies come from the same sub-step sums."
        )
    elif manifest.plant not in BOPTEST_PLANTS:
        blockers.append(f"The plant is {manifest.plant}, not BOPTEST (local or hosted).")
    if manifest.ended != "finished":
        how = "it failed" if manifest.ended == "failed" else f"it was {manifest.ended}"
        blockers.append(f"The run did not finish: {how} ({manifest.end_reason}).")
    if manifest.dropped_events:
        blockers.append(f"The recorder dropped {manifest.dropped_events} events.")
    for name in RUNS:
        label, own, difference = _LABELS[name], check.own[name], check.differences[name]
        if name not in check.integrated:
            blockers.append(f"The {label} run has no measurements, so no saving can be computed.")
        elif own is None or own <= 0:
            blockers.append(f"The {label} plant reported no energy, so no saving can be computed.")
        elif difference is not None and abs(difference) > CROSS_CHECK_PCT:
            blockers.append(
                f"The {label} run's step-mean integration is {difference:+.3f} % off its "
                f"plant's own energy, more than {CROSS_CHECK_PCT:g} %."
            )
        if fallbacks := run.fallbacks.get(name, 0):
            blockers.append(
                f"The {label} run's power fell back to its end-of-step value in {fallbacks} of "
                "its steps."
            )
    if not manifest.settings.get("speed"):
        blockers.append(
            "The run was unpaced (speed 0): the agent's writes land at random points between "
            "steps, so it does not reproduce."
        )
    if manifest.settings.get("stub_agent"):
        blockers.append(
            "A stub agent ran it, with perfect perception: quote it only as an upper bound with "
            "perfect perception, labelled so."
        )
    return blockers


def verdict(run: RecordedRun) -> list[str]:
    """Whether the run's energy saved may be quoted, as lines.

    Args:
        run: The recorded run.

    Returns:
        ``Quotable: ...`` with the plants' own saving alone, or ``Not quotable:`` followed by
        every ``quote_blockers`` sentence.
    """
    blockers = quote_blockers(run)
    if blockers:
        return ["Not quotable:", *blockers]
    check = check_energy(run)
    assert check.saved_own is not None  # no blockers: both runs and both plants' energy
    agree = max(abs(d) for d in check.differences.values() if d is not None)
    return [
        f"Quotable: {check.saved_own:.2f} % saved (the plants' own energy; the world's "
        f"step-mean integration agrees within {agree:.3f} %)."
    ]


def arrivals(run: RecordedRun) -> list[Arrival]:
    """Every arrival in the run, with each plant run's recovery time.

    An arrival is a tick whose scenario state has people in a zone that had none at the tick
    before. Recovery is searched from the measurements stamped at the arrival on (the state
    the people walk into), against the baseline's cooling setpoint at the same stamp.

    Args:
        run: The recorded run; without a baseline run there is no band, and no arrivals.
    """
    baseline = {sample.t_s: sample.summary for sample in run.samples.get("baseline", [])}
    if not baseline:
        return []
    found: list[Arrival] = []
    before: dict[Zone, int] = {}
    for t_s, state in run.states:
        for scene in state.zones:
            if before.get(scene.zone) == 0 and scene.people > 0:
                minutes: dict[Run, float | None] = {
                    name: _recovery(scene.zone, t_s, samples, baseline)
                    for name, samples in run.samples.items()
                }
                found.append(Arrival(scene.zone, t_s, scene.people, minutes))
            before[scene.zone] = scene.people
    return found


def write_report(run_dir: Path, run_id: str) -> Path:
    """Write a recorded run's charts and ``report.md`` into its directory.

    Args:
        run_dir: Where the runs are.
        run_id: The run.

    Returns:
        The report's path.

    Raises:
        ModuleNotFoundError: If matplotlib (the ``report`` extra) is not installed.
        ValueError: If the run cannot be read (see ``load_run``).
        OSError: If a file cannot be read or written.
    """
    run = load_run(run_dir, run_id)
    folder = run_dir / run_id
    _draw_charts(run, folder)
    path = folder / REPORT_FILE
    path.write_text(_markdown(run, arrivals(run)), encoding="utf-8")
    return path


def _samples(run: Run, records: list[RawRecord]) -> list[Sample]:
    meter = EnergyMeter()
    samples: list[Sample] = []
    for record in sorted(records, key=lambda r: r.index):
        try:
            summary = metered_summary(run, record.raw, record.sim_time_s, meter)
        except KeyError:
            continue  # a state lacking a value (such as BOPTEST's initial one) is not drawn
        samples.append(Sample(record.sim_time_s, summary))
    return samples


def _recovery(
    zone: Zone, at_s: float, samples: list[Sample], baseline: dict[float, PlantMeasurementsData]
) -> float | None:
    for sample in samples:
        band = baseline.get(sample.t_s)  # both runs' stamps come from the same tick
        if sample.t_s < at_s - 1e-6 or band is None:  # a stamp is tick + step: allow rounding
            continue
        if _zone(sample.summary, zone).temp_c <= _zone(band, zone).cooling_sp_c + RECOVERY_MARGIN_K:
            return (sample.t_s - at_s) / 60
    return None


def _zone(summary: PlantMeasurementsData, zone: Zone) -> ZoneMeasurement:
    return next(measured for measured in summary.zones if measured.zone is zone)


def _markdown(run: RecordedRun, found: list[Arrival]) -> str:
    manifest = run.manifest
    runs: list[Run] = [name for name in RUNS if name in run.samples]
    ended = manifest.ended + (f" ({manifest.end_reason})" if manifest.end_reason else "")
    lines = [
        f"# Run {manifest.run_id}: {manifest.scenario} on the {manifest.plant} plant",
        "",
        *([f"**{STUB_AGENT_NOTE}**", ""] if manifest.settings.get("stub_agent") else []),
        f"- Simulated {sim_datetime(manifest.start_time_s):%Y-%m-%d}, "
        f"{SimClock.clock_text(manifest.start_time_s)} to {SimClock.clock_text(manifest.end_time_s)}"
        f" in {manifest.step_s / 60:g}-minute steps; {ended}.",
        f"- Recorded {manifest.created_utc:%Y-%m-%d %H:%M} UTC by spotcool-world "
        f"{manifest.world_version} (contracts {manifest.contracts_version}), commit "
        f"{manifest.git_sha or 'unknown'}; scenario sha256 {manifest.scenario_sha256 or 'unknown'}.",
        f"- Events the recorder dropped: {manifest.dropped_events}.",
        "",
        "## KPIs",
        "",
        *_kpi_table(run, runs),
        "",
        *_verdict_lines(run),
        "",
        "## Recovery after arrivals",
        "",
        *_recovery_lines(found, runs),
        "",
        "## Charts",
        "",
        f"![HVAC power and cumulative energy of both runs]({POWER_CHART})",
        "",
        f"![Zone temperatures against the baseline's cooling setpoint]({ZONES_CHART})",
        "",
        f"![People in each zone, from the scenario]({OCCUPANCY_CHART})",
        "",
        "## Honesty note",
        "",
        HONESTY_NOTE,
        "",
    ]
    return "\n".join(lines)


def _verdict_lines(run: RecordedRun) -> list[str]:
    """The verdict in Markdown: its first line, then any blockers as a list."""
    head, *blockers = verdict(run)
    return [head, "", *(f"- {blocker}" for blocker in blockers)] if blockers else [head]


def _kpi_table(run: RecordedRun, runs: list[Run]) -> list[str]:
    kpis, check = run.manifest.final_kpis, check_energy(run)
    area = load_catalog().area_m2
    rows: list[tuple[str, Mapping[Run, float | None], int]] = [
        ("HVAC energy, step-mean integration (kWh)", check.integrated, 1),
        (f"HVAC energy, plant's own: `ener_tot` x {area:g} m2 (kWh)", check.own, 1),
        ("Difference from the plant's own (%)", check.differences, 3),
        ("Energy saved, plants' own (%)", {"spotcool": check.saved_own}, 2),
        ("Energy saved, step-mean integration (%)", {"spotcool": check.saved_integrated}, 2),
        ("Power readings that fell back (steps)", run.fallbacks, 0),
        (
            "Comfort where people were (K h)",
            {name: kpi.comfort_where_people_kh for name, kpi in kpis.items()},
            2,
        ),
    ]
    names = dict.fromkeys(key for name in runs if name in kpis for key in kpis[name].boptest)
    for key in names:
        boptest: dict[Run, float | None] = {
            name: kpis[name].boptest.get(key) for name in runs if name in kpis
        }
        rows.append((f"BOPTEST `{key}`", boptest, 4))
    header = "| KPI | " + " | ".join(_LABELS[name] for name in runs) + " |"
    lines = [header, "|---|" + "---:|" * len(runs)]
    for label, values, digits in rows:
        cells = [_number(values.get(name), digits) for name in runs]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return lines


def _recovery_lines(found: list[Arrival], runs: list[Run]) -> list[str]:
    if not found:
        return ["No zone went from empty to occupied during the run."]
    header = "| Zone | Arrival | People | " + " | ".join(f"{_LABELS[n]} (min)" for n in runs) + " |"
    lines = [header, "|---|---|---:|" + "---:|" * len(runs)]
    never: list[str] = []
    for arrival in found:
        zone, at = ZONES[arrival.zone].display_name, SimClock.clock_text(arrival.at_s)
        cells = []
        for name in runs:
            minutes = arrival.minutes.get(name)
            cells.append("never" if minutes is None else f"{minutes:g}")
            if minutes is None:
                never.append(f"- {_LABELS[name]}: {zone} at {at} ({arrival.people} people)")
        lines.append(f"| {zone} | {at} | {arrival.people} | " + " | ".join(cells) + " |")
    band = f"the baseline's cooling setpoint plus {RECOVERY_MARGIN_K:g} K"
    if never:
        lines += ["", f"Arrivals that never recovered (warmer than {band} until the end):", ""]
        lines += never
    else:
        lines += ["", f"Every arrival recovered to {band}."]
    return lines


def _number(value: float | None, digits: int) -> str:
    # + 0.0 turns a rounded -0.0 into 0.0
    return "n/a" if value is None else f"{round(value, digits) + 0.0:.{digits}f}"


def _draw_charts(run: RecordedRun, folder: Path) -> None:
    """Draw the three charts with matplotlib's Agg backend (the ``report`` extra)."""
    from matplotlib.axes import Axes
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.ticker import MaxNLocator

    day_s = run.manifest.start_time_s // 86_400 * 86_400
    step_s = run.manifest.step_s

    def hours(t_s: float) -> float:
        return (t_s - day_s) / 3600

    def figure(rows: int, height: float, title: str) -> tuple[Figure, list[Axes]]:
        fig = Figure(figsize=(10, height), facecolor=_SURFACE, layout="constrained")
        FigureCanvasAgg(fig)
        axes = list(fig.subplots(rows, 1, sharex=True, squeeze=False)[:, 0])
        for ax in axes:
            ax.set_facecolor(_SURFACE)
            ax.grid(color=_GRID, linewidth=0.8)
            ax.tick_params(colors=_MUTED, labelsize=9)
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
            for side in ("left", "bottom"):
                ax.spines[side].set_color(_GRID)
        axes[-1].set_xlabel("Hour of the simulated day", color=_MUTED)
        fig.suptitle(title, color=_INK, fontsize=13)
        return fig, axes

    occupied = {
        zone: [t_s for t_s, state in run.states for s in state.zones if s.zone is zone and s.people]
        for zone in Zone
    }

    fig, (power, energy) = figure(2, 6.0, f"Run {run.manifest.run_id}: HVAC power and energy")
    for name, samples in run.samples.items():
        x = [hours(s.t_s) for s in samples]
        style = {"color": _COLORS[name], "linewidth": 1.6, "label": _LABELS[name]}
        # A step's mean power holds over the step that ends at its stamp.
        power.plot(x, [s.summary.total_power_kw for s in samples], drawstyle="steps-pre", **style)
        energy.plot(x, [s.summary.energy_kwh for s in samples], **style)
    power.set_ylabel("Step-mean power (kW)", color=_MUTED)
    energy.set_ylabel("Energy (kWh)", color=_MUTED)
    power.legend(frameon=False, labelcolor=_INK)
    fig.savefig(folder / POWER_CHART, dpi=120)

    title = "Zone temperatures; occupied steps shaded"
    fig, axes = figure(len(Zone), 11.0, title)
    baseline = run.samples.get("baseline", [])
    for ax, zone in zip(axes, Zone, strict=True):
        for t_s in occupied[zone]:
            ax.axvspan(hours(t_s), hours(t_s + step_s), color=_OCCUPIED, linewidth=0)
        for name, samples in run.samples.items():
            x = [hours(s.t_s) for s in samples]
            temps = [_zone(s.summary, zone).temp_c for s in samples]
            ax.plot(x, temps, color=_COLORS[name], linewidth=1.6, label=_LABELS[name])
        if baseline:
            setpoint = [_zone(s.summary, zone).cooling_sp_c for s in baseline]
            ax.plot(
                [hours(s.t_s) for s in baseline],
                setpoint,
                color=_MUTED,
                linewidth=1.2,
                linestyle="--",
                label="Baseline cooling setpoint",
                zorder=1.5,  # under the runs' lines: the baseline holds it exactly most of the day
            )
        ax.set_ylabel(f"{ZONES[zone].display_name} (°C)", color=_MUTED)
    axes[0].legend(frameon=False, labelcolor=_INK, ncols=3, loc="upper left")
    fig.savefig(folder / ZONES_CHART, dpi=120)

    fig, axes = figure(len(Zone), 7.5, "People in each zone (the scenario's ground truth)")
    for ax, zone in zip(axes, Zone, strict=True):
        x = [hours(t_s) for t_s, _ in run.states]
        people = [next(s.people for s in state.zones if s.zone is zone) for _, state in run.states]
        if x:
            x.append(x[-1] + step_s / 3600)
            people.append(people[-1])
        ax.fill_between(x, people, step="post", color=_COLORS["spotcool"], alpha=0.25, linewidth=0)
        ax.step(x, people, where="post", color=_COLORS["spotcool"], linewidth=1.6)
        ax.set_ylim(0, max(people, default=0) + 1)
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.set_ylabel(ZONES[zone].display_name, color=_MUTED)
    fig.savefig(folder / OCCUPANCY_CHART, dpi=120)
