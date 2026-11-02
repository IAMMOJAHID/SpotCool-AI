import asyncio
import base64
import json
import logging
import math
import os
import re
import time
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any, Optional, Sequence
from uuid import uuid4

import cv2
from openai import AsyncOpenAI, BadRequestError
from PIL import Image

if __package__:
    from .spotcool import agent, control, media, perception, physics
    from .spotcool import config as cortex_config
    from .spotcool.schemas import ManipulatedVariables, SceneSemantics
else:
    from spotcool import agent, control, media, perception, physics
    from spotcool import config as cortex_config
    from spotcool.schemas import ManipulatedVariables, SceneSemantics

__all__ = [
    "ManipulatedVariables",
    "SceneSemantics",
    "get_client",
    "process_frames",
    "process_window",
]

LOGGER = logging.getLogger("spotcool.pipeline")

BOX_REGEX = re.compile(r"x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)")


def _positive_float_from_env(name: str, default: float) -> float:
    value = os.getenv(name, str(default))
    try:
        parsed = float(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive number.") from exc
    if parsed <= 0:
        raise RuntimeError(f"{name} must be a positive number.")
    return parsed


def _positive_int_from_env(name: str, default: int) -> int:
    value = os.getenv(name, str(default))
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer.") from exc
    if parsed < 1:
        raise RuntimeError(f"{name} must be a positive integer.")
    return parsed


def _json_object_from_env(name: str, default: dict[str, Any]) -> dict[str, Any]:
    value = os.getenv(name)
    if not value:
        return default.copy()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} must contain a JSON object.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{name} must contain a JSON object.")
    return parsed


@lru_cache(maxsize=1)
def get_client() -> AsyncOpenAI:
    api_key = os.getenv("META_API_KEY")
    if not api_key:
        raise RuntimeError("META_API_KEY must be set to call the Meta AI API.")

    return AsyncOpenAI(
        base_url=os.getenv("META_API_BASE_URL", "https://api.meta.ai/v1"),
        api_key=api_key,
        timeout=_positive_float_from_env("META_API_TIMEOUT_SECONDS", 90.0),
        max_retries=cortex_config.non_negative_int_from_env("META_API_MAX_RETRIES", 1),
    )


def _pipeline_mode() -> str:
    return cortex_config.choice_from_env("PIPELINE_MODE", "cortex", {"cortex", "legacy"})


def _target_control_variables() -> dict[str, Any]:
    return _json_object_from_env(
        "TARGET_CONTROL_VARIABLES", cortex_config.DEFAULT_TARGET_CONTROL_VARIABLES
    )


# --------------------------------------------------------------------------
# Legacy MVP path (PIPELINE_MODE=legacy, and the fallback if Cortex fails)
# --------------------------------------------------------------------------

def _encode_frame(frame: Any) -> str:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(rgb)
    buffer = BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=_positive_int_from_env("JPEG_QUALITY", 85),
    )
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _read_clip_frames(
    video_path: str | Path,
    clip_start_seconds: float,
    clip_end_seconds: float,
) -> list[str]:
    if clip_start_seconds < 0 or clip_end_seconds <= clip_start_seconds:
        raise ValueError("Clip timestamps must satisfy 0 <= start < end.")

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            raise RuntimeError(f"Video has an invalid frame rate: {video_path}")

        sample_interval = _positive_float_from_env(
            "FRAME_SAMPLE_INTERVAL_SECONDS",
            2.0,
        )
        max_frames = _positive_int_from_env("MAX_FRAMES_PER_CLIP", 30)
        clip_duration = clip_end_seconds - clip_start_seconds
        sample_step = max(sample_interval, clip_duration / max_frames)

        frames: list[str] = []
        sample_time = clip_start_seconds
        while sample_time < clip_end_seconds and len(frames) < max_frames:
            capture.set(cv2.CAP_PROP_POS_MSEC, sample_time * 1000)
            success, frame = capture.read()
            if not success:
                if not frames:
                    raise RuntimeError(
                        f"Could not read video frame at {sample_time:.3f}s "
                        f"from {video_path}."
                    )
                break
            frames.append(_encode_frame(frame))
            sample_time += sample_step

        if not frames:
            raise RuntimeError(
                f"No video frames found between {clip_start_seconds} and "
                f"{clip_end_seconds} seconds in {video_path}."
            )
        return frames
    finally:
        capture.release()


