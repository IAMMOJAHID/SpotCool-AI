# GEMINI.md — Antigravity Agent Configuration

> **Agent:** Antigravity (Google DeepMind)  
> **Workspace:** `SpotCool-AI`  
> **Active Branch:** `feature/spotcool-dev`  
> **Target PR Branch:** `dev` $\rightarrow$ `main`  
> **Dedicated Remote:** `git@github.com-iammojahid:IAMMOJAHID/SpotCool-AI.git`  
> **Dedicated Author:** `Mojahid Hussain <86100226+IAMMOJAHID@users.noreply.github.com>`

---

## 1. Shared Invariants
Read and enforce all **Six Engineering Laws** defined in [`AGENTS.md`](file:///c:/Users/hussa/OneDrive/Desktop/Spot-cool-ai/SpotCool-AI/AGENTS.md):
1. **Appliance-Only Twin:** AC clone only, never full room 3D model.
2. **Slew Rate Invariant:** Louver slew rate $\le 15^\circ/\text{s}$ ($\pm 0.75^\circ$ per 50ms tick). No instant jumps.
3. **Mask-Only Median Depth:** `np.median()` within SAM 3 segmented mask; never bounding box average.
4. **Loop Separation:** Fast 10 Hz kinematic loop decoupled from slow 0.2 Hz semantic LLM loop.
5. **Thermodynamic Grounding:** Deterministic Fanger PMV (ASHRAE 55) in Python; never ask LLM to guess comfort.
6. **Privacy Boundary:** Ephemeral memory processing for video frames and audio buffers.

---

## 2. Antigravity Best Practices & Roles

* **Role:** High-context system architect, multi-file refactoring lead, cross-repository synthesizer, and EGM review coordinator.
* **Artifacts:** Use structured markdown artifacts for extensive architecture designs, plans, and technical reports.
* **Subagents:** Use `egm-*` subagents for deep architecture reviews, and `research` subagent for deep documentation / paper audits.
* **Hand-Off to Claude Code:**
  - Before ending complex implementation turns, verify tests pass (`pytest tests/`, `ruff check src/`).
  - Keep commits clean on `feature/spotcool-dev` so Claude Code sessions from terminal stay in sync.
  - Never modify global Git configs (`~/.gitconfig`); maintain repository-local configs.
* **Slash Commands to Recommend:**
  - `/plan`: When decomposing multi-component features across backend, digital twin, and perception.
  - `/boost`: For complex thermodynamic or kinematic optimization algorithms.
  - `/learn`: To store newly discovered environment patterns.
