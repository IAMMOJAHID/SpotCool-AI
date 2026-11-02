import asyncio
import math
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from .comfort import (
    draft_rating,
    fuse_sensation,
    grounded_reward,
    jet_speed,
    occupant_pmv,
    room_discomfort,
    update_room_temp,
)
from .config import AppConfig, ControlLimits
from .geometry import CameraModel, angle_between, choose_aim, locate_track, louver_angles, resolve_posture
from .memory import EpisodeStore, reidentify
from .meta_client import MetaBackend
from .perception import (
    analyze_door,
    attribute_complaints,
    build_tracks,
    confirm_door,
    cue_sign,
    extract_complaints,
    median_box,
    observations_from_spark,
    occupancy_count,
    parse_sam_output,
    parse_transcript,
    spark_prompt,
)
from .preprocess import TurnInput, motion_scores, plan_segments, prepare_segment, probe, select_keyframes
from .rl_controller import (
    WONDER_INSTRUCTIONS,
    apply_safety,
    critic_from_wire,
    grounded_evaluation,
    mv_from_wire,
    resolve_door,
    rule_based_policy,
    update_aim_membership,
    update_preferences,
    wonder_payload,
)
from .schemas import (
    ClipInfo,
    Complaint,
    ControllerState,
    CriticPolicyWire,
    ManipulatedVariables,
    OccupantState,
    SamParseResult,
    SparkWire,
    TranscriptTurn,
    TurnRecord,
    TurnState,
    parse_wire,
    strict_json_schema,
)
from .visualize import build_grid, encode_jpeg, encode_png, muse_hud_prompt, render_hud

SPARK_SCHEMA = strict_json_schema(SparkWire)
WONDER_SCHEMA = strict_json_schema(CriticPolicyWire)


@dataclass
class TurnContext:
    cfg: AppConfig
    backend: MetaBackend
    store: EpisodeStore
    previews: bool = True
    hud: str = "local"


@dataclass
class _Notes:
    degraded: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    response_ids: dict[str, str] = field(default_factory=dict)


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


async def _track(ctx: TurnContext, turn_input: TurnInput, notes: _Notes) -> tuple[Optional[SamParseResult], Optional[SamParseResult]]:
    """Upload the 1 FPS clip once and track "person" and "open door" in parallel."""
    settings = ctx.cfg.settings
    start = time.perf_counter()
    file_id: Optional[str] = None
    try:
        file_id = await asyncio.wait_for(ctx.backend.upload_video(turn_input.video_path), settings.upload_timeout_s)
        results = await asyncio.gather(
            *(
                asyncio.wait_for(ctx.backend.sam_track(file_id, concept), settings.sam_timeout_s)
                for concept in ("person", "open door")
            ),
            return_exceptions=True,
        )
    except Exception as exc:
        notes.degraded.append(f"sam: upload failed ({_describe(exc)})")
        return None, None
    finally:
        if file_id is not None:
            try:
                await asyncio.wait_for(ctx.backend.delete_file(file_id), 30)
            except Exception as exc:
                notes.warnings.append(f"uploaded clip not deleted ({_describe(exc)}); it expires on its own")
        notes.timings["sam_s"] = round(time.perf_counter() - start, 2)
    width, height = turn_input.frame_size
    parsed: list[Optional[SamParseResult]] = []
    for concept, result in zip(("person", "open door"), results):
        if isinstance(result, BaseException):
            notes.degraded.append(f"sam {concept}: {_describe(result)}")
            parsed.append(None)
            continue
        sam = parse_sam_output(result.text, concept)
        if result.status not in ("completed", None):
            notes.degraded.append(f"sam {concept}: response {result.status}; partial output used")
        if result.response_id:
            notes.response_ids[f"sam_{concept.replace(' ', '_')}"] = result.response_id
        if sam.frame_size and abs(sam.frame_size[0] / sam.frame_size[1] - width / height) > 0.02:
            notes.warnings.append(f"sam {concept}: frame {sam.frame_size} differs in aspect from the upload {width}x{height}")
        notes.warnings.extend(sam.warnings[:5])
        if len(sam.warnings) > 5:
            notes.warnings.append(f"sam {concept}: {len(sam.warnings) - 5} more parser warnings")
        parsed.append(sam)
    return parsed[0], parsed[1]


