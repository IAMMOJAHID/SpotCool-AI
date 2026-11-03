---
description: Run an Anthropic-style multi-agent pre-PR code review against REVIEW.md
---

# Code Review Command (`/code-review`)

You are an expert technical reviewer following Anthropic's multi-agent code review pipeline.
Your job is to thoroughly review the current git diff against the base branch (`dev` or `main`) before opening or updating a PR.

## Step 1: Read the Diff and Context
1. Inspect the current diff with `git diff origin/main...HEAD` (or `origin/dev...HEAD`).
2. Read `REVIEW.md` and `CLAUDE.md` to ground your review in the project's standards and failure definitions.
3. Read the full files modified (not just the diff chunks) to verify context.
4. If the change implements a work package brief (`docs/agent/workpackages/` or `docs/workpackages/`), read it and check the code against it (REVIEW.md §4b).
5. Follow the output rules in REVIEW.md §4a: count first, at most 3 P2s, tag `Pre-existing` bugs.

## Step 2: Multi-Layer Evaluation
Analyze the code through three parallel lenses:

1. **Domain Correctness & Definitions of Failure (`REVIEW.md`):**
   - **Documentation:** Does anything lie or contradict the implementation?
   - **Actuation:** Priority 10 only, the priority array read first and slots 1-9 backed off from, no other's slot released, the heartbeat kept, every slot released on stop?
   - **Perception & privacy:** Frames and audio in memory only, no complaint words logged, no ground truth read?
   - **Quoting:** Every figure from `docs/release/numbers.md`, with its label; only `Quotable` runs as SpotCool's own?
   - **BACnet:** One client and socket per device, one request at a time?

2. **Logic, Concurrency & Edge Cases:**
   - Are there null pointer / NoneType dereferences, race conditions, or off-by-one errors?
   - Are errors gracefully handled with structured fallbacks?
   - Are new branches covered by unit tests in the package's `tests/`?

3. **Security & Data Sanitization:**
   - Are there hardcoded secrets or API tokens?
   - Are WebSocket messages validated against strict Pydantic schemas?

## Step 3: Verification Pass (Prune False Positives)
For each potential issue found:
- Trace the code backwards. Is this already clamped, caught, or sanitized elsewhere?
- If you cannot point to an exact line of code and describe an executable reproduction, **discard the comment**.

## Step 4: Output Ranked Findings
Present your review clearly formatted:

```markdown
## 🔍 Pre-PR Code Review Summary

### Status: [PASS | ACTION REQUIRED]

### P0 (Blocking Issues)
- **`path/to/file.py:L42`**: Description of failure mechanism. Why it breaks. Suggested fix.

### P1 (Warnings & Test Gaps)
- **`path/to/file.py:L105`**: Description and recommendation.

### P2 (Suggestions & Polish)
- **`path/to/file.py:L12`**: Minor improvement.

---
**Verification Checklist:**
- [ ] Tests pass (`uv run pytest`, `uv run pytest -m eval`)
- [ ] Linters pass (`uv run ruff check packages scripts`, `uv run pyright packages`)
- [ ] No write outside priority 10, and no frame or audio stored
- [ ] Review cycle count: Round X of 3 max
```

*Reminder: If this is Round 3 of review fixes and issues persist, stop automated patches and request architectural human review.*
