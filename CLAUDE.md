# CLAUDE.md — SpotCool AI Project Guide

SpotCool AI replaces passive uniform AC cooling with autonomous 3D spatial spot cooling for the Meta Global AI Developer Hackathon.

## Commands

```bash
# Setup Python environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Run Backend State Bus (FastAPI)
uvicorn src.backend.main:app --reload --port 8000

# Run Frontend / Digital Twin (React + Vite)
npm run dev

# Run Verification Suite
pytest tests/
ruff check src/
mypy src/

# Run single test
pytest tests/test_state_bus.py -k "test_louver_angle"
```

## Non-Obvious Context & Gotchas

* **Source of Truth:** Research, whiteboards, and rubrics are in `temp/H/`. Read `temp/H/INDEX.md` and `temp/H/whiteboard_and_rubric_analysis.md` before making architectural decisions.
* **Appliance-Only Twin:** Do **not** build a 3D model of the whole room. We simulate the **AC appliance itself ("AC Clone")** receiving remote commands. The real room is captured by webcam/mic.
* **Actuation Slew (No Instant Jumps):** Louvers must slew at $15^\circ/\text{sec}$ ($\pm 0.75^\circ$ per 50ms tick). Fan RPM accelerates gradually. Never jump angles instantaneously.
* **Depth Filtering:** When extracting occupant distance $Z$, compute the **median depth only within the SAM 3 human mask pixels**, never the whole bounding box (background walls skew distance).
* **Two Loop Frequencies:** Decouple the fast kinematic loop (10 Hz: SAM 3 + Depth $\rightarrow$ louver angles) from the slow semantic loop (0.2 Hz: Llama Vision for clothing Clo, leaks, and voice intent).
* **Thermodynamic Grounding:** Compute thermal comfort with Fanger's PMV equation in Python (ASHRAE 55), not by asking the LLM to guess temperatures.

## EGM (Elephant-Goldfish Model)
EGM artifact directory: docs/designs
Use `/egm:egm-brainstorm`, `/egm:egm-draft`, and `/egm:egm-review` for spec-driven design reviews.

## Architecture

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
