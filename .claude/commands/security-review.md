---
description: Run an infrastructure, dependency, and input validation security audit
---

# Security Review Command (`/security-review`)

You are an automated security auditor checking the current diff and repository state against OWASP Top 10, secret leakage, and trust boundary violations.

## Audit Checklist

1. **Secret & Credential Archaeology:**
   - Scan for hardcoded API keys (`sk-...`, `ghp_...`, AWS secrets, JWT keys).
   - Verify `.env` or credential files are strictly gitignored.
   - Ensure environment variables are loaded via safe config models (`pydantic-settings`).

2. **Trust Boundaries & Deserialization:**
   - Validate that all incoming WebSocket payloads at `/ws/twin-state` and `/ac-twin/command` are parsed using strictly typed Pydantic models with extra fields forbidden (`extra="forbid"`).
   - Verify no use of `eval()`, `pickle.loads()`, or unsanitized dynamic shell executions.

3. **Audio & Video Privacy Constraints:**
   - Verify camera frames and microphone audio streams are processed in memory and never logged or written to unencrypted disk storage.
   - Confirm acoustic feedback agent discards raw audio buffers after Silero VAD / transcription.

4. **Dependencies & Supply Chain:**
   - Check `uv.lock` and `apps/dashboard/package-lock.json` for pinned versions.
   - Flag any deprecated or high-vulnerability packages.

## Output Format

```markdown
## 🛡️ Security Audit Report

### Overall Status: [SECURE | VULNERABILITIES DETECTED]

### Critical / High Findings (Blocking)
- None (or detail exact path, attack vector, and mitigation)

### Medium / Low Observations
- ...

### Privacy & Boundary Checklist
- [x] Ephemeral frame processing (no disk leaks)
- [x] Zero hardcoded secrets in diff
- [x] Schema input enforcement active
```
