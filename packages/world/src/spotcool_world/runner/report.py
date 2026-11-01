"""The run report: a recorded run as ``runs/<run_id>/report.md`` and PNG charts.

``spotcool-world report <run_id>`` calls ``write_report``. The charts need the ``report`` extra
(matplotlib, drawn with its Agg backend); this module imports without it.

The series come from the record's raw measurements, through the ``TelemetryProcessor``'s own
functions, and the people from its ``scenario_state`` events. The KPIs are the final ones in the
manifest. The report adds recovery times, which the contract's ``KpiData`` has no field for.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from spotcool_contracts import ZONES, Zone
from spotcool_contracts.events import (
    EventType,
    PlantMeasurementsData,
    Run,
    ScenarioStateData,
    ZoneMeasurement,
)
from spotcool_world.clocks import SimClock
from spotcool_world.processors.kpi import RUNS
from spotcool_world.processors.telemetry import EnergyMeter, summarize
from spotcool_world.scenarios import sim_datetime
from spotcool_world.serializers.jsonl import (
    EVENTS_FILE,
    MANIFEST_FILE,
    RUN_ID_PATTERN,
    RawRecord,
    RunManifest,
    read_records,
)

REPORT_FILE = "report.md"
"""The report's name inside the run directory."""
RECOVERY_MARGIN_K = 0.5
"""A zone has recovered once it is no warmer than the baseline's cooling setpoint plus this."""
HONESTY_NOTE = (
    "BOPTEST's occupant heat follows its fixed schedule. Savings in zones that the cameras show "
    "empty may therefore be overstated by up to the occupants' heat. The RC plant and the "
    "`all_hands_day` scenario bracket this."
)
"""The report's honesty note, word for word."""
POWER_CHART = "power_energy.png"
ZONES_CHART = "zone_temperatures.png"
OCCUPANCY_CHART = "occupancy.png"

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
    """

    manifest: RunManifest
    samples: dict[Run, list[Sample]]
    states: list[tuple[float, ScenarioStateData]]


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
    for record in read_records(folder / EVENTS_FILE):
        if isinstance(record, RawRecord):
            raws[record.run].append(record)
        elif record.envelope.type is EventType.SCENARIO_STATE:
            t_s = record.envelope.sim_time_s
            if t_s is not None:
                states.append((t_s, ScenarioStateData.model_validate(record.envelope.data)))
    samples: dict[Run, list[Sample]] = {
        run: _samples(run, records) for run, records in raws.items() if records
    }
    return RecordedRun(manifest, samples, sorted(states, key=lambda entry: entry[0]))


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
            summary = summarize(run, record.raw)
        except KeyError as exc:
            raise ValueError(f"{run} record {record.index} lacks {exc}") from None
        energy = meter.add(record.sim_time_s, summary.total_power_kw)
        samples.append(Sample(record.sim_time_s, summary.model_copy(update={"energy_kwh": energy})))
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


def _kpi_table(run: RecordedRun, runs: list[Run]) -> list[str]:
    kpis = run.manifest.final_kpis
    saved = kpis["spotcool"].energy_saved_pct if "spotcool" in kpis else None
    rows: list[tuple[str, dict[Run, float | None], int]] = [
        ("HVAC energy (kWh)", {name: run.samples[name][-1].summary.energy_kwh for name in runs}, 1),
        ("Energy saved (%)", {"spotcool": saved}, 1),
        (
            "Comfort where people were (K h)",
            {name: kpi.comfort_where_people_kh for name, kpi in kpis.items()},
            2,
        ),
    ]
    names = dict.fromkeys(key for name in runs if name in kpis for key in kpis[name].boptest)
    for key in names:
        values: dict[Run, float | None] = {
            name: kpis[name].boptest.get(key) for name in runs if name in kpis
        }
        rows.append((f"BOPTEST `{key}`", values, 4))
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
    return "n/a" if value is None else f"{value:.{digits}f}"


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
        power.plot(x, [s.summary.total_power_kw for s in samples], **style)
        energy.plot(x, [s.summary.energy_kwh for s in samples], **style)
    power.set_ylabel("Power (kW)", color=_MUTED)
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