async def run_sam31(b64_frame: str, concept: str) -> list[dict[str, Any]]:
    response = await get_client().responses.create(
        model=os.getenv("SAM_MODEL", "sam-3.1"),
        input=[{
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": concept},
                {
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{b64_frame}",
                },
            ],
        }],
    )
    boxes = []
    for match in BOX_REGEX.finditer(response.output_text):
        x1, y1, x2, y2, width, height = map(int, match.groups())
        boxes.append({
            "cx": round((x1 + x2) / (2 * width), 3),
            "cy": round((y1 + y2) / (2 * height), 3),
            "z_est_m": round(1.8 / max((y2 - y1) / height, 0.1), 2),
        })
    return boxes


async def run_muse_spark(b64_grids: Sequence[str]) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{
        "type": "input_text",
        "text": os.getenv(
            "SCENE_ANALYSIS_PROMPT",
            "Evaluate room thermal load parameters across these frames.",
        ),
    }]
    content.extend(
        {
            "type": "input_image",
            "image_url": f"data:image/jpeg;base64,{b64_grid}",
        }
        for b64_grid in b64_grids
    )

    response = await get_client().responses.parse(
        model=os.getenv("SCENE_MODEL", "muse-spark-1.3"),
        input=[{"type": "message", "role": "user", "content": content}],
        text_format=SceneSemantics,
    )
    if response.output_parsed is None:
        raise RuntimeError("Scene analysis returned no parsed response.")
    return response.output_parsed.model_dump()


async def _legacy_process_frames(
    b64_frames: Sequence[str],
    current_control_variables: dict[str, Any],
    current_manipulated_variables: dict[str, Any],
    turn_history: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], Optional[str]]:
    if not b64_frames:
        raise ValueError("At least one sampled video frame is required.")

    persons, secondary_objects, semantics = await asyncio.gather(
        run_sam31(
            b64_frames[-1],
            os.getenv("SAM_PERSON_CONCEPT", "humans"),
        ),
        run_sam31(
            b64_frames[-1],
            os.getenv("SAM_SECONDARY_CONCEPT", "fan"),
        ),
        run_muse_spark(b64_frames),
    )
    telemetry = {
        "persons": persons,
        "doors": secondary_objects,
        "semantics": semantics,
        "pipeline_mode": "legacy",
    }

    target_control_variables = _target_control_variables()
    system_instructions = os.getenv(
        "CONTROL_SYSTEM_INSTRUCTIONS",
        "You are an automated ASHRAE building management controller. "
        "Analyze numeric zone sensor telemetry and active HVAC setpoints. "
        "Return the JSON setpoint delta. Set unchanged parameters to null.",
    )
    prompt = f"""
    {system_instructions}
    Recent Turn History (last {len(turn_history)} ticks): {list(turn_history)}
    The current manipulated variables are {current_manipulated_variables}; current building/room control variables are {current_control_variables}.
    Our aim is to achieve this state of control variables: {target_control_variables}
    We analysed the latest clip of the room and here are the details:
    SAM spatial persons: {persons} | Additional detected objects: {secondary_objects}
    Muse Spark semantics: {semantics}
    Analyse this information thoroughly and recommend new manipulated variables
    to achieve the target control variables. Return JSON and set every variable
    that does not need to change right now to null.
    """
    response_id: Optional[str]
    try:
        decision = await get_client().responses.parse(
            model=os.getenv("DECISION_MODEL", "muse-wonder-1.0"),
            reasoning={"effort": os.getenv("DECISION_REASONING_EFFORT", "minimal")},
            input=[{"type": "message", "role": "user", "content": prompt}],
            text_format=ManipulatedVariables,
        )
        if decision.output_parsed is None:
            raise RuntimeError("HVAC decision returned no parsed response.")
        output = decision.output_parsed.model_dump()
        response_id = decision.id
    except BadRequestError as exc:
        print(
            f"[Warning] Clip filtered or rejected ({exc.status_code}); "
            "holding previous HVAC state."
        )
        output = ManipulatedVariables().model_dump()
        response_id = None

    return output, telemetry, response_id


