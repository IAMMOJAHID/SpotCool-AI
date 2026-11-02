"""Run SpotCool closed-loop turns on recorded clips.

    python run_turn.py --video videos/sample_1.mp4 --turn 1
    python run_turn.py --video c1.mp4 c2.mp4 c3.mp4            # one turn per clip, in order
    python run_turn.py --video videos/2.mp4 --segment-seconds 60
    python run_turn.py --mode mock --reset --video videos/sample_1.mp4

A clip's sidecar files attach to its first turn: <clip>.wav (complaint audio) and <clip>.txt
(typed complaints, one per line, like the world's POST /chat).
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from app.spotcool_agent.config import load_config
from app.spotcool_agent.memory import EpisodeStore
from app.spotcool_agent.meta_client import make_backend
from app.spotcool_agent.pipeline import ClipSpec, TurnContext, run_sequence
from app.spotcool_agent.schemas import TurnRecord, TurnState


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", nargs="+", type=Path, required=True, help="clip(s), one turn each")
    parser.add_argument("--turn", type=int, help="turn number of the first clip (default: next in the episode)")
    parser.add_argument("--episode", default="default", help="episode name under --out (default: default)")
    parser.add_argument("--reset", action="store_true", help="start the episode from scratch")
    parser.add_argument("--audio", type=Path, help="complaint audio for the first clip (overrides its sidecar .wav)")
    parser.add_argument("--complaint", action="append", default=[], help="typed complaint for the first clip (repeatable)")
    parser.add_argument("--gap-s", type=float, default=0.0, help="seconds between consecutive clips (default 0)")
    parser.add_argument("--segment-seconds", type=float, default=60.0, help="split longer clips into turns (0 = never)")
    parser.add_argument("--mode", choices=("live", "mock", "replay"), default="live")
    parser.add_argument("--record", action="store_true", help="save raw live responses for --mode replay")
    parser.add_argument("--cassette", type=Path, help="recorded responses for --mode replay")
    parser.add_argument("--calibration", type=Path, help="JSON with partial settings/calibration/limits")
    parser.add_argument("--vrf-profile", choices=("mitsubishi", "daikin", "lg"))
    parser.add_argument("--room-temp", type=float, help="measured room temperature in C (default: lag model)")
    parser.add_argument("--rh", type=float, help="relative humidity in %% (default 50)")
    parser.add_argument("--hud", choices=("local", "muse", "none"), default="local")
    parser.add_argument("--no-previews", action="store_true", help="do not save grid/HUD images")
    parser.add_argument("--out", type=Path, default=Path("runs"))
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def format_turn(record: TurnRecord, previous: Optional[TurnState], store_dir: Path) -> str:
    state = record.state
    lines = []
    clip = state.clip
    header = f"== Turn {record.turn}: {clip.label} ({clip.duration_s:.0f}s, {clip.frames} frames) mode={record.mode} "
    lines.append(header + "=" * max(0, 100 - len(header)))
    door = state.door
    confirmed = {True: "confirmed", False: "NOT confirmed", None: "unconfirmed"}[door.spark_confirmed]
    lines.append(
        f"Perception  occupied={state.occupied} count={state.occupant_count} | door open {door.effective_open_s:.0f}s "
        f"({confirmed}{', override ON' if door.override_active else ''}) | room {state.room_temp_c:.1f} C"
    )
    if state.occupants:
        lines.append(f"  {'key':7}{'sam':5}{'dist':>6} {'via':8}{'yaw':>6}{'pitch':>7}{'clo':>5}{'met':>5}{'s_i':>6}{'PMV':>6}  cues")
        for o in state.occupants:
            cues = ",".join(o.cues) or "-"
            flag = " DRAFT" if o.draft else ""
            lines.append(
                f"  {o.occupant_key:7}{o.sam_id:5}{o.distance_m:5.1f}m {o.distance_method:8}{o.yaw_deg:+6.0f}{o.pitch_deg:+7.0f}"
                f"{o.clothing_clo:5.1f}{o.activity_met:5.1f}{o.observed_sensation:+6.1f}{o.pmv:+6.1f}  {cues}{flag}"
            )
    for complaint in state.complaints:
        who = ",".join(complaint.attributed_to) or "room"
        lines.append(f"  complaint {complaint.category} ({complaint.source}) -> {who}: \"{complaint.text[:70]}\"")
    if state.scene_summary:
        lines.append(f"  scene: {state.scene_summary[:110]}")
    reward = record.reward
    lines.append(
        f"Reward      R_t={reward.score:+.2f} ({reward.source}) grounded={reward.grounded.score:+.2f} trend={reward.trend}"
    )
    lines.append(f"  {reward.explanation[:180]}")
    if previous is not None:
        lines.append(
            f"State delta discomfort {previous.discomfort:.2f} -> {state.discomfort:.2f} | occupants "
            f"{len(previous.occupants)} -> {len(state.occupants)} | room {previous.room_temp_c:.1f} -> {state.room_temp_c:.1f} C"
        )
    lines.append(f"Action      proposal from {record.proposal_source}: {record.rationale[:150]}")
    lines.append(f"  {'field':18}{'A_t-1':>10}{'proposed':>10}{'applied':>10}  reasons")
    reasons = {a.field: a.reasons for a in record.adjustments}
    for name in type(record.applied).model_fields:
        before, proposed, applied = (getattr(m, name) for m in (record.previous_action, record.proposed, record.applied))
        shown = [f"{v:.1f}" if isinstance(v, float) else str(v) for v in (before, proposed, applied)]
        lines.append(f"  {name:18}{shown[0]:>10}{shown[1]:>10}{shown[2]:>10}  {'; '.join(reasons.get(name, []))}")
    vrf = record.vrf_command
    points = " ".join(f"{k}={v}" for k, v in vrf.points().items())
    lines.append(f"VRF         {points}  changed: {', '.join(vrf.changed) or 'none'}")
    for override in record.overrides:
        lines.append(f"Override    {override}")
    for item in record.degraded:
        lines.append(f"Degraded    {item}")
    for item in record.warnings:
        lines.append(f"Warning     {item}")
    timings = " ".join(f"{k}={v:.1f}s" for k, v in record.timings_s.items())
    lines.append(f"Saved       {store_dir / f'turn_{record.turn:03d}.json'}  ({timings})")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    load_dotenv()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    cfg = load_config(args.calibration)
    cfg.settings.mode = args.mode
    if args.vrf_profile:
        cfg.calibration.vrf_profile = args.vrf_profile
    if args.room_temp is not None:
        cfg.calibration.room_temp_c = args.room_temp
    if args.rh is not None:
        cfg.calibration.rh_pct = args.rh

    missing = [str(path) for path in args.video if not path.is_file()]
    if missing:
        print(f"error: clip not found: {', '.join(missing)}", file=sys.stderr)
        return 2
    store = EpisodeStore(args.out, args.episode)
    if args.reset:
        store.reset()
    record_dir = store.dir / "cassette" if args.record else None
    try:
        backend = make_backend(cfg.settings, cassette_dir=args.cassette, record_dir=record_dir)
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    clips = []
    for index, path in enumerate(args.video):
        audio = path.with_suffix(".wav") if path.with_suffix(".wav").is_file() else None
        sidecar = path.with_suffix(".txt")
        texts = [line.strip() for line in sidecar.read_text(encoding="utf-8").splitlines() if line.strip()] if sidecar.is_file() else []
        if index == 0:
            audio = args.audio or audio
            texts = [*texts, *args.complaint]
        clips.append(ClipSpec(path=path, audio=audio, texts=texts))

    first_turn = args.turn if args.turn is not None else EpisodeStore.next_turn(store.load())
    ctx = TurnContext(cfg=cfg, backend=backend, store=store, previews=not args.no_previews, hud=args.hud)
    earlier = next((r for r in store.load().turns if r.turn == first_turn - 1), None)
    previous_state: Optional[TurnState] = earlier.state if earlier else None

    def on_turn(record: TurnRecord) -> None:
        nonlocal previous_state
        print(format_turn(record, previous_state, store.dir) + "\n", flush=True)
        previous_state = record.state

    try:
        records = asyncio.run(run_sequence(clips, first_turn, ctx, args.segment_seconds or None, args.gap_s, on_turn))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    if record_dir is not None:
        print(f"Recorded raw responses to {record_dir} (replay with --mode replay --cassette {record_dir})")
    print(f"Episode '{args.episode}': {len(records)} turn(s) written to {store.dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
