"""Scenario linter: every rule a scenario must meet, reported as ``file:line: level: message``.

``check`` finds the problems in a validated ``Scenario``; ``lint`` reads a file and reports them
together with the file's own problems (bad YAML, schema errors), each at its line. An error means
the scenario cannot run as written; a warning means it runs, but perhaps not as meant.
"""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from spotcool_contracts import Zone
from spotcool_world.scenarios.loader import Loc, ScenarioFileError, load_scenario
from spotcool_world.scenarios.schema import (
    BookingEvent,
    ComplaintEvent,
    DoorOpenEvent,
    Scenario,
    Scene,
    clock_s,
    door_zones,
)

Level = Literal["error", "warning"]

PEOPLE_PER_SCENE: dict[Scene, tuple[int, int | None]] = {
    "empty": (0, 0),
    "few": (1, 3),
    "many": (4, None),
    "meeting": (2, None),
    "complaint": (1, None),
    "door_open": (0, None),
}
"""The people counts that fit each scene, as (least, most); ``None`` means no upper limit."""


@dataclass(frozen=True)
class Issue:
    """A rule a scenario breaks, located by keys and indexes rather than a line.

    Parameters:
        loc: Where in the scenario, e.g. ``("events", 2, "to")``.
        level: ``error`` or ``warning``.
        message: What is wrong.
    """

    loc: Loc
    level: Level
    message: str


@dataclass(frozen=True)
class Finding:
    """One linter message, printed as ``file:line: level: message``.

    Parameters:
        path: The scenario file.
        line: The 1-based line.
        level: ``error`` or ``warning``.
        message: What is wrong.
    """

    path: Path
    line: int
    level: Level
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.level}: {self.message}"


def lint(
    source: str | Path,
    *,
    step_s: float,
    footage_dir: Path | None = None,
    clips_dir: Path | None = None,
) -> list[Finding]:
    """Lint one scenario file: its YAML and schema problems, then ``check``'s, in line order.

    Args:
        source: A library name or a path to a ``.yaml`` file.
        step_s: The world's step; every time must be a multiple of it after midnight.
        footage_dir: Where voice cues find their audio (``footage_dir/audio/``).
        clips_dir: If given, also check that every ``<zone>_<scene>.mp4`` is there.

    Raises:
        ValueError: If ``source`` names no scenario, or the file is not UTF-8.
        OSError: If the file cannot be read.
    """
    try:
        loaded = load_scenario(source)
    except ScenarioFileError as exc:
        return [Finding(exc.path, line, "error", message) for line, message in exc.problems]
    issues = check(loaded.scenario, step_s=step_s, footage_dir=footage_dir, clips_dir=clips_dir)
    findings = [Finding(loaded.path, loaded.line(i.loc), i.level, i.message) for i in issues]
    return sorted(findings, key=lambda finding: finding.line)