# --------------------------------------------------------------------------
# SpotCool Cortex path
# --------------------------------------------------------------------------

def _parameters() -> control.Parameters:
    params = control.Parameters(
        zone=cortex_config.parameters_from_env("ZONE_PARAMETERS", cortex_config.ZONE_DEFAULTS),
        hvac=cortex_config.parameters_from_env("HVAC_PARAMETERS", cortex_config.HVAC_DEFAULTS),
        camera=cortex_config.parameters_from_env("CAMERA_PARAMETERS", cortex_config.CAMERA_DEFAULTS),
        control=cortex_config.parameters_from_env("CONTROL_PARAMETERS", cortex_config.CONTROL_DEFAULTS),
        baseline=cortex_config.json_object_from_env(
            "BASELINE_MANIPULATED_VARIABLES", cortex_config.BASELINE_DEFAULTS
        ),
    )
    rate_limits = params.control["rate_limits"]
    for name, band in params.control["deadbands"].items():
        if name in rate_limits and float(band) >= float(rate_limits[name]):
            raise RuntimeError(
                f"CONTROL_PARAMETERS deadband for {name} must be smaller than its rate limit."
            )
    return params


def _remaining(deadline: float) -> float:
    return deadline - time.monotonic()


def _budget(deadline: float, stage_cap: float, reserve: float) -> float:
    return max(1.0, min(stage_cap, _remaining(deadline) - reserve))


def _effective_config(params: control.Parameters) -> dict[str, Any]:
    return {
        "models": {
            "sam": os.getenv("SAM_MODEL", "sam-3.1"),
            "scene": os.getenv("SCENE_MODEL", "muse-spark-1.3"),
            "supervisor": os.getenv("DECISION_MODEL", "muse-wonder-1.0"),
            "voice": os.getenv("VOICE_MODEL", "muse-voice-transcribe-1.0"),
            "image": os.getenv("IMAGE_MODEL", "muse-image-1.0"),
        },
        "scene_reasoning_effort": os.getenv("SCENE_REASONING_EFFORT", "low"),
        "supervisor_reasoning_effort": os.getenv("SUPERVISOR_REASONING_EFFORT", "medium"),
        "decision_max_tool_rounds": os.getenv("DECISION_MAX_TOOL_ROUNDS", "3"),
        "keyframes_per_clip": os.getenv("KEYFRAMES_PER_CLIP", "4"),
        "analysis_sample_interval_seconds": os.getenv("ANALYSIS_SAMPLE_INTERVAL_SECONDS", "0.5"),
        "sam_concepts": {
            "person": os.getenv("SAM_PERSON_CONCEPT", "humans"),
            "door": os.getenv("SAM_DOOR_CONCEPT", "open door"),
            "context": cortex_config.json_list_from_env(
                "SAM_CONTEXT_CONCEPTS", ["open window", "computer monitor", "ceiling fan"]
            ),
        },
        "privacy_blur_faces": cortex_config.bool_from_env("PRIVACY_BLUR_FACES", True),
        "zone": params.zone,
        "hvac": params.hvac,
        "camera": params.camera,
        "control_weights": params.control["weights"],
        "horizon_minutes": params.control["horizon_minutes"],
    }