async def _transcribe(ctx: TurnContext, turn_input: TurnInput, notes: _Notes) -> list[TranscriptTurn]:
    if not turn_input.audio_paths:
        return []
    start = time.perf_counter()
    results = await asyncio.gather(
        *(
            asyncio.wait_for(ctx.backend.transcribe(path, index), ctx.cfg.settings.transcribe_timeout_s)
            for index, path in enumerate(turn_input.audio_paths)
        ),
        return_exceptions=True,
    )
    turns: list[TranscriptTurn] = []
    for path, result in zip(turn_input.audio_paths, results):
        source = "clip audio" if path.stem.endswith("_clip") else "attached audio"
        if isinstance(result, BaseException):
            notes.degraded.append(f"transcribe {source}: {_describe(result)}")
            continue
        turns.extend(parse_transcript(result.text, source))
    notes.timings["transcribe_s"] = round(time.perf_counter() - start, 2)
    return turns


async def _spark(ctx: TurnContext, grid_jpeg: bytes, prompt: str, notes: _Notes) -> Optional[SparkWire]:
    start = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            ctx.backend.spark_assess(grid_jpeg, prompt, SPARK_SCHEMA), ctx.cfg.settings.spark_timeout_s
        )
        if result.response_id:
            notes.response_ids["spark"] = result.response_id
        return parse_wire(SparkWire, result.text)
    except Exception as exc:
        notes.degraded.append(f"spark: {_describe(exc)}; default clo/met used, door unconfirmed")
        return None
    finally:
        notes.timings["spark_s"] = round(time.perf_counter() - start, 2)


def _applied_jet(before: ControllerState, cfg: AppConfig) -> tuple[Optional[float], Optional[float]]:
    """Direction of the jet during this clip: the applied (quantized) vanes of A_{t-1}."""
    applied, vrf = before.applied, before.vrf
    if applied.louver_mode == "swing":
        return None, None
    if vrf is not None and vrf.vane_h_angle_deg is not None:
        yaw = vrf.vane_h_angle_deg
    else:
        yaw = applied.louver_yaw_deg if cfg.profile.vane_horizontal is not None else 0.0
    pitch = vrf.vane_v_angle_deg if vrf is not None and vrf.vane_v_angle_deg is not None else applied.louver_pitch_deg
    return yaw, pitch


def _is_draft(occupant: OccupantState, complaints: Sequence[Complaint], previous: ManipulatedVariables, limits: ControlLimits) -> bool:
    if any(c.category == "draft" and occupant.occupant_key in c.attributed_to for c in complaints):
        return True
    if "shielding_from_airflow" in occupant.cues:
        return True
    cold = cue_sign(occupant.cues) < 0 or any(
        c.category == "too_cold" and occupant.occupant_key in c.attributed_to for c in complaints
    )
    in_jet = (
        previous.louver_mode == "fixed"
        and occupant.jet_angle_deg is not None
        and occupant.jet_angle_deg <= limits.draft_attribution_deg
    )
    return cold and in_jet


def _occupant_order(occupant: OccupantState) -> int:
    suffix = occupant.occupant_key.rsplit("-", 1)[-1]
    return int(suffix) if suffix.isdigit() else 0


