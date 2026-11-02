---
paths:
  - "src/backend/**"
  - "src/orchestrator/**"
---

# Backend & Orchestrator Rules

- **Deterministic Schema:** All state communication must use strictly typed Pydantic models reflecting the 4 process variable classes (Set, Manipulated, Disturbance, Controlled).
- **FastAPI WebSockets:** Expose `/ws/twin-state` with two-tier message broadcasting: 10 Hz for kinematic telemetry (angles, coordinates) and 0.5 Hz for thermodynamic setpoints and wattage counters.
- **Fanger PMV Grounding:** Use `pythermalcomfort` or our deterministic `pmv_engine.py` adhering strictly to ASHRAE Standard 55 / ISO 7730. Never ask Meta Llama to guess comfort scores.
- **In-Context RL:** Maintain per-user comfort offset biases ($\Delta T_{\text{bias}}$) in-memory, updating immediately upon acoustic complaints (*"Too cold"*, *"Blowing in my face"*).

