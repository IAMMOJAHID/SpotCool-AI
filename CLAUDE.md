# CLAUDE.md — SpotCool AI Project Guide

SpotCool AI replaces passive uniform AC cooling with autonomous 3D spatial spot cooling for the Meta Global AI Developer Hackathon.

---

@CLAUDE.md

---


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

# Claude Code Review & Verification Commands
# (Based on claude.dev/blog/solving-code-review-at-anthropic/)
/code-review       # Pre-PR multi-agent review against base REVIEW.md
/security-review   # Security, secrets, and trust boundary audit
/explain <module>  # On-demand mental model explainer for any subsystem
```

## Workstation & Branch Policy
* **Working Branch:** `feature/spotcool-dev` (All ongoing development on this machine).
* **PR Integration Target:** `dev` (Merges into `main` for release).
* **Universal Agent Rules:** See `AGENTS.md` for shared invariants between Claude Code and Antigravity.

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
Flow: EGM spec approved in `docs/designs/` → implement → `/code-review` + `/security-review` checks code against the spec → PR (template links the spec). Weekly: `/egm:egm-drift-report`.

## Code Review Standards (Anthropic Architecture)
* **Charter:** See `REVIEW.md` for domain-specific "Definitions of Failure" and false-positive elimination rules.
* **3-Round Cap:** Never loop through automated fix cycles more than 3 times; halt and rethink architecture.
* **Base Branch Grounding:** Review rules are always read from the target base branch to ensure PRs cannot alter their own review standards.
* **Risk Tiers:** See `.github/CODEOWNERS` for automated vs human approval requirements.

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
