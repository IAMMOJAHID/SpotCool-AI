"""Muse Wonder supervisor: explores what-ifs on the zone digital twin with a
function tool, then commits a structured decision relative to a physics plan."""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from openai import APIConnectionError, AsyncOpenAI, InternalServerError
from pydantic import ValidationError

from . import config, control, physics
from .control import Candidate, TurnContext
from .schemas import MANIPULATED_VARIABLE_NAMES, ManipulatedVariables, SupervisorDecision

LOGGER = logging.getLogger("spotcool.agent")

SUPERVISOR_INSTRUCTIONS = """You are the supervisory agent of SpotCool, an autonomous HVAC controller for one zone of a commercial building.
Deterministic models already did the arithmetic: ISO 7730 PMV/PPD comfort, CO2/TVOC/PM dilution (ASHRAE 62.1), a grey-box thermal
model and an energy model. They produced three ranked plans (recommended, comfort_first, energy_first) with 5/10/15-minute predictions.
Your job is judgement the physics cannot make from numbers alone: who matters (stationary staff vs passers-by), what the scene and audio
imply (a meeting ending, people complaining of heat, a door propped open), whether sensors look stale or contradictory, and the
trade-off between comfort, air quality and energy.
Rules:
- Pick one base plan. Override a variable only with a concrete reason; leave every other override null.
- Use the simulate_plan tool to check any override before you commit; it returns predictions and what the safety governor would change.
- Never invent sensor readings. A deterministic safety governor enforces bounds, interlocks and rate limits on whatever you choose.
- Report anomalies with specific evidence from the state card."""

COMMIT_INSTRUCTIONS = """Commit your final decision now as a SupervisorDecision.
base_plan: one of recommended / comfort_first / energy_first. overrides: only the variables you change relative to that plan (null otherwise).
rationale: at most 4 sentences that cite the numbers you relied on. anomalies: only ones supported by evidence. confidence: 0 to 1."""


def _tool_schema() -> dict[str, Any]:
    schema = ManipulatedVariables.model_json_schema()
    properties = {}
    for name, prop in schema["properties"].items():
        # Drop the Optional null branch: omitted fields already mean "keep the plan value".
        branch = next((b for b in prop.get("anyOf", [prop]) if b.get("type") != "null"), prop)
        properties[name] = {
            **{key: value for key, value in branch.items() if key not in ("title", "default")},
            "description": "Omit to keep the recommended plan value.",
        }
    return {
        "type": "function",
        "name": "simulate_plan",
        "description": (
            "Simulate the zone digital twin for the next 15 minutes with candidate HVAC manipulated variables. "
            "Fields left null are taken from the recommended physics plan. Returns predicted temperature, RH, CO2, "
            "TVOC, PM, occupant PMV/PPD, draft risk, energy, holding power vs the uniform-cooling baseline, and "
            "the safety governor's adjustments."
        ),
        "parameters": {"type": "object", "properties": properties, "additionalProperties": False},
    }


@dataclass
class SupervisorOutcome:
    proposal: dict[str, Any]
    decision: Optional[SupervisorDecision]
    response_id: Optional[str]
    trace: dict[str, Any] = field(default_factory=dict)


def _compact_mv(mv: dict[str, Any]) -> dict[str, Any]:
    return {name: mv.get(name) for name in MANIPULATED_VARIABLE_NAMES}


def clean_arguments(arguments: Any) -> dict[str, Any]:
    """Keep only real values; models sometimes send "null" strings for untouched fields."""
    if not isinstance(arguments, dict):
        return {}
    return {
        key: value
        for key, value in arguments.items()
        if key in MANIPULATED_VARIABLE_NAMES
        and value is not None
        and not (isinstance(value, str) and value.strip().lower() in ("", "null", "none"))
    }


