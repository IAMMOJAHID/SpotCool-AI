# SpotCool AI Blueprint

> **Meta Global AI Developer Hackathon Project**  
> Eliminating up to 60% of HVAC energy waste through real-time, human-centric 3D spot cooling.

---

## System Overview

Traditional air conditioning wastes up to 60% of its energy by cooling unoccupied space or ignoring individual thermal comfort differences. **SpotCool AI** replaces passive, uniform room cooling with an autonomous multi-agent edge system powered by Meta's AI ecosystem (SAM 3, Llama Vision, Llama Orchestration).

Instead of maintaining a constant cooling load across entire cubic volumes, SpotCool AI tracks occupants in 3D space, determines clothing insulation and comfort signals, and dynamically actuates smart louvers and fan speeds directly toward occupied zones.

```
[Webcam Feed] ──► Visual & Spatial Agent (SAM 3 + Depth + VLM) ──┐
                                                                 ▼
[Mic Stream]  ──► Acoustic Agent (Silero VAD + Intent Parsing) ─► [Canonical JSON State Bus]
                                                                 │
          ┌──────────────────────────────────────────────────────┘
          ▼
[Thermodynamic Policy Orchestrator (Meta Llama)] ──► "Acts as Human with Remote"
          │
          ├──► [AC Digital Twin (Appliance Emulator / Stepper Slew & Telemetry)]
          └──► [Judge Telemetry Dashboard (Real-to-Sim Mirror & Wattage Comparison)]
```

### Core Agents

1. **Visual & Spatial Agent (SAM 3 + Monocular Depth):**
   * Segments human occupants from camera feeds.
   * Computes median depth and back-projects 2D pixels into $(X, Y, Z)$ real-world coordinates:
     $$X = \frac{(u - c_x) \cdot Z}{f_x}, \quad Y = \frac{(v - c_y) \cdot Z}{f_y}$$
   * Calculates dynamic louver angles: $\theta_{\text{pan}} = \arctan2(X, Z)$, $\theta_{\text{tilt}} = \arctan2(Y, Z)$.
   * Evaluates clothing insulation (`Clo` values) and flags open door/window energy leaks via Llama Vision.

2. **Acoustic & Implicit Feedback Agent (VAD + Audio Classification):**
   * Continuously monitors microphone input using Silero Voice Activity Detection.
   * Transcribes implicit thermal remarks (e.g., *"It's freezing in here"*, *"Turn it up"*) to dynamic temperature offsets and fan overrides.
   * Updates per-user in-context comfort memory.

3. **Thermodynamic Policy Orchestrator (Meta Llama):**
   * Acts as the autonomous decision-maker ("the human with the smart remote").
   * Integrates spatial coordinates, clothing insulation, and ambient temperature into ASHRAE Standard 55 / Fanger PMV thermal comfort models.
   * Dispatches structured control payloads (`POST /ac-twin/command`) to actuate blade angles, fan speed tiers, and eco modes.

4. **AC Digital Twin & Telemetry:**
   * A stateful software emulator representing the physical split-AC unit.
   * Simulates mechanical stepper motor slew rates (15°/sec), fan RPM spin-up curves, and compressor electrical load.
   * Generates live energy telemetry comparing baseline uniform cooling against SpotCool AI in real time.

### Tech Stack

* **Computer Vision:** Meta Segment Anything 3 (SAM 3), Depth Anything V2 / MiDaS, OpenCV
* **Language & Vision Models:** Meta Llama 3.3 / Llama 4, Meta Llama Vision
* **Audio & Speech:** Silero VAD, Whisper / Fast Audio Intent Parsing
* **Backend:** Python, FastAPI, WebSockets, asyncio state loop
* **Frontend / Digital Twin:** React, Three.js / SVG Interactive Twin, Web Audio API
* **Cloud & Model Hosting:** Baseten, Fireworks AI, Modal

---

## Subdirectories & System Boundaries

- [docs](./docs/README.md): Central documentation directory containing workflow guides, architectural specifications, and approved EGM design records.
  - [docs/workflow](./docs/workflow/README.md): Claude Code and AI assistant master workflows, prompts, and best-practice protocols.
- [.claude](./.claude/rules/backend.md): Path-scoped agentic rules and environment configuration.

---

## Global Invariants

- **Canonical JSON State Bus:** All agents exchange state via strictly typed Pydantic contracts reflecting the 4 process variable classes (Set, Manipulated, Disturbance, Controlled).
- **Appliance-Only Twin:** The digital twin models only the AC appliance ("AC Clone") receiving remote commands; the real room is observed via sensors.
- **Actuation Slew Rate:** Actuator transitions are constrained to $15^\circ/\text{sec}$ stepper motor slew rate without instantaneous angle jumps.
- **Thermodynamic Grounding:** Comfort scores and setpoint adjustments must be computed deterministically via Fanger's PMV model (ASHRAE 55) rather than unconstrained LLM estimates.

---

## Global Side Effects

- Dispatches remote actuation commands to the AC Digital Twin emulator (`POST /ac-twin/command`).
- Broadcasts real-time kinematic (10 Hz) and thermodynamic (0.5 Hz) telemetry over WebSockets (`/ws/twin-state`).
- Accesses local webcam video stream and microphone audio stream for real-time spatial and acoustic perception.

---

## System Verification

- Run: `pytest tests/` (or `pytest tests/test_state_bus.py -k "test_louver_angle"`)