async def run_turn(turn_input: TurnInput, turn: int, ctx: TurnContext) -> TurnRecord:
    cfg = ctx.cfg
    limits, cal = cfg.limits, cfg.calibration
    notes = _Notes(warnings=list(turn_input.warnings))
    if turn_input.preprocess_s:
        notes.timings["preprocess_s"] = turn_input.preprocess_s
    started = time.perf_counter()
    episode = ctx.store.load()
    if not episode.calibration:
        episode.calibration = cfg.calibration.model_dump()
    before, previous_record, warning = ctx.store.state_before(episode, turn)
    if warning:
        notes.warnings.append(warning)
    previous_state = previous_record.state if previous_record else None
    previous_action = before.applied
    frames = turn_input.frames
    n_frames, fps = len(frames), turn_input.fps
    width, height = turn_input.frame_size
    ctx.backend.begin_turn(turn, n_frames, (width, height))

    # Stage 1: SAM person + open door on the uploaded clip; transcription runs alongside.
    transcription = asyncio.create_task(_transcribe(ctx, turn_input, notes))
    try:
        person_result, door_result = await _track(ctx, turn_input, notes)
    except BaseException:
        transcription.cancel()
        raise
    tracks = build_tracks(person_result, n_frames, limits.person_min_confidence, notes.warnings) if person_result else []
    door = analyze_door(door_result, n_frames, fps, limits.door_min_confidence, limits.door_gap_bridge_s)

    # Stage 2: keyframes (evenly spaced + motion peaks) labelled with SAM IDs -> Muse Spark.
    boxes_by_frame: dict[int, list[tuple[float, float, float, float]]] = defaultdict(list)
    for track in tracks:
        for detection in track.detections:
            boxes_by_frame[detection.frame].append(detection.box)
    keyframes = select_keyframes(n_frames, motion_scores(frames, boxes_by_frame))
    grid = build_grid(frames, keyframes, tracks, door.detections_by_frame, fps)
    track_ids = [t.track_id for t in tracks]
    spark: Optional[SparkWire] = None
    if tracks or door.state.open_frames:
        prompt = spark_prompt(track_ids, keyframes, fps, turn_input.duration_s)
        spark = await _spark(ctx, encode_jpeg(grid), prompt, notes)
    observations = observations_from_spark(spark, track_ids, notes.warnings)
    confirm_door(door, keyframes, spark.door_open_by_frame if spark else None)
    transcript = await transcription
    complaints = extract_complaints(transcript, turn_input.texts)

    # Geometry, re-identification, room temperature and comfort.
    camera = CameraModel.from_calibration(cal, width, height)
    working = before.model_copy(deep=True)
    medians = {t.track_id: median_box(t) for t in tracks}
    mapping, working.occupants, working.next_occupant_index = reidentify(
        medians, working.occupants, turn, working.next_occupant_index, limits.reid_gate, limits.reid_max_absent_turns
    )
    if cal.room_temp_c is not None:
        room_temp = cal.room_temp_c
    else:
        start_temp = before.room_temp_c if before.room_temp_c is not None else previous_action.target_temp_c
        room_temp = update_room_temp(start_temp, previous_action, turn_input.gap_s + turn_input.duration_s, cal)
    working.room_temp_c = room_temp
    jet_yaw, jet_pitch = _applied_jet(before, cfg)
    occupants: list[OccupantState] = []
    for track in tracks:
        observation = observations[track.track_id]
        box = medians[track.track_id]
        posture = resolve_posture(observation.posture, box, width, height)
        location = locate_track(camera, track, posture, cal)
        aim_height = cal.standing_aim_m if posture in ("standing", "walking") else cal.seated_aim_m
        point = (location.x, aim_height, location.z)
        yaw, pitch = louver_angles(cal.ac_position, point)
        angle = angle_between(jet_yaw, jet_pitch, yaw, pitch) if jet_yaw is not None and jet_pitch is not None else None
        speed = jet_speed(
            previous_action.fan_speed, angle, math.dist(cal.ac_position, point),
            previous_action.louver_mode == "swing", cal.room_air_speed_ms,
        )  # fmt: skip
        pmv, ppd = occupant_pmv(room_temp, cal.room_air_speed_ms, cal.rh_pct, observation.activity_met, observation.clothing_clo)
        occupants.append(
            OccupantState(
                occupant_key=mapping[track.track_id],
                sam_id=f"P{track.track_id}",
                present_ratio=round(len(track.detections) / n_frames, 3),
                bbox=box,
                posture=posture,  # type: ignore[arg-type]
                distance_m=round(location.distance_m, 3),
                distance_method=location.method,  # type: ignore[arg-type]
                position_m=(round(location.x, 3), aim_height, round(location.z, 3)),
                yaw_deg=round(yaw, 2),
                pitch_deg=round(pitch, 2),
                clothing_clo=observation.clothing_clo,
                activity_met=observation.activity_met,
                cues=observation.cues,
                apparent_sensation=observation.apparent_sensation,
                pmv=round(pmv, 3),
                ppd=round(ppd, 1),
                jet_angle_deg=None if angle is None else round(angle, 1),
                jet_speed_ms=round(speed, 3),
                draft_rating_pct=round(draft_rating(room_temp, speed), 1),
                distance_estimates_m=location.estimates,
            )
        )
    occupants.sort(key=_occupant_order)
    attribute_complaints(complaints, occupants)
    for occupant in occupants:
        occupant.observed_sensation, occupant.observed = fuse_sensation(occupant, complaints)
        occupant.draft = _is_draft(occupant, complaints, previous_action, limits)
    update_aim_membership(working.occupants, occupants, limits)
    state = TurnState(
        turn=turn,
        clip=ClipInfo(
            label=turn_input.label,
            source=turn_input.source,
            segment_start_s=turn_input.segment_start_s,
            duration_s=turn_input.duration_s,
            fps=fps,
            frames=n_frames,
            frame_size=(width, height),
            audio_sources=[path.name for path in turn_input.audio_paths],
            texts=turn_input.texts,
            gap_s=turn_input.gap_s,
            keyframes=keyframes,
        ),
        occupants=occupants,
        occupant_count=occupancy_count(tracks, n_frames),
        occupied=bool(tracks),
        door=resolve_door(door.state, before, limits),
        transcript=transcript,
        complaints=complaints,
        scene_summary=spark.scene_summary if spark else "",
        discomfort=round(room_discomfort(occupants, complaints), 4),
        room_temp_c=round(room_temp, 3),
    )

    # Critic and policy: Muse Wonder anchored by the grounded reward, rule-based fallback.
    first_turn = previous_state is None
    grounded = grounded_reward(state, previous_state, previous_action)
    aim = choose_aim(occupants, working.occupants, previous_action, limits, cfg.profile)
    suggestion, suggestion_rationale = rule_based_policy(state, working, aim, cfg)
    proposed, rationale, source = suggestion, suggestion_rationale, "fallback"
    critic, preference_wire = None, []
    if ctx.backend.supports_policy:
        start = time.perf_counter()
        try:
            payload = wonder_payload(state, previous_state, working, grounded, aim, suggestion, suggestion_rationale, limits)
            result = await asyncio.wait_for(
                ctx.backend.wonder_decide(WONDER_INSTRUCTIONS, payload, WONDER_SCHEMA), cfg.settings.wonder_timeout_s
            )
            wire = parse_wire(CriticPolicyWire, result.text)
            proposed, mv_notes = mv_from_wire(wire.manipulated_variables, suggestion)
            notes.warnings.extend(f"wonder: {note}" for note in mv_notes)
            critic = critic_from_wire(wire.reward_evaluation, grounded, first_turn)
            rationale = wire.rationale.strip() or rationale
            preference_wire = wire.updated_preferences
            source = "llm"
            if result.response_id:
                notes.response_ids["wonder"] = result.response_id
        except Exception as exc:
            notes.degraded.append(f"wonder: {_describe(exc)}; rule-based policy and grounded reward used")
        finally:
            notes.timings["wonder_s"] = round(time.perf_counter() - start, 2)
    if critic is None:
        critic = grounded_evaluation(grounded, first_turn)

    # Safety layer, then memory.
    safety = apply_safety(proposed, state, before, cfg)
    after = safety.state
    after.occupants, after.next_occupant_index = working.occupants, working.next_occupant_index
    after.room_temp_c = room_temp
    profiles = update_preferences(after, state, preference_wire, limits)

    later = sorted(r.turn for r in episode.turns if r.turn > turn)
    if later:
        notes.warnings.append(f"turn {turn} re-run: later turns {later} were computed from the old one and dropped")
    if ctx.previews:
        await _write_previews(ctx, turn, frames, keyframes, grid, medians, mapping, state, safety, notes)
    notes.timings["total_s"] = round(time.perf_counter() - started, 2)
    state.degraded = list(notes.degraded)
    record = TurnRecord(
        turn=turn,
        mode=ctx.backend.name,
        state=state,
        previous_action=previous_action,
        reward=critic,
        proposal_source=source,  # type: ignore[arg-type]
        proposed=proposed,
        applied=safety.applied,
        adjustments=safety.adjustments,
        overrides=safety.overrides,
        vrf_command=safety.vrf,
        occupancy={
            "occupied": state.occupied,
            "count": state.occupant_count,
            "empty_streak_s": round(after.empty_streak_s, 1),
        },
        rationale=rationale,
        updated_preferences=profiles,
        state_before=before,
        state_after=after,
        degraded=notes.degraded,
        warnings=notes.warnings,
        timings_s=notes.timings,
        response_ids=notes.response_ids,
    )
    ctx.store.upsert(episode, record)
    ctx.store.save(episode)
    (ctx.store.dir / f"turn_{turn:03d}.json").write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return record


