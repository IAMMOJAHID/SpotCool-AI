# Documentation Workflows

## Overview
This package houses development and AI assistant workflow guides, operational protocols, and prompting frameworks for SpotCool AI.

## Interface
- [CLAUDE_WORKFLOW_GUIDE.md](./CLAUDE_WORKFLOW_GUIDE.md): Master multi-stage workflow guide for Claude Code CLI and agentic pairing, grounded in official Anthropic research and best practices.

## Invariants
- Prompt templates and workflow sequences must enforce plan-mode gates (`Shift+Tab`) before destructive code generation.
- All implementation steps must include deterministic verification commands (`pytest`, `ruff`) to support autonomous self-correction loops.
- Context window usage must be monitored and cleared (`/clear`) between specification and implementation phases.

## Side Effects
- Documents operational guidelines; causes no runtime system state mutations or external API calls.

## Verification
- Run: `markdownlint docs/workflow/*.md` (or inspect links and syntax manually)

