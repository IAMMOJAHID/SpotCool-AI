# Runbooks

Step-by-step lists for the user during the demo-video takes. Each one is short and links to [`packages/world/AGENTS.md`](../../packages/world/AGENTS.md) for the why.

| Runbook | When |
|---|---|
| [boptest-codespace.md](boptest-codespace.md) | Before any BOPTEST run: start the Codespace, forward its port, stop it after |
| [windows-firewall.md](windows-firewall.md) | Once per PC, before another PC or the phone's network reaches the world |
| [phone-camera.md](phone-camera.md) | Before the VRF segment: the live phone camera |
| [yabe.md](yabe.md) | Checking the BMS and the VRF by hand; the operator override for the video |
| [video-takes.md](video-takes.md) | The takes themselves |

Every command runs in Git Bash from the repo root of the main checkout, after:

```bash
export UV_PROJECT_ENVIRONMENT="C:/Users/hussa/dev/venvs/spotcool-ai"
export SPOTCOOL_WORLD_RUN_DIR="C:/Users/hussa/dev/runs"   # keep runs out of the repo
export SPOTCOOL_WORLD_FOOTAGE_DIR="C:/Users/hussa/dev/footage"   # the filmed clips and voice
```

Without `SPOTCOOL_WORLD_FOOTAGE_DIR` the cameras draw synthetic frames and the 14:15 voice complaint goes as chat: a take recorded so is not the filmed one.

PowerShell commands are marked; run them in an elevated PowerShell where it says so.
