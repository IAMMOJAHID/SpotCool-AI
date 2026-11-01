"""08: Scenarios: the film script of a demo day, linted and compiled.

A scenario says, for every 15-minute step, what each zone's camera shows and how many people are
in it, when the West door stands open, which meetings the calendar shows and when an occupant
complains. This example lints the three library scenarios, then compiles hybrid_tuesday and
shows three of its ticks: 08:30 (the staff arrive), 10:15 (the West door stands open) and 14:15
(the complaint in the South meeting room). Each snapshot shows every zone, the door, the events
in force, the complaints due on that tick and the bookings the calendar shows.

The voice complaint needs footage_dir (SPOTCOOL_WORLD_FOOTAGE_DIR) for its recording; without
it the linter warns that the cue falls back to chat.

Run: uv run python packages/world/examples/foundational/08_scenario_lint.py
"""

from spotcool_world.clocks import SimClock
from spotcool_world.scenarios import (
    CompiledScenario,
    compile_scenario,
    library_names,
    lint,
    load_scenario,
    sim_datetime,
)
from spotcool_world.services import WorldSettings


def snapshot(scenario: CompiledScenario, t_s: float, step_s: float) -> None:
    state = scenario.state_at(t_s)
    print(f"\n{scenario.name} at {SimClock.clock_text(t_s)} ({sim_datetime(t_s):%Y-%m-%d}):")
    print("  zone  scene      people  door    clothing")
    for z in state.zones:
        door = "-" if z.door_open is None else "open" if z.door_open else "closed"
        clothing = z.clothing or "-"
        print(f"  {z.zone.value:<5} {z.scene:<10} {z.people:>6}  {door:<7} {clothing}")
    print(f"  active events: {', '.join(state.active_events) or 'none'}")
    window = f"({SimClock.clock_text(t_s - step_s)}, {SimClock.clock_text(t_s)}]"
    cues = scenario.cues_between(t_s - step_s, t_s)
    for cue in cues:
        audio = f" (audio {cue.audio})" if cue.audio else ""
        print(f'  cue in {window}: {cue.zone.value} {cue.channel} "{cue.text}"{audio}')
    if not cues:
        print(f"  cues in {window}: none")
    for room, items in scenario.bookings_at(t_s).items():
        shown = [
            f'{i.start.dateTime[11:16]}-{i.end.dateTime[11:16]} {i.status} "{i.subject}"'
            for i in items
        ]
        print(f"  calendar {room}: {'; '.join(shown) or 'no bookings'}")


def main() -> None:
    settings = WorldSettings()
    step_s = settings.step_s
    print(f"Linting the library on the {step_s / 60:g}-minute step grid:")
    for name in library_names():
        findings = lint(name, step_s=step_s, footage_dir=settings.footage_dir)
        errors = sum(f.level == "error" for f in findings)
        print(f"  {name}: {errors} error(s), {len(findings) - errors} warning(s)")
        for finding in findings:
            print(f"    line {finding.line}: {finding.level}: {finding.message}")

    loaded = load_scenario("hybrid_tuesday")
    tuesday = compile_scenario(loaded.scenario, step_s=step_s)
    day_s = loaded.scenario.day * 86_400
    for hours, minutes in ((8, 30), (10, 15), (14, 15)):
        snapshot(tuesday, day_s + hours * 3600 + minutes * 60, step_s)


if __name__ == "__main__":
    main()
