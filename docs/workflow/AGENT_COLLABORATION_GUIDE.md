# SpotCool AI — Dual-Agent Collaboration Guide
### Orchestrating Claude Code CLI and Antigravity (Gemini)

> **Hackathon:** Meta Global AI Developer Hackathon (Oct 26 – Nov 4, 2026)  
> **Repository:** [IAMMOJAHID/SpotCool-AI](https://github.com/IAMMOJAHID/SpotCool-AI)  
> **Active Working Branch:** `feature/spotcool-dev`  
> **Integration Branch:** `dev` $\rightarrow$ `main`

---

## 1. Architectural Philosophy: The "Two-Brain" Pair Programming Model

Rather than treating AI assistants as disconnected chat windows, SpotCool AI operates a coordinated **dual-agent pair programming model**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             SPOTCOOL AI REPOSITORY                           │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
            ┌──────────────────────────┴──────────────────────────┐
            ▼                                                     ▼
┌───────────────────────────────┐             ┌───────────────────────────────┐
│     ANTIGRAVITY (GEMINI)      │             │      CLAUDE CODE (CLI)        │
│   "High-Altitude Architect"   │             │   "High-Velocity Terminal"    │
├───────────────────────────────┤             ├───────────────────────────────┤
│ • Full-repo context window    │             │ • Fast terminal executions    │
│ • EGM spec design reviews     │             │ • Granular file diff editing  │
│ • Multi-directory refactoring │             │ • Interactive CLI debugging   │
│ • Rich Mermaid & KaTeX docs   │             │ • /code-review subagent runs  │
│ • Sidecar visualizers         │             │ • /security-review audits     │
│ • Governed by GEMINI.md       │             │ • Governed by CLAUDE.md       │
└───────────────┬───────────────┘             └───────────────┬───────────────┘
                │                                             │
                └──────────────────────┬──────────────────────┘
                                       ▼
                    ┌─────────────────────────────────────┐
                    │              AGENTS.md              │
                    │   Universal Invariants & In-Memory  │
                    │        Ground Truth Contract        │
                    └─────────────────────────────────────┘
```

---

## 2. The Six Non-Negotiable Invariants

Both agents are bound by the invariants documented in [`AGENTS.md`](file:///c:/Users/hussa/OneDrive/Desktop/Spot-cool-ai/SpotCool-AI/AGENTS.md) and [`REVIEW.md`](file:///c:/Users/hussa/OneDrive/Desktop/Spot-cool-ai/SpotCool-AI/REVIEW.md):

| Law | Invariant | Failure Condition | Mitigation |
| :--- | :--- | :--- | :--- |
| **Law 1** | **Appliance-Only Twin** | Modeling the entire room in 3D. | Emulate only the physical AC indoor split unit ("AC Clone") receiving remote commands. |
| **Law 2** | **Mechanical Slew Rate** | Instantaneous angle jumps ($\theta = \theta_{\text{target}}$). | Slew stepper louvers at $\le 15^\circ/\text{s}$ ($\pm 0.75^\circ$ per 50ms control tick). |
| **Law 3** | **Mask-Only Median Depth** | Averaging the 2D bounding box depth. | Compute `np.median(depth_map[sam_mask])` exclusively over segmented person pixels. |
| **Law 4** | **Loop Separation** | Blocking the 10 Hz video loop on slow LLMs. | Fast kinematic loop (10 Hz) $\leftrightarrow$ slow semantic loop (0.2 Hz background worker). |
| **Law 5** | **PMV Grounding** | LLM hallucinating temperature or fan offsets. | Deterministic Fanger PMV formula in Python (`pythermalcomfort` / ISO 7730). |
| **Law 6** | **Privacy Boundary** | Writing webcam frames or audio to disk. | Keep all raw video and audio streams strictly in ephemeral RAM buffers. |

---

## 3. Workstation & Git Policy

### Dedicated Identity
All commits and pushes from this workstation must authenticate strictly through your dedicated `IAMMOJAHID` identity:
* **Author Name:** `Mojahid Hussain`
* **Author Email:** `86100226+IAMMOJAHID@users.noreply.github.com`
* **SSH Remote Host:** `git@github.com-iammojahid:IAMMOJAHID/SpotCool-AI.git`
* **Global Isolation:** The default global user on this machine (`mhussam-ai`) must never be modified; repo-local configs enforce identity isolation.

### Branch Lifecycle
```
feature/spotcool-dev (Workstation Active Branch)
        │
        ▼ (PR with Claude Code Review & Verification)
      dev            (Staging & Integration)
        │
        ▼ (Final Review)
     main            (Production Submission)
```

---

## 4. Context Hand-Off: Seamless Switching

When toggling between Antigravity and Claude Code:

### 1. Moving from Antigravity to Claude Code:
1. Verify working tree is clean (`git status`).
2. Open terminal in workspace root.
3. Start Claude Code: `claude`
4. Run `/explain <subsystem>` or `/code-review` to orient Claude with the latest changes.

### 2. Moving from Claude Code to Antigravity:
1. Run `pytest tests/` and `ruff check src/` in Claude to ensure green checks.
2. Commit your working changes:
   ```bash
   git commit -m "feat: implement <module>"
   git push origin feature/spotcool-dev
   ```
3. Return to Antigravity; Antigravity immediately reads `GEMINI.md` and `AGENTS.md` to resume high-level planning.

---

## 5. Command Reference Cheat Sheet

| Task | Antigravity (Gemini) | Claude Code CLI |
| :--- | :--- | :--- |
| **Deep Planning** | `/plan` or `/boost` | `Shift+Tab` (Plan Mode) |
| **Code Review** | EGM swarm review (`/egm:egm-review`) | `/code-review` against `REVIEW.md` |
| **Security Audit** | CSO audit / Security subagent | `/security-review` |
| **Subsystem Mental Model** | Generates artifact with Mermaid | `/explain <path>` |
| **Run Tests** | Automated run command | `pytest tests/` |
| **Clean Context** | New conversation turn | `/clear` or `/compact` |