def _aim_pixel(final_mv: dict[str, Any], params: control.Parameters, width: int, height: int) -> Optional[tuple[int, int]]:
    camera = params.camera
    tilt = float(final_mv.get("louver_vertical_deg") or 0)
    pan = math.radians(float(final_mv.get("louver_horizontal_deg") or 90) - 90.0)
    drop = float(camera["ac_height_m"]) - float(camera["target_height_m"])
    reach = drop / math.tan(math.radians(tilt)) if tilt > 2 else 8.0
    reach = min(reach, 15.0)
    return perception.project_floor_point(
        float(camera["ac_lateral_m"]) + reach * math.sin(pan),
        float(camera["ac_forward_m"]) + reach * math.cos(pan),
        float(camera["target_height_m"]),
        width,
        height,
        camera,
    )


def _write_artifact(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path.resolve())


async def _run_cortex(
    clip: perception.ClipAnalysis,
    current_control_variables: dict[str, Any],
    current_manipulated_variables: dict[str, Any],
    turn_history: Sequence[dict[str, Any]],
    *,
    video_path: Optional[str],
    clip_start_seconds: Optional[float],
    clip_end_seconds: Optional[float],
    artifact_stem: Optional[Path],
    started: float,
) -> tuple[dict[str, Any], dict[str, Any], Optional[str]]:
    deadline = started + _positive_float_from_env("PIPELINE_DEADLINE_SECONDS", 220.0)
    client = get_client()
    params = _parameters()
    ledger: list[dict[str, Any]] = []
    timings: dict[str, float] = {"video_analysis_s": round(clip.decode_seconds, 2)}
    blur = cortex_config.bool_from_env("PRIVACY_BLUR_FACES", True)

    # Stage 2a: SAM 3.1 segmentation and clip audio transcription, in parallel.
    stage = time.monotonic()
    sam_budget = _budget(deadline, 45.0, 130.0)
    if video_path is not None and clip_start_seconds is not None and clip_end_seconds is not None:
        voice_job = media.transcribe_clip(video_path, clip_start_seconds, clip_end_seconds, ledger, sam_budget)
    else:
        voice_job = asyncio.sleep(0, result={"status": "skipped", "reason": "no source video for this turn"})
    sam_result, voice_result = await asyncio.gather(
        asyncio.wait_for(perception.detect_scene(client, clip, ledger), sam_budget),
        asyncio.wait_for(voice_job, sam_budget),
        return_exceptions=True,
    )
    sam = sam_result if isinstance(sam_result, perception.SamScene) else None
    if sam is None:
        LOGGER.warning("SAM 3.1 stage failed: %r", sam_result)
    voice = voice_result if isinstance(voice_result, dict) else {"status": "error", "reason": repr(voice_result)[:300]}
    timings["sam_and_voice_s"] = round(time.monotonic() - stage, 2)

    # Stage 2b: Muse Spark set-of-marks assessment (legacy semantics as fallback).
    stage = time.monotonic()
    people = perception.latest_people(sam)
    private_clip = perception.privacy_keyframes(clip, sam) if blur else clip
    latest_private = private_clip.keyframes[-1].frame
    storyboard_b64 = perception.encode_jpeg_b64(perception.storyboard(private_clip))
    assessment = None
    legacy = None
    try:
        assessment = await asyncio.wait_for(
            perception.assess_scene(
                client,
                perception.encode_jpeg_b64(perception.draw_marks(latest_private, people)),
                storyboard_b64,
                perception.sam_summary(sam, people),
                media.transcript_lines(voice, clip.start_s),
                ledger,
            ),
            _budget(deadline, 60.0, 100.0),
        )
    except Exception as exc:
        LOGGER.warning("Set-of-marks scene assessment failed (%s); trying fallback semantics.", exc)
        try:
            legacy = await asyncio.wait_for(
                perception.legacy_semantics(client, [storyboard_b64], ledger),
                _budget(deadline, 30.0, 95.0),
            )
        except Exception as fallback_exc:
            LOGGER.warning("Fallback scene semantics failed: %s", fallback_exc)
    fused = perception.fuse(clip, sam, people, assessment, legacy, params)
    timings["scene_assessment_s"] = round(time.monotonic() - stage, 2)

    # Stages 3-5: state estimation, physics and plan search (CPU, off the event loop).
    stage = time.monotonic()
    ctx = control.build_context(
        params,
        current_control_variables,
        _target_control_variables(),
        current_manipulated_variables,
        turn_history,
        fused.facts,
        clip_start_seconds,
        clip_end_seconds,
    )
    search = await asyncio.to_thread(control.search_plans, ctx)
    plans: dict[str, control.Candidate] = search["plans"]
    comfort_now = physics.evaluate_comfort(ctx.state.temperature_c, ctx.state.rh_pct, ctx.previous_mv, ctx.env)
    for occupant, comfort in zip(fused.occupants, comfort_now["per_occupant"]):
        occupant.update(comfort)
    anomalies = control.detect_anomalies(ctx, fused.sam_count, fused.spark_count)
    timings["physics_and_planning_s"] = round(time.monotonic() - stage, 2)

    # Stage 6: Muse Wonder supervisor, with the Muse Image render alongside.
    stage = time.monotonic()
    card = agent.state_card(ctx, plans, comfort_now, fused.scene, fused.occupants, anomalies, voice)
    render_wanted = artifact_stem is not None and cortex_config.bool_from_env("PRIVACY_RENDER_ENABLED", True)
    render_job = (
        asyncio.wait_for(media.render_privacy_snapshot(client, latest_private, ledger), _budget(deadline, 90.0, 8.0))
        if render_wanted else asyncio.sleep(0, result=None)
    )
    outcome_result, render_result = await asyncio.gather(
        agent.supervise(client, ctx, plans, card, ledger, deadline - 8.0),
        render_job,
        return_exceptions=True,
    )
    if isinstance(outcome_result, agent.SupervisorOutcome):
        outcome = outcome_result
    else:
        LOGGER.warning("Supervisor failed: %r", outcome_result)
        outcome = agent.SupervisorOutcome(
            proposal=dict(plans["recommended"].mv),
            decision=None,
            response_id=None,
            trace={"commit": f"failed: {outcome_result!r}"[:300]},
        )
    timings["supervisor_s"] = round(time.monotonic() - stage, 2)

    # Stage 7: safety governor, outcome prediction and memory.
    final_mv, adjustments = control.govern(ctx, outcome.proposal)
    delta = control.decision_delta(final_mv, ctx.previous_mv)
    final_prediction = physics.simulate(ctx.env, ctx.state, final_mv, ctx.horizon_s)
    recommended = plans["recommended"].prediction
    energy = control.energy_outcome(ctx, final_mv)
    agent_state = control.next_agent_state(ctx, final_mv, energy, control.predict_after(ctx, final_mv, ctx.dt_s))
    end_point = final_prediction.points[-1]
    supervisor_anomalies: list[dict[str, Any]] = []
    reported_kinds = {a["kind"] for a in anomalies}
    for anomaly in outcome.decision.anomalies if outcome.decision else []:
        if anomaly.kind not in reported_kinds:
            reported_kinds.add(anomaly.kind)
            supervisor_anomalies.append({**anomaly.model_dump(), "source": "muse_wonder"})

    artifacts: dict[str, Any] = {}
    if artifact_stem is not None:
        if cortex_config.bool_from_env("SAVE_DECISION_OVERLAY", True):
            height, width = latest_private.shape[:2]
            banner = [
                f"SpotCool Cortex | occupants {fused.facts.occupancy} ({len(people)} located) | "
                f"PMV now {comfort_now['pmv_mean']:+.2f}  PPD {comfort_now['ppd_mean']:.0f}%",
                f"Setpoint {final_mv['temperature_c']:.1f}C (target {ctx.targets['temperature_c']:.0f}C) | fan "
                f"{final_mv['fan_speed_mode']} {final_mv['airflow_rate_m3h'] or 0:.0f} m3/h | "
                f"{final_mv['compressor_frequency_hz'] or 0:.0f} Hz",
                f"Outdoor air {ctx.ventilation['outdoor_air_m3h']:.0f} m3/h for CO2 {ctx.state.co2_ppm:.0f}->"
                f"{ctx.targets['co2_ppm']:.0f} ppm | {energy['plan_kw']:.2f} kW vs {energy['baseline_kw']:.2f} kW "
                f"baseline ({energy['savings_pct']:+.0f}% saved)",
                f"Muse Wonder: {outcome.decision.base_plan if outcome.decision else 'physics fallback'} | "
                f"governor adjustments: {len(adjustments)}",
            ]
            overlay = media.draw_overlay(
                latest_private, fused.occupants, _aim_pixel(final_mv, params, width, height),
                bool(final_mv.get("swing_mode")), banner,
            )
            artifacts["decision_overlay_path"] = _write_artifact(artifact_stem.with_name(artifact_stem.name + "_overlay.jpg"), overlay)
        if isinstance(render_result, (bytes, bytearray)):
            artifacts["privacy_render_path"] = _write_artifact(artifact_stem.with_name(artifact_stem.name + "_render.webp"), bytes(render_result))
        elif isinstance(render_result, BaseException):
            artifacts["privacy_render_error"] = f"{type(render_result).__name__}: {render_result}"[:300]

    telemetry: dict[str, Any] = {
        "persons": fused.persons_legacy,
        "doors": fused.doors_legacy,
        "semantics": perception.semantics_from_facts(fused.facts, comfort_now["pmv_mean"]),
        "pipeline_mode": "cortex",
        "video": clip.summary(),
        "occupants": fused.occupants,
        "scene": fused.scene,
        "voice": voice,
        "comfort": {
            "standard": "ISO 7730 PMV/PPD with ASHRAE 55 relative air speed and dynamic clothing insulation",
            "now": {
                "pmv_mean": round(comfort_now["pmv_mean"], 2),
                "ppd_mean": round(comfort_now["ppd_mean"], 1),
                "pmv_max": round(comfort_now["pmv_max"], 2) if fused.facts.occupants else None,
                "draft_rate_pct": round(comfort_now["draft_soft_pct"], 1),
            },
            "setpoint": plans["recommended"].comfort,
            "predicted_end_of_horizon": {"pmv": end_point["pmv"], "ppd": end_point["ppd"]},
        },
        "air_quality": {
            "now": {k: round(ctx.current[k], 1) for k in ("co2_ppm", "tvoc_ppb", "pm25_ug_m3", "pm10_ug_m3", "humidity_pct")},
            "targets": ctx.targets,
            "ventilation": ctx.ventilation,
            "predicted_end_of_horizon": {k: end_point[k] for k in ("co2_ppm", "tvoc_ppb", "pm25_ug_m3", "pm10_ug_m3", "rh_pct")},
        },
        "energy": {**energy, "cumulative": agent_state["cumulative"]},
        "plan": {
            "horizon_minutes": round(ctx.horizon_s / 60.0, 1),
            "candidates_evaluated": search["candidates_evaluated"],
            "plans": {label: control.summarize_candidate(c, label) for label, c in plans.items()},
            "final_prediction": final_prediction.points,
        },
        "supervisor": {
            **outcome.trace,
            "base_plan": outcome.decision.base_plan if outcome.decision else None,
            "rationale": outcome.decision.rationale if outcome.decision else None,
            "confidence": outcome.decision.confidence if outcome.decision else None,
        },
        "governor": {
            "adjustments": adjustments,
            "soft_checks": {
                "mean_ppd_vs_recommended": round(final_prediction.mean_ppd - recommended.mean_ppd, 1),
                "energy_kwh_vs_recommended": round(final_prediction.energy_kwh - recommended.energy_kwh, 3),
                "co2_end_of_horizon_ppm": end_point["co2_ppm"],
                "co2_within_target": end_point["co2_ppm"] <= ctx.targets["co2_ppm"] * 1.1,
                "mean_draft_rate_pct": round(final_prediction.mean_draft_pct, 1),
            },
        },
        "anomalies": anomalies + supervisor_anomalies,
        "models": ledger,
        "timings": {**timings, "total_s": round(time.monotonic() - started, 2)},
        "artifacts": artifacts,
        "effective_config": _effective_config(params),
        "agent_state": agent_state,
    }
    return delta, telemetry, outcome.response_id