async def _write_previews(ctx, turn, frames, keyframes, grid, medians, mapping, state, safety, notes) -> None:  # type: ignore[no-untyped-def]
    ctx.store.dir.mkdir(parents=True, exist_ok=True)
    (ctx.store.dir / f"turn_{turn:03d}_grid.jpg").write_bytes(encode_jpeg(grid))
    if ctx.hud == "none":
        return
    frame = frames[keyframes[-1]] if keyframes else frames[-1]
    boxes = {mapping[track_id]: box for track_id, box in medians.items()}
    title = f"turn {turn} | {state.clip.label}"
    hud = render_hud(frame, boxes, state.occupants, safety.applied, safety.vrf, state.door, ctx.cfg.calibration, title)
    (ctx.store.dir / f"turn_{turn:03d}_hud.jpg").write_bytes(encode_jpeg(hud))
    if ctx.hud == "muse":
        target = max(state.occupants, key=lambda o: o.observed_sensation).occupant_key if state.occupants else None
        start = time.perf_counter()
        try:
            image = await asyncio.wait_for(
                ctx.backend.image_edit(encode_png(frame), muse_hud_prompt(safety.applied, target)),
                ctx.cfg.settings.image_timeout_s,
            )
            if image:
                (ctx.store.dir / f"turn_{turn:03d}_hud_muse.png").write_bytes(image)
            else:
                notes.warnings.append("muse-image returned no image")
        except Exception as exc:
            notes.warnings.append(f"muse-image HUD: {_describe(exc)}")
        finally:
            notes.timings["image_s"] = round(time.perf_counter() - start, 2)


