---
description: Generate an on-demand architectural and conceptual explainer for any subsystem or file
---

# Explain Command (`/explain <path or concept>`)

Generate a clear, high-signal architectural explainer for a given subsystem, module, or concept in SpotCool AI to minimize cognitive load.

## Guidelines
1. **Mental Model First:** Start with a 2-3 sentence visual analogy or high-level flow diagram (e.g. Mermaid flowchart).
2. **Process Variable Mapping:** Identify which Process Variables are manipulated (Set, Manipulated, Disturbance, Controlled).
3. **Core Interfaces & Contracts:** Show the input types, output types, and error states.
4. **Gotchas & Hidden Assumptions:** Highlight non-obvious details (e.g., 50ms tick slew rates, median depth filtering, Fanger PMV constraints).
5. **Trace Through Code:** Provide clickable file links (`file:///...`) showing the exact lines where data enters and leaves the subsystem.
