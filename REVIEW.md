# REVIEW.md — SpotCool AI Code Review Guidelines

> Based on the Anthropic Code Review Architecture ([claude.dev/blog/solving-code-review-at-anthropic](https://claude.dev/blog/solving-code-review-at-anthropic/)).  
> **Note for Automated Reviewers:** This file must always be evaluated from the repository's **base branch** (e.g. `main` or `dev`) to ensure reviews are graded against trusted standards rather than PR modifications.

---

## 1. Core Philosophy: Machine-Heavy Review, Human-Centric Judgment

1. **Shift Bulk Reading to Machines:** Automated review agents analyze whole-codebase context, trace dependencies, and verify edge cases before any human review.
2. **Require Concrete Evidence:** Reviewers (AI or human) must cite specific file paths, line numbers, and failure mechanisms. Speculative vibes and vague opinions are not acceptable.
3. **No Stylistic Bikeshedding:** Automated linters, formatters and type checkers (`ruff`, `pyright`, `eslint`) handle formatting. Reviews focus strictly on correctness, architecture, honesty, privacy and safety.
4. **The 3-Round Cap Rule:** If an AI agent attempts fixes across 3 automated review rounds without clearing checks, **halt automated commits**. A 3-round failure indicates a flawed mental model; step back, rethink the architecture, or escalate to human review.
5. **AI Never Self-Approves:** Automated approvals never substitute for human review on Tier 1 and Tier 2 changes.

---

## 2. Domain-Specific "Definitions of Failure"

Every domain in SpotCool AI has an unambiguous failure condition:

| Subsystem | Definition of Failure | What to Catch |
| :--- | :--- | :--- |
| **Documentation & Guides** | *"It lies"* | Broken code samples, outdated commands, contradictory architecture diagrams, or documenting features not in code. |
| **Configurations & CI/CD** | *"It misroutes"* | Leaked API secrets, incorrect environment variables, hardcoded ports, or flawed workflow triggers. |
| **Actuation (BMS and VRF writes)** | *"It fights the operator"* | A write at any priority but 10; a write without reading the priority array first, or one that doesn't back off when a slot 1 to 9 holds a value; releasing a slot that isn't the agent's; a heartbeat that can stop toggling; a path (19:00, normal stop, Ctrl+C, crash) that doesn't release every slot the agent holds; a complaint or a language model writing a BMS zone point; a VRF point written more than once a second. |
| **Perception & Privacy** | *"It keeps what it saw"* | A frame or audio written to disk or a log, or kept beyond its use; a complaint's words in a log; the agent reading ground truth (`/ws/events`, `scenario_state`, `camera_scene`, `ZoneScene.people`) or importing `spotcool_world` at runtime. |
| **Quoting** | *"It overclaims"* | A figure that isn't in `docs/release/numbers.md`, or loses its wording or label there; a run quoted as SpotCool's own whose report isn't `Quotable`; a stub run without "upper bound with perfect perception"; any RC figure or never-quote figure; a change that weakens `quote_blockers`. |
| **BACnet** | *"It shares a socket"* | Two devices behind one BACnet client or socket; more than one request in flight on a client. Each device gets its own client: the agent's BMS client 1910 on UDP 47820, its VRF client 1911 on 47821. |

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
* Per-step work that may not fit the building loop's deadline (the step's wall time minus the larger of 2.5 s and 40 % of it).
* Models that drift from `packages/contracts` (a field, unit or point name the contract doesn't define).

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

## 4a. Output Rules

* **Count first:** The summary starts with the finding count, e.g. `2 findings (1 P0, 1 P1)`. If there is nothing to fix, say `No issues found.` and stop.
* **Nit cap:** At most 3 `P2` findings per review. Drop the rest.
* **Pre-existing:** Tag bugs the PR did not introduce as `Pre-existing` so they aren't blamed on the author.
* **No repeats:** Don't re-post a finding already on the PR. After round 2, review only the new commits.
* **Skip paths:** `temp/`, `resources/`, `evals/` fixtures, lockfiles, and generated files.

## 4b. Design Conformance (Work Package Briefs)

Features that need design decisions get a brief before code, with its binding decisions: `docs/agent/workpackages/` for the agent lane, `docs/workpackages/` for the world lane (the gate is in `AGENTS.md` §5).

* If the PR's **Design** section links a brief, check the code against it. A divergence that isn't reflected in the brief, or in the package's `AGENTS.md`, is a finding: the doc now *lies* (see §2).
* Design changes go back to the lead and the brief, not through code review.

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
