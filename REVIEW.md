# REVIEW.md — SpotCool AI Code Review Guidelines

> Based on the Anthropic Code Review Architecture ([claude.dev/blog/solving-code-review-at-anthropic](https://claude.dev/blog/solving-code-review-at-anthropic/)).  
> **Note for Automated Reviewers:** This file must always be evaluated from the repository's **base branch** (e.g. `main` or `dev`) to ensure reviews are graded against trusted standards rather than PR modifications.

---

## 1. Core Philosophy: Machine-Heavy Review, Human-Centric Judgment

1. **Shift Bulk Reading to Machines:** Automated review agents analyze whole-codebase context, trace dependencies, and verify edge cases before any human review.
2. **Require Concrete Evidence:** Reviewers (AI or human) must cite specific file paths, line numbers, and failure mechanisms. Speculative vibes and vague opinions are not acceptable.
3. **No Stylistic Bikeshedding:** Automated linters and formatters (`ruff`, `mypy`, `eslint`) handle formatting. Reviews focus strictly on correctness, architecture, thermodynamics, and safety.
4. **The 3-Round Cap Rule:** If an AI agent attempts fixes across 3 automated review rounds without clearing checks, **halt automated commits**. A 3-round failure indicates a flawed mental model; step back, rethink the architecture, or escalate to human review.
5. **AI Never Self-Approves:** Automated approvals never substitute for human review on Tier 1 and Tier 2 changes.

---

## 2. Domain-Specific "Definitions of Failure"

Every domain in SpotCool AI has an unambiguous failure condition:

| Subsystem | Definition of Failure | What to Catch |
| :--- | :--- | :--- |
| **Documentation & Guides** | *"It lies"* | Broken code samples, outdated commands, contradictory architecture diagrams, or documenting features not in code. |
| **Configurations & CI/CD** | *"It misroutes"* | Leaked API secrets, incorrect environment variables, hardcoded ports, or flawed workflow triggers. |
| **Thermodynamics & ASHRAE 55** | *"It hallucinates comfort"* | Having an LLM guess temperature or fan offsets without mathematical grounding in Fanger's PMV equation (`pythermalcomfort` / ISO 7730). |
| **Digital Twin & Kinematics** | *"Instantaneous angle jumps"* | Telemetry or commands that jump angles directly instead of slewing louvers at $\le 15^\circ/\text{sec}$ ($\pm 0.75^\circ$ per 50ms tick). |
| **Perception & Spatial Depth** | *"Bounding box averaging"* | Computing occupant distance $Z$ using the full bounding box average instead of taking the `np.median()` of segmented SAM 3 mask pixels. |
| **Control Loop Timing** | *"Blocking the 10 Hz loop"* | Synchronously awaiting slow LLM inference (0.2 Hz Llama Vision / Whisper) inside the fast 10 Hz video/louver kinematic loop. |

---

## 3. Severity Ranking Framework

All review findings must be tagged with one of three severity tiers:

### `P0 (Blocking / Bug)` — Must be resolved before merge
* Violations of the process definitions of failure above.
* Concurrency race conditions, unhandled exceptions on the WebSocket bus, or memory leaks.
* Hardcoded credentials or insecure deserialization.
* Broken unit tests or test regressions.

### `P1 (Warning / Non-blocking)` — Strongly recommended before merge
* Missing test coverage for newly introduced branches or edge cases.
* Suboptimal algorithmic complexity in image masking or depth calculation.
* Inconsistent process variable typing across Pydantic schemas.

### `P2 (Suggestion / Polish)` — Optional follow-up
* Minor code readability or naming improvements.
* Documentation phrasing polish.
* Type annotation improvements that do not affect runtime behavior.

---

## 4. Verification Pass (False-Positive Elimination)

Before posting review feedback, review agents must run a **Self-Verification Step**:
1. Re-read the full file surrounding the flagged lines (not just the git diff chunk).
2. Check if the suspected issue is handled elsewhere (e.g. in middleware, upstream sanitizers, or clamp functions).
3. If the scenario cannot be reproduced by a concrete code path or unit test, **discard the comment**.

---

## 5. Review Flow & Lifecycle

```
[Developer / Agent writes code]
               │
               ▼
   [Local: claude /code-review] ──► (Parallel Subagents: Correctness + Security)
               │
               ▼
     [Local: pytest & ruff]
               │
               ▼
     [Open Pull Request]
               │
               ▼
[GitHub Action: Claude Code Review] ──► Reads base branch REVIEW.md
               │                      Dispatches parallel PR reviewers
               │                      Filters false positives & posts P0/P1/P2
               ▼
 [Risk-Based Human Review / CODEOWNERS]
               │
               ▼
        [Merge to dev/main]
```