async def process_frames(
    b64_frames: Sequence[str],
    current_control_variables: dict[str, Any],
    current_manipulated_variables: dict[str, Any],
    turn_history: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], Optional[str]]:
    if not b64_frames:
        raise ValueError("At least one sampled video frame is required.")
    frames = list(b64_frames)
    history = list(turn_history)
    if _pipeline_mode() == "legacy":
        return await _legacy_process_frames(frames, current_control_variables, current_manipulated_variables, history)
    started = time.monotonic()
    try:
        clip = perception.clip_from_b64_frames(frames)
        return await _run_cortex(
            clip,
            current_control_variables,
            current_manipulated_variables,
            history,
            video_path=None,
            clip_start_seconds=None,
            clip_end_seconds=None,
            artifact_stem=None,
            started=started,
        )
    except Exception as exc:
        if not cortex_config.bool_from_env("CORTEX_FALLBACK_TO_LEGACY", True):
            raise
        LOGGER.exception("Cortex pipeline failed; falling back to the legacy MVP path.")
        delta, telemetry, response_id = await _legacy_process_frames(
            frames, current_control_variables, current_manipulated_variables, history
        )
        telemetry.update(pipeline_mode="legacy_fallback", cortex_error=f"{type(exc).__name__}: {exc}"[:500])
        return delta, telemetry, response_id