@dataclass
class ClipSpec:
    path: Path
    audio: Optional[Path] = None
    texts: list[str] = field(default_factory=list)


async def run_sequence(
    clips: Sequence[ClipSpec],
    first_turn: int,
    ctx: TurnContext,
    segment_s: Optional[float],
    gap_s: float,
    on_turn: Callable[[TurnRecord], None],
) -> list[TurnRecord]:
    """One turn per clip (or per segment of a long clip), in order, carrying state forward."""
    records: list[TurnRecord] = []
    turn = first_turn
    for spec in clips:
        clip = await asyncio.to_thread(probe, spec.path)
        segments = plan_segments(clip.duration_s, segment_s)
        for index, (start, length) in enumerate(segments):
            label = spec.path.name if len(segments) == 1 else f"{spec.path.name}[{start:.0f}-{start + length:.0f}s]"
            with tempfile.TemporaryDirectory(prefix="spotcool_", ignore_cleanup_errors=True) as workdir:
                turn_input = await prepare_segment(
                    clip,
                    Path(workdir),
                    start,
                    length,
                    label=label,
                    attached_audio=spec.audio if index == 0 else None,
                    texts=spec.texts if index == 0 else (),
                    gap_s=gap_s if index == 0 else 0.0,
                    timeout_s=ctx.cfg.settings.ffmpeg_timeout_s,
                )
                record = await run_turn(turn_input, turn, ctx)
            on_turn(record)
            records.append(record)
            turn += 1
    return records