def simulate_proposal(ctx: TurnContext, base_mv: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    proposal = {**base_mv, **clean_arguments(arguments)}
    try:
        validated = ManipulatedVariables(**proposal).model_dump()
    except ValidationError as exc:
        return {"error": f"invalid manipulated variables: {exc.errors()[:3]}"}
    governed, adjustments = control.govern(ctx, validated)
    prediction = physics.simulate(ctx.env, ctx.state, governed, ctx.horizon_s)
    energy = control.energy_outcome(ctx, governed)
    return {
        "governed_manipulated_variables": _compact_mv(governed),
        "governor_adjustments": adjustments,
        "predicted": prediction.points,
        "energy_kwh_horizon": round(prediction.energy_kwh, 3),
        "mean_ppd": round(prediction.mean_ppd, 1),
        "mean_draft_pct": round(prediction.mean_draft_pct, 1),
        "holding_power_kw": energy["plan_kw"],
        "baseline_holding_power_kw": energy["baseline_kw"],
        "savings_pct": energy["savings_pct"],
    }


def state_card(
    ctx: TurnContext,
    plans: dict[str, Candidate],
    comfort_now: dict[str, Any],
    perception_scene: dict[str, Any],
    occupants: list[dict[str, Any]],
    anomalies: list[dict[str, Any]],
    voice: dict[str, Any],
) -> dict[str, Any]:
    moving = sum(1 for o in occupants if o.get("moving"))
    return {
        "zone_now": {
            **{k: round(v, 2) for k, v in ctx.current.items()},
            "dew_point_c": round(physics.dew_point_c(ctx.state.temperature_c, ctx.state.rh_pct), 1),
        },
        "targets": ctx.targets,
        "outdoor": {
            "temperature_c": ctx.env.outdoor_temperature_c,
            "co2_ppm": ctx.env.outdoor_co2_ppm,
            "pm25_ug_m3": ctx.env.outdoor_pm25,
        },
        "occupancy": {
            "count": ctx.facts.occupancy,
            "located": len(occupants),
            "located_stationary": len(occupants) - moving,
            "located_moving": moving,
            "located_dwelling_most_of_clip": sum(1 for o in occupants if o.get("dwell_fraction", 0) >= 0.75),
            "space_type": ctx.space_type,
            "trend": perception_scene.get("occupancy_trend"),
            "scene_summary": perception_scene.get("summary"),
            "openings": perception_scene.get("openings"),
            "unoccupied_setback_active": ctx.setback,
        },
        "occupants": [
            {k: o.get(k) for k in ("id", "distance_m", "dwell_fraction", "moving", "clothing_ensemble", "clo", "activity", "met", "in_direct_sunlight", "discomfort_cue", "pmv")}
            for o in occupants[:20]
        ],
        "comfort_now_with_previous_settings": {
            "pmv_mean": round(comfort_now["pmv_mean"], 2),
            "ppd_mean": round(comfort_now["ppd_mean"], 1),
            "pmv_max": round(comfort_now["pmv_max"], 2) if occupants else None,
        },
        "ventilation_requirement": ctx.ventilation,
        "louver_aim": ctx.aim,
        "voice": {"status": voice.get("status"), "turns": voice.get("turns", [])[:10], "comfort_bias_c": round(ctx.voice_bias_c, 2)},
        "previous_manipulated_variables": _compact_mv(ctx.previous_mv),
        "plans": {
            label: {
                "candidate": c.name,
                "manipulated_variables": _compact_mv(c.mv),
                "predicted": c.prediction.points,
                "energy_kwh_horizon": round(c.prediction.energy_kwh, 3),
                "mean_ppd": round(c.prediction.mean_ppd, 1),
                "comfort_setpoint": c.comfort,
                "feedforward": c.feedforward,
            }
            for label, c in plans.items()
        },
        "memory": {
            "turn_index": ctx.previous_agent_state.get("turn_index", 0),
            "stale_turns": ctx.stale_turns,
            "empty_turns": ctx.empty_turns,
            "cumulative": ctx.previous_agent_state.get("cumulative"),
        },
        "anomalies_detected": anomalies,
        "hard_limits": {
            "setpoint_c": [ctx.params.hvac["setpoint_min_c"], ctx.params.hvac["setpoint_max_c"]],
            "frequency_hz": [ctx.params.hvac["min_frequency_hz"], ctx.params.hvac["max_frequency_hz"]],
            "rate_limits_per_turn": ctx.params.control["rate_limits"],
            "interlocks": [
                "fan must run while the compressor runs",
                "OFF/FAN_ONLY forces 0 Hz and a closed valve",
                "turbo, eco and sleep are mutually exclusive",
                "compressor minimum off time",
            ],
        },
    }


def _remaining(deadline: float) -> float:
    return deadline - time.monotonic()


async def supervise(
    client: AsyncOpenAI,
    ctx: TurnContext,
    plans: dict[str, Candidate],
    card: dict[str, Any],
    ledger: list[dict[str, Any]],
    deadline: float,
) -> SupervisorOutcome:
    model = os.getenv("DECISION_MODEL", "muse-wonder-1.0")
    effort = os.getenv("SUPERVISOR_REASONING_EFFORT", "medium")
    max_rounds = config.non_negative_int_from_env("DECISION_MAX_TOOL_ROUNDS", 3)
    operator = os.getenv("CONTROL_SYSTEM_INSTRUCTIONS")
    instructions = f"{operator}\n\n{SUPERVISOR_INSTRUCTIONS}" if operator else SUPERVISOR_INSTRUCTIONS
    recommended_mv = plans["recommended"].mv
    tools = [_tool_schema()]
    card_text = json.dumps(card, separators=(",", ":"), default=str)
    trace: dict[str, Any] = {"model": model, "reasoning_effort": effort, "tool_calls": [], "explore": "skipped"}
    whatifs: list[dict[str, Any]] = []
    explore_notes = ""

    if max_rounds > 0:
        started = time.monotonic()
        try:
            response = await asyncio.wait_for(
                client.responses.create(
                    model=model,
                    instructions=instructions,
                    tools=tools,
                    reasoning={"effort": effort},
                    input=[{"type": "message", "role": "user", "content": f"State card:\n{card_text}\n\nInvestigate with simulate_plan as needed, then summarise your conclusion."}],
                ),
                timeout=max(1.0, _remaining(deadline) - 30.0),
            )
            rounds = 0
            while True:
                calls = [item for item in response.output if getattr(item, "type", "") == "function_call"]
                if not calls or rounds >= max_rounds:
                    break
                outputs = []
                for call in calls:
                    try:
                        arguments = clean_arguments(json.loads(call.arguments or "{}"))
                    except json.JSONDecodeError:
                        arguments = {}
                    result = simulate_proposal(ctx, recommended_mv, arguments)
                    whatifs.append({"arguments": arguments, "result": result})
                    trace["tool_calls"].append({
                        "round": rounds + 1,
                        "arguments": arguments,
                        "mean_ppd": result.get("mean_ppd"),
                        "holding_power_kw": result.get("holding_power_kw"),
                        "savings_pct": result.get("savings_pct"),
                        "error": result.get("error"),
                    })
                    outputs.append({"type": "function_call_output", "call_id": call.call_id, "output": json.dumps(result, default=str)})
                rounds += 1
                for attempt in range(2):
                    try:
                        response = await asyncio.wait_for(
                            client.responses.create(
                                model=model,
                                instructions=instructions,
                                tools=tools,
                                reasoning={"effort": effort},
                                previous_response_id=response.id,
                                input=outputs,
                            ),
                            timeout=max(1.0, _remaining(deadline) - 30.0),
                        )
                        break
                    except (InternalServerError, APIConnectionError):
                        # Conversation-state storage is occasionally unavailable; retry once.
                        if attempt == 1 or _remaining(deadline) < 45.0:
                            raise
                        await asyncio.sleep(1.0)
            explore_notes = (response.output_text or "")[:2000]
            trace["explore"] = f"completed after {rounds} tool round(s)"
            config.record_model_call(ledger, "supervisor", model, f"explore what-ifs ({len(whatifs)} simulations)", started, ok=True)
        except Exception as exc:
            trace["explore"] = f"stopped: {type(exc).__name__}: {str(exc)[:200]}"
            config.record_model_call(ledger, "supervisor", model, "explore what-ifs", started, ok=False, detail=str(exc)[:200])

    commit_input = f"State card:\n{card_text}"
    if whatifs:
        commit_input += "\n\nWhat-if simulations you ran:\n" + json.dumps(
            [{"arguments": w["arguments"], "mean_ppd": w["result"].get("mean_ppd"), "holding_power_kw": w["result"].get("holding_power_kw"),
              "savings_pct": w["result"].get("savings_pct"), "governor_adjustments": w["result"].get("governor_adjustments"),
              "predicted_15min": (w["result"].get("predicted") or [None])[-1]} for w in whatifs],
            default=str,
        )
    if explore_notes:
        commit_input += f"\n\nYour investigation notes:\n{explore_notes}"
    commit_input += f"\n\n{COMMIT_INSTRUCTIONS}"

    started = time.monotonic()
    try:
        response = await asyncio.wait_for(
            client.responses.parse(
                model=model,
                instructions=instructions,
                reasoning={"effort": effort},
                input=[{"type": "message", "role": "user", "content": commit_input}],
                text_format=SupervisorDecision,
            ),
            timeout=max(1.0, _remaining(deadline) - 5.0),
        )
        decision = response.output_parsed
        if decision is None:
            raise RuntimeError("Supervisor returned no parsed decision.")
    except Exception as exc:
        config.record_model_call(ledger, "supervisor", model, "commit decision", started, ok=False, detail=str(exc)[:200])
        trace["commit"] = f"failed: {type(exc).__name__}: {str(exc)[:200]}; using the recommended physics plan"
        return SupervisorOutcome(proposal=dict(recommended_mv), decision=None, response_id=None, trace=trace)
    config.record_model_call(ledger, "supervisor", model, "commit decision", started, ok=True)
    base = plans[decision.base_plan].mv
    overrides = {k: v for k, v in decision.overrides.model_dump().items() if v is not None and v != base.get(k)}
    trace["commit"] = "ok"
    trace["overrides_vs_base_plan"] = overrides
    return SupervisorOutcome(proposal={**base, **overrides}, decision=decision, response_id=response.id, trace=trace)