def check(
    scenario: Scenario,
    *,
    step_s: float,
    footage_dir: Path | None = None,
    clips_dir: Path | None = None,
) -> list[Issue]:
    """Check the rules that need the whole scenario.

    Errors: a missing zone or one without an entry at the start; times not increasing within a
    zone, outside start-end, or off the step grid; ``from`` not before ``to``; overlapping door
    events in a zone, or a door event in a zone without a door contact; ``booked_at`` after the
    booking's ``from``. Warnings: ``people`` that does not fit the scene, a voice cue that
    will fall back to chat (no audio file, ``footage_dir`` unset, or the file missing), and with
    ``clips_dir``, a missing ``<zone>_<scene>.mp4``.

    Args:
        scenario: The scenario.
        step_s: The world's step; every time must be a multiple of it after midnight.
        footage_dir: Where voice cues find their audio (``footage_dir/audio/``).
        clips_dir: If given, also check that every ``<zone>_<scene>.mp4`` is there.

    Returns:
        The problems, in no particular order.
    """
    issues: list[Issue] = []
    start, end = clock_s(scenario.start), clock_s(scenario.end)

    def on_grid(loc: Loc, text: str) -> int:
        seconds = clock_s(text)
        if seconds % step_s:
            grid = f"{step_s / 60:g}-minute step grid (the world ticks every {step_s:g} s)"
            issues.append(Issue(loc, "error", f"{text} is not on the {grid}"))
        return seconds

    def timed(loc: Loc, text: str) -> int:
        seconds = on_grid(loc, text)
        if not start <= seconds <= end:
            span = f"{scenario.start}-{scenario.end}"
            issues.append(Issue(loc, "error", f"{text} is outside the scenario's {span}"))
        return seconds

    def interval(loc: Loc, event: DoorOpenEvent | BookingEvent) -> tuple[int, int]:
        opens, closes = timed((*loc, "from"), event.from_), timed((*loc, "to"), event.to)
        if opens >= closes:
            message = f"from {event.from_} is not before to {event.to}"
            issues.append(Issue((*loc, "to"), "error", message))
        return opens, closes

    on_grid(("start",), scenario.start)
    on_grid(("end",), scenario.end)
    if start >= end:
        message = f"end {scenario.end} is not after start {scenario.start}"
        issues.append(Issue(("end",), "error", message))

    clips_seen: set[str] = set()
    for zone in Zone:
        entries = scenario.zones.get(zone)
        if entries is None:
            issues.append(Issue(("zones",), "error", f"zone {zone} is missing; list every zone"))
            continue
        if not entries or clock_s(entries[0].at) != start:
            loc: Loc = ("zones", zone.value, 0, "at") if entries else ("zones", zone.value)
            message = f"zone {zone} needs an entry at the start, {scenario.start}"
            issues.append(Issue(loc, "error", message))
        previous: int | None = None
        for index, entry in enumerate(entries):
            loc = ("zones", zone.value, index)
            at = timed((*loc, "at"), entry.at)
            if previous is not None and at <= previous:
                message = f"{entry.at} is not after the zone's previous entry"
                issues.append(Issue((*loc, "at"), "error", message))
            previous = at
            least, most = PEOPLE_PER_SCENE[entry.scene]
            if entry.people < least or (most is not None and entry.people > most):
                need = f"{least}+" if most is None else f"{least}-{most}" if most > least else least
                message = f"scene {entry.scene} needs {need} people, not {entry.people}"
                issues.append(Issue((*loc, "people"), "warning", message))
            clip = f"{zone.value}_{entry.scene}.mp4"
            if clips_dir is not None and clip not in clips_seen:
                clips_seen.add(clip)
                if not (clips_dir / clip).is_file():
                    message = f"clip {clip} is not in {clips_dir}"
                    issues.append(Issue((*loc, "scene"), "warning", message))

    doors: dict[Zone, list[tuple[int, int, str]]] = defaultdict(list)
    for index, event in enumerate(scenario.events):
        loc = ("events", index)
        if isinstance(event, DoorOpenEvent):
            opens, closes = interval(loc, event)
            if event.zone not in door_zones():
                message = f"zone {event.zone} has no door contact in the office_bms point map"
                issues.append(Issue((*loc, "zone"), "error", message))
            for other_opens, other_closes, other in doors[event.zone]:
                if opens < other_closes and other_opens < closes:
                    message = f"door_open {event.from_}-{event.to} overlaps {other} in {event.zone}"
                    issues.append(Issue(loc, "error", message))
            doors[event.zone].append((opens, closes, f"{event.from_}-{event.to}"))
        elif isinstance(event, BookingEvent):
            opens, _ = interval(loc, event)
            if event.booked_at is not None and timed((*loc, "booked_at"), event.booked_at) > opens:
                message = f"booked_at {event.booked_at} is after the booking's from {event.from_}"
                issues.append(Issue((*loc, "booked_at"), "error", message))
        else:
            timed((*loc, "at"), event.at)
            if isinstance(event, ComplaintEvent) and event.channel == "voice":
                issues.extend(_voice_issues(loc, event, footage_dir))
    return issues


def _voice_issues(loc: Loc, cue: ComplaintEvent, footage_dir: Path | None) -> list[Issue]:
    """A warning if a voice cue will fall back to chat for want of its recording."""
    if cue.audio is None:
        return [Issue(loc, "warning", "voice cue has no audio file; it falls back to chat")]
    if footage_dir is None:
        message = "footage_dir is not set (SPOTCOOL_WORLD_FOOTAGE_DIR); the cue falls back to chat"
        return [Issue((*loc, "audio"), "warning", message)]
    audio = footage_dir / "audio" / cue.audio
    if not audio.is_file():
        return [
            Issue((*loc, "audio"), "warning", f"{audio} is missing; the cue falls back to chat")
        ]
    return []