def _load_previous_state(
    previous_turn_state_file_path: Optional[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if previous_turn_state_file_path is None:
        initial_variables = _json_object_from_env(
            "INITIAL_MANIPULATED_VARIABLES",
            {
                "temperature_c": 24.0,
                "fan_speed_mode": "AUTO",
                "fan_mode": "AUTO",
                "airflow_rate_m3h": 500.0,
                "louver_vertical_deg": 60,
                "louver_horizontal_deg": 90,
                "swing_mode": False,
                "compressor_mode": "COOL",
                "compressor_frequency_hz": 40.0,
                "valve_open_pct": 50,
                "eco_mode": False,
                "sleep_mode": False,
                "turbo_mode": False,
                "humidity_target_pct": 50.0,
                "dehumidification_mode": False,
                "fresh_air_damper_pct": 0,
                "ventilation_mode": "AUTO",
                "filter_cleaning_alert": False,
                "defrost_mode": False,
            },
        )
        return initial_variables, []

    state_path = Path(previous_turn_state_file_path).expanduser()
    if not state_path.is_file():
        raise FileNotFoundError(
            f"Previous turn state file does not exist: {state_path}"
        )
    with state_path.open("r", encoding="utf-8") as state_file:
        previous_state = json.load(state_file)
    if not isinstance(previous_state, dict):
        raise ValueError("Previous turn state file must contain a JSON object.")

    manipulated_variables = previous_state.get("manipulated_variables")
    turn_history = previous_state.get("turn_history", [])
    if not isinstance(manipulated_variables, dict):
        raise ValueError(
            "Previous turn state file has no manipulated_variables object."
        )
    if not isinstance(turn_history, list) or not all(
        isinstance(turn, dict) for turn in turn_history
    ):
        raise ValueError("Previous turn state file has invalid turn_history.")
    return manipulated_variables, turn_history


def _turn_details_directory() -> Path:
    configured_output_dir = os.getenv("TURN_DETAILS_DIRECTORY")
    if configured_output_dir:
        output_dir = Path(configured_output_dir).expanduser()
        if not output_dir.is_absolute():
            output_dir = Path(__file__).resolve().parent / output_dir
    else:
        output_dir = Path(__file__).resolve().parent / "turn_details"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


async def process_window(
    video_path: str,
    clip_start_seconds: float,
    clip_end_seconds: float,
    previous_turn_state_file_path: Optional[str],
    current_control_variables: dict[str, Any],
    job_id: str,
) -> tuple[Optional[str], str]:
    started = time.monotonic()
    current_manipulated_variables, previous_history = _load_previous_state(
        previous_turn_state_file_path
    )
    max_history = _positive_int_from_env("TURN_HISTORY_LENGTH", 3)
    previous_history = previous_history[-max_history:]
    output_dir = _turn_details_directory()
    turn_id = uuid4().hex

    async def run_legacy() -> tuple[int, dict[str, Any], dict[str, Any], Optional[str]]:
        frames = _read_clip_frames(video_path, clip_start_seconds, clip_end_seconds)
        delta, telemetry, response_id = await _legacy_process_frames(
            frames, current_control_variables, current_manipulated_variables, previous_history
        )
        return len(frames), delta, telemetry, response_id

    if _pipeline_mode() == "legacy":
        sampled_frame_count, decision_delta, telemetry, response_id = await run_legacy()
    else:
        try:
            clip = await asyncio.to_thread(
                perception.analyze_clip, video_path, clip_start_seconds, clip_end_seconds
            )
            sampled_frame_count = clip.sample_count
            decision_delta, telemetry, response_id = await _run_cortex(
                clip,
                current_control_variables,
                current_manipulated_variables,
                previous_history,
                video_path=video_path,
                clip_start_seconds=clip_start_seconds,
                clip_end_seconds=clip_end_seconds,
                artifact_stem=output_dir / "artifacts" / f"turn_{turn_id}",
                started=started,
            )
        except Exception as exc:
            if not cortex_config.bool_from_env("CORTEX_FALLBACK_TO_LEGACY", True):
                raise
            LOGGER.exception("Cortex pipeline failed for job %s; falling back to the legacy MVP path.", job_id)
            sampled_frame_count, decision_delta, telemetry, response_id = await run_legacy()
            telemetry.update(pipeline_mode="legacy_fallback", cortex_error=f"{type(exc).__name__}: {exc}"[:500])

    updated_manipulated_variables = current_manipulated_variables.copy()
    updated_manipulated_variables.update(
        {
            key: value
            for key, value in decision_delta.items()
            if value is not None
        }
    )
    turn_history = previous_history + [{
        "clip_start_seconds": clip_start_seconds,
        "clip_end_seconds": clip_end_seconds,
        "telemetry": telemetry,
        "decision_delta": decision_delta,
    }]
    turn_history = turn_history[-max_history:]

    saved_file = output_dir / f"turn_{turn_id}.json"
    details = {
        "job_id": job_id,
        "video_path": video_path,
        "clip_start_seconds": clip_start_seconds,
        "clip_end_seconds": clip_end_seconds,
        "sampled_frame_count": sampled_frame_count,
        "previous_turn_state_file_path": previous_turn_state_file_path,
        "current_control_variables": current_control_variables,
        "target_control_variables": _target_control_variables(),
        "previous_manipulated_variables": current_manipulated_variables,
        "decision_delta": decision_delta,
        "manipulated_variables": updated_manipulated_variables,
        "telemetry": telemetry,
        "response_id": response_id,
        "turn_history": turn_history,
    }
    with saved_file.open("w", encoding="utf-8") as details_file:
        json.dump(details, details_file, indent=2, ensure_ascii=False)
        details_file.write("\n")

    return response_id, str(saved_file.resolve())
