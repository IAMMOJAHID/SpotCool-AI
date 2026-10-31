# AGENTS.md — Universal Agent Guide for SpotCool AI

> **Target Systems:** Antigravity (Google DeepMind) & Claude Code (Anthropic)  
> **Project:** SpotCool AI — Meta Global AI Developer Hackathon (Oct 26 – Nov 4, 2026)  
> **Mission:** Autonomous 3D spatial spot cooling & appliance digital twin to eliminate up to 60% HVAC energy waste.

---

## 1. The Six Core Engineering Laws of SpotCool AI

Every AI agent (Antigravity or Claude Code) must strictly follow these invariants:

1. **Law 1: Appliance-Only Twin ("AC Clone")**  
   We simulate the **physical AC split unit itself** receiving remote commands (stepper motors, fan blower, compressor load, piezo beep), NOT a full 3D room. The real physical room is captured by the webcam and microphone.
2. **Law 2: Actuator Slew Rate (No Instant Angle Jumps)**  
   Louvers are driven by mechanical stepper motors that slew at $\le 15^\circ/\text{sec}$ ($\pm 0.75^\circ$ per 50ms control tick). Never write code that sets target angles instantaneously.
3. **Law 3: SAM 3 Mask-Only Median Depth**  
   Extract occupant distance $Z$ using the **`np.median()` of depth values within the segmented person mask**. Never take the average of the 2D bounding box (background walls severely skew depth).
4. **Law 4: Decouple the Two Control Frequencies**  
   * **Fast Kinematic Loop (10 Hz):** Frame capture $\rightarrow$ SAM 3 mask $\rightarrow$ Median Depth $\rightarrow$ Louver $(\theta_{\text{pan}}, \theta_{\text{tilt}})$.  
   * **Slow Semantic Loop (0.2 Hz):** Llama Vision (clothing `Clo`, open door/window leaks) and Whisper/Silero VAD (voice complaints).  
   * **Rule:** Never block the 10 Hz loop on slow LLM/network inference.
5. **Law 5: Deterministic Thermodynamic Grounding (ASHRAE 55)**  
   Compute thermal comfort and PMV (Predicted Mean Vote) using deterministic Python formulas (`pythermalcomfort` / Fanger ISO 7730). Never ask an LLM to guess comfort scores or temperatures.
6. **Law 6: Ephemeral Privacy Boundary**  
   Webcam frames and microphone audio streams must be processed strictly in memory and discarded. Never write raw audio or video to unencrypted disk or logging endpoints.

---

## 2. Workstation & Git Governance

* **Active Machine Working Branch:** `feature/spotcool-dev` (All ongoing development on this machine occurs here).
* **PR Integration Target:** `dev` (All feature PRs merge into `dev`; `dev` merges into `main` for release).
* **Git Identity:** Must strictly use:
  ```text
  Name:  Mojahid Hussain
  Email: 86100226+IAMMOJAHID@users.noreply.github.com
  Remote: git@github.com-iammojahid:IAMMOJAHID/SpotCool-AI.git
  ```
* **Global Config Isolation:** Do not alter the global git config on this machine. Local config must remain isolated to this repo.
* **The 3-Round Review Cap:** If an agent attempts automated review fixes across 3 rounds without success, **halt automated commits** and escalate for architectural clarification.

---

## 3. Toolchain & Execution Commands

```bash
# Setup Python environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Run Backend State Bus (FastAPI)
uvicorn src.backend.main:app --reload --port 8000

# Run Frontend / Digital Twin (React + Vite + Web Audio)
npm run dev

# Verification & Quality Suite
pytest tests/
ruff check src/
mypy src/

# Single test
pytest tests/test_state_bus.py -k "test_louver_angle"
```

---

## 4. Dual-Agent Collaboration Protocol

| Dimension | Antigravity (Gemini) | Claude Code (Anthropic) |
| :--- | :--- | :--- |
| **Primary Strength** | System architecture, cross-directory synthesis, workspace management, sidecar visualization, multi-file refactoring. | Terminal command execution, fast surgical code edits, git operations, local CLI review loops. |
| **Config File** | `GEMINI.md`, `AGENTS.md` | `CLAUDE.md`, `.claude/rules/` |
| **Custom Commands** | Built-in slash commands (`/goal`, `/plan`, `/boost`, `/learn`) | Slash commands in `.claude/commands/` (`/code-review`, `/security-review`, `/explain`) |
| **Review Charter** | Audits against `REVIEW.md` and EGM blueprints (`docs/designs/`). | Runs `/code-review` and tests against base branch `REVIEW.md`. |
| **Conflict Avoidance** | Check `git status` before editing; commit cleanly on `feature/spotcool-dev`. | Keep turns focused, verify with `pytest`, run `/compact` when context > 50%. |

---

## 5. Directory Blueprint

```text
SpotCool-AI/
├── AGENTS.md                       # Universal agent invariants (this file)
├── GEMINI.md                       # Antigravity agent configuration
├── CLAUDE.md                       # Claude Code lean configuration
├── REVIEW.md                       # Code review failure definitions & grading charter
├── .claude/
│   ├── rules/                      # Path-scoped rules (backend, digital_twin, perception)
│   └── commands/                   # Custom slash commands (/code-review, /security-review, /explain)
├── .github/
│   ├── CODEOWNERS                  # Risk-tiered approval hierarchy
│   └── workflows/
│       └── claude-code-review.yml  # Automated PR review action
├── evals/
│   ├── code_review_evals.json      # Benchmark regression suite for review prompts
│   └── README.md
├── docs/
│   ├── workflow/
│   │   ├── CLAUDE_WORKFLOW_GUIDE.md
│   │   └── AGENT_COLLABORATION_GUIDE.md
│   └── designs/                    # EGM architecture and design specs
└── temp/H/                         # [Git-ignored] R&D whiteboards & hackathon rubrics
```
