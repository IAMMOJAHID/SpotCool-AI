"""Golden-eval runs: each scenario run once with the stub agent, saved, and read back.

Each session-scoped fixture runs the whole world (``World``, as ``spotcool-world run`` does) on
loopback: the BMS, the VRF gateway and both servers on ports the operating system has just
handed out, the run recorded into a temp ``run_dir``. It returns only what was saved, read back
by ``runner/report.py``'s ``load_run``, under the run id the fixture names. The judging is in
``test_golden.py``.

Runs are paced at ``SPEED``: each 15-minute step gets 0.5 s of wall time, so the stub's loopback
writes, made at a step's close, are taken in the next step, and a run reproduces. The world
never waits for an agent, so an unpaced run would let them lag by steps. No footage, no BOPTEST
(except the live fixture), no network beyond loopback.
"""

import asyncio
import os
import socket
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from spotcool_world.runner.assembly import World
from spotcool_world.runner.report import RecordedRun, load_run
from spotcool_world.scenarios import lint
from spotcool_world.services import WorldSettings

HOST = "127.0.0.1"
SPEED = 1800.0
"""Simulation seconds per wall second: a 15-minute step every 0.5 s, the RC day in about 25 s."""
LIVE_SPEED = 60.0
"""The live eval's speed, the paced BOPTEST checkpoint's: a step every 15 s."""
RC_TIMEOUT_S = 180.0
BOPTEST_TIMEOUT_S = 2400.0  # the start (select, a 3-day warm-up), then 48 steps of 15 s
LIVE_URL_ENV = "SPOTCOOL_LIVE_BOPTEST_URL"
LIVE_RUN_DIR_ENV = "SPOTCOOL_LIVE_RUN_DIR"
CONTROL = Path(__file__).resolve().parent / "scenarios" / "all_day_occupied.yaml"
"""The honesty control: every zone occupied for the whole run (not a library scenario)."""


def free_ports(*kinds: socket.SocketKind) -> list[int]:
    """One port per kind on 127.0.0.1, all free a moment ago and all different."""
    probes = [socket.socket(socket.AF_INET, kind) for kind in kinds]
    try:
        for probe in probes:
            probe.bind((HOST, 0))
        return [probe.getsockname()[1] for probe in probes]
    finally:
        for probe in probes:
            probe.close()


def run_world(scenario: str, run_dir: Path, run_id: str, **changes: Any) -> RecordedRun:
    """Run a library scenario once with the stub agent at ``SPEED``, and read the record back.

    No ``SPOTCOOL_WORLD_*`` variable and no ``world.yaml`` reach the settings: the run's working
    directory is ``run_dir``. The occupants post to the world's own HTTP server, which answers
    the complaint at once, where a closed port would take seconds of refused connections.

    Args:
        scenario: A library scenario, or a ``.yaml`` file given by its absolute path.
        run_dir: Where to record.
        run_id: The run's id.
        **changes: Settings that differ from these defaults, such as the plant or the speed.
    """
    tcp, udp = socket.SOCK_STREAM, socket.SOCK_DGRAM
    http, dashboard, bms, vrf = free_ports(tcp, tcp, udp, udp)
    values: dict[str, Any] = {
        "plant": "rc",
        "scenario": scenario,
        "speed": SPEED,
        "stub_agent": True,
        "run_dir": run_dir,
        "http_host": HOST,
        "http_port": http,
        "dashboard_port": dashboard,
        "bms_address": HOST,
        "bms_port": bms,
        "vrf_port": vrf,
        "agent_base_url": f"http://{HOST}:{http}",
    }
    settings = values | changes
    timeout_s = RC_TIMEOUT_S if settings["plant"] == "rc" else BOPTEST_TIMEOUT_S
    with pytest.MonkeyPatch.context() as patch:
        for key in [key for key in os.environ if key.startswith("SPOTCOOL_WORLD_")]:
            patch.delenv(key)
        patch.chdir(run_dir)
        world = World(WorldSettings(**settings), run_id=run_id)
        asyncio.run(asyncio.wait_for(world.run(handle_sigint=False), timeout_s))
    return load_run(run_dir, run_id)


@pytest.fixture(scope="session")
def eval_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The session's run directory, shared by every eval run."""
    return tmp_path_factory.mktemp("eval-runs")


@pytest.fixture(scope="session")
def hybrid_tuesday(eval_dir: Path) -> RecordedRun:
    """``hybrid_tuesday`` on RC: the demo day."""
    return run_world("hybrid_tuesday", eval_dir, "hybrid_tuesday-stub")


@pytest.fixture(scope="session")
def hybrid_tuesday_again(eval_dir: Path) -> RecordedRun:
    """``hybrid_tuesday`` on RC a second time, for the reproducibility eval."""
    return run_world("hybrid_tuesday", eval_dir, "hybrid_tuesday-again-stub")


@pytest.fixture(scope="session")
def all_hands_day(eval_dir: Path) -> RecordedRun:
    """``all_hands_day`` on RC: everyone in, the honesty check."""
    return run_world("all_hands_day", eval_dir, "all_hands_day-stub")


@pytest.fixture(scope="session")
def empty_friday(eval_dir: Path) -> RecordedRun:
    """``empty_friday`` on RC: one person in Core."""
    return run_world("empty_friday", eval_dir, "empty_friday-stub")


@pytest.fixture(scope="session")
def all_day_occupied(eval_dir: Path) -> RecordedRun:
    """The control (``CONTROL``) on RC: every zone occupied for the whole run; linted first."""
    findings = lint(CONTROL, step_s=WorldSettings.model_fields["step_s"].default)
    assert not findings, "; ".join(str(finding) for finding in findings)
    return run_world(str(CONTROL), eval_dir, "all_day_occupied-stub")


@pytest.fixture(scope="session")
def hybrid_tuesday_boptest(eval_dir: Path) -> RecordedRun:
    """``hybrid_tuesday`` on BOPTEST at ``SPOTCOOL_LIVE_BOPTEST_URL``, at ``LIVE_SPEED``.

    It skips, as the live plant tests do, when the variable is unset or BOPTEST does not answer
    within 5 s. The plants stop both BOPTEST tests when the run ends. With
    ``SPOTCOOL_LIVE_RUN_DIR`` set, the run is recorded there as ``eval-live-hybrid-<time>`` and
    kept, so ``spotcool-world report`` can read it; otherwise it goes in the session's temp dir.
    """
    from spotcool_world.services.boptest.client import BoptestClient, BoptestError

    url = os.environ.get(LIVE_URL_ENV)
    if not url:
        pytest.skip(f"set {LIVE_URL_ENV} to run the live eval")

    async def probe() -> None:
        client = BoptestClient(url)
        try:
            await client.version(timeout_s=5.0)
        finally:
            await client.aclose()

    try:
        asyncio.run(probe())
    except BoptestError as exc:
        pytest.skip(f"BOPTEST at {url} does not answer: {exc}")
    kept = os.environ.get(LIVE_RUN_DIR_ENV)
    run_dir = Path(kept).resolve() if kept else eval_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_world(
        "hybrid_tuesday",
        run_dir,
        f"eval-live-hybrid-{datetime.now():%Y%m%d-%H%M%S}",
        plant="boptest-local",
        boptest_local_url=url,
        speed=LIVE_SPEED,
    )
