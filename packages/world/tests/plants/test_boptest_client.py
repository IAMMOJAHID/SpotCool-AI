"""BOPTEST client and plant against a fake BOPTEST behind httpx.MockTransport.

The fake behaves like the REST API measured on BOPTEST: test calls answer {status, message,
payload}; an empty body sent as JSON gets HTTP 500; an unknown input gets 400; inputs latch
until _activate = 0; overwritten signals come back under their _u keys; stop answers a plain
"OK", and a stopped test id then gets 400. PUT /results answers each point's samples every
30 s from start_time up to final_time or the latest time stored, as BOPTEST stores them (the
meters follow ``power``), and HTTP 500 for a start_time past the store; an unknown point gets
400.

Connection faults: ``once`` fails a request before it is handled, ``lose`` after it (the
answer is lost on its way back), ``late_s`` lands the next advance that long after its answer
was lost, and ``down_until`` refuses every connection until that time on the fake's clock.
"""

import asyncio
import json
import math
import re
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from loguru import logger

from spotcool_contracts import Zone
from spotcool_world.processors.telemetry import EnergyMeter
from spotcool_world.services import PlantStartParams
from spotcool_world.services.boptest import client as client_module
from spotcool_world.services.boptest.catalog import (
    MANAGED_INPUTS,
    bms_signal,
    load_catalog,
    zone_damper,
)
from spotcool_world.services.boptest.client import (
    ADVANCE_TIMEOUT_S,
    STOP_RETRY_S,
    BoptestClient,
    BoptestError,
)
from spotcool_world.services.boptest.plant import RECOVERY_S, BoptestPlant, step_means

URL = "http://boptest.test"
TESTID = "t-1"
CATALOG = load_catalog()
SELECT = f"/testcases/{CATALOG.testcase}/select-async"
CORE = bms_signal("ZN-COR-CLG-SP", write=True)
CORE_ACTIVATE = CORE.removesuffix("_u") + "_activate"
SOUTH_DAMPER = zone_damper(Zone.SOU)
HEATING = bms_signal("ZN-COR-HTG-SP")  # read under the input's _u key
MANAGED_ACTIVATE = {name.removesuffix("_u") + "_activate" for name in MANAGED_INPUTS}
START = PlantStartParams(start_time_s=198 * 86_400 + 10 * 3600, step_s=900.0, warmup_s=3600.0)
METERS = CATALOG.electric_power
RESULTS = f"/results/{TESTID}"
SPIKE_W, IDLE_W = 9000.0, 1000.0
SPIKE_MEAN_W = (SPIKE_W * 60 + IDLE_W * 840) / 900  # a minute of 9 kW in a 15-minute step


def spiky(t_s: float) -> float:
    """A meter's power: 9 kW for a minute from 5 minutes into every step, else 1 kW.

    So a damper reopening mid-step looks to the meters, and the end of the step misses it.
    """
    return SPIKE_W if 300 <= (t_s - START.start_time_s) % START.step_s < 360 else IDLE_W


def answer(payload: Any, status: int = 200, message: str = "ok") -> httpx.Response:
    return httpx.Response(status, json={"status": status, "message": message, "payload": payload})


def route_of(request: httpx.Request) -> str:
    return request.url.path.strip("/").split("/")[0]


class FakeTime:
    """Wall time that passes only when the code under test sleeps, so no test waits for it."""

    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    async def sleep(self, delay_s: float) -> None:
        self.now += delay_s
        await asyncio.sleep(0)


class FakeBoptest:
    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self.clock = clock
        self.requests: list[httpx.Request] = []
        self.sent_at: list[float] = []  # the clock's time of each request
        self.queued_polls = 0
        self.fail: dict[str, int] = {}  # route ("advance", "initialize", ...) -> status
        self.once: dict[str, list[int | Exception]] = {}  # route -> one-shot answers, in order
        self.lose: dict[str, list[int | Exception]] = {}  # route -> handled, then this instead
        self.late_s: float | None = None
        self.pending: list[tuple[float, dict[str, Any]]] = []  # late advances: (due, body)
        self.down_until = -math.inf
        self.advances = 0  # steps BOPTEST applied
        self.delays: dict[str, float] = {}  # route -> seconds to answer
        self.busy = 0  # requests being answered right now
        self.pings_while_busy = 0
        self.period_over = False
        self.applied: dict[str, float] = {}
        self.time = 0.0
        self.step = 3600.0
        self.power: Callable[[float], float] = lambda t_s: 295.0  # each meter's W at a time
        self.results: Any = None  # an answer to PUT /results instead of the samples
        self.stopped: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle_slowly)

    def calls(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == path]

    def times(self, path: str) -> list[float]:
        return [t for r, t in zip(self.requests, self.sent_at, strict=True) if r.url.path == path]

    def bodies(self, path: str) -> list[dict[str, Any]]:
        return [json.loads(r.content) if r.content else {} for r in self.calls(path)]

    async def handle_slowly(self, request: httpx.Request) -> httpx.Response:
        if route_of(request) == "name" and self.busy:
            self.pings_while_busy += 1
        self.busy += 1
        try:
            await asyncio.sleep(self.delays.get(route_of(request), 0.0))
            return self.handle(request)
        finally:
            self.busy -= 1

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.sent_at.append(self.clock())
        for due in [late for late in self.pending if late[0] <= self.clock()]:
            self.pending.remove(due)  # the worker finishes a job its client left
            self.advance(due[1])
        if self.clock() < self.down_until:
            raise httpx.ConnectError("connection refused")
        response = self.respond(request)
        if self.lose.get(route_of(request)):
            lost = self.lose[route_of(request)].pop(0)
            if isinstance(lost, Exception):
                raise lost
            return httpx.Response(lost, text="Bad Gateway")  # from the tunnel, not BOPTEST
        return response

    def respond(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("content-type") == "application/json" and not request.content:
            return httpx.Response(500, text="Unexpected end of JSON input")
        path = request.url.path
        route = route_of(request)
        body = json.loads(request.content) if request.content else {}
        if route in self.fail:
            return answer(None, self.fail[route], f"{route} failed")
        if self.once.get(route):
            failure = self.once[route].pop(0)
            if isinstance(failure, Exception):
                raise failure
            return answer(None, failure, f"{route} failed once")
        if route == "version":
            return answer({"version": "0.9.0"})
        if route == "testcases":
            if path != SELECT:
                return httpx.Response(404, text="Not Found")
            return httpx.Response(200, json={"testid": TESTID})
        if TESTID in self.stopped:
            return httpx.Response(400, json={"errors": [{"msg": f"Invalid testid: {TESTID}"}]})
        if route == "status":
            if self.queued_polls:
                self.queued_polls -= 1
                return httpx.Response(200, json="Queued")
            return httpx.Response(200, json="Running")
        if route == "stop":
            self.stopped.append(TESTID)
            return httpx.Response(200, text="OK")
        if route == "initialize":
            self.time, self.applied = body["start_time"], {}
            return answer(self.state())
        if route == "scenario":
            self.time, self.applied = CATALOG.period_start_s(body["time_period"]), {}
            return answer(
                {"electricity_price": body["electricity_price"], "time_period": self.state()}
            )
        if route == "step":
            self.step = body["step"]
            return answer({"step": self.step})
        if route == "advance":
            if self.late_s is not None:  # BOPTEST got it, and runs it after the answer is lost
                self.pending.append((self.clock() + self.late_s, body))
                self.late_s = None
                raise httpx.ReadTimeout("")
            return self.advance(body)
        if route == "kpi":
            kpis = dict.fromkeys(["cost_tot", "emis_tot", "idis_tot", "pele_tot", "tdis_tot"], 0.1)
            return answer(
                kpis | {"ener_tot": 0.2, "pdih_tot": None, "pgas_tot": None, "time_rat": 0.0}
            )
        if route == "name":
            return answer({"name": CATALOG.testcase})
        if route == "results":
            if self.results is not None:
                return answer(self.results)
            start, final = body["start_time"], min(body["final_time"], self.time)
            if start > final:  # BOPTEST finds no stored time from start_time on
                return answer(None, 500, "Failed to query simulation results: IndexError")
            state = self.state()
            if unknown := [name for name in body["point_names"] if name not in state]:
                return answer(None, 400, f"Invalid point name {unknown[0]} in point_names.")
            times = [start + 30.0 * k for k in range(int((final - start) // 30) + 1)]
            samples = {
                name: [self.power(t_s) if name in METERS else state[name] for t_s in times]
                for name in body["point_names"]
            }
            return answer(samples | {"time": times})
        return httpx.Response(404, text="Not Found")

    def advance(self, body: dict[str, Any]) -> httpx.Response:
        for key in body:
            if (
                key.removesuffix("_activate") + "_u" not in CATALOG.inputs
                and key not in CATALOG.inputs
            ):
                return answer(None, 400, f"Unexpected input variable: {key}.")
        if self.period_over:
            return answer({}, message="End of test case scenario time period reached.")
        for name in CATALOG.inputs:  # inputs latch until _activate = 0
            flag = body.get(name.removesuffix("_u") + "_activate")
            if flag == 1:
                self.applied[name] = body[name]
            elif flag == 0:
                self.applied.pop(name, None)
        self.time += self.step
        self.advances += 1
        return answer(self.state())

    def state(self) -> dict[str, float | None]:
        state: dict[str, float | None] = {"time": self.time, "hvac_reaAhu_dp_sup_y": None}
        for name in CATALOG.outputs:
            if name.removesuffix("_y") + "_u" not in CATALOG.inputs:  # REST drops overwritten _y
                state.setdefault(name, 295.0)
        for name in CATALOG.inputs:
            state[name] = self.applied.get(name, 297.15)
            state[name.removesuffix("_u") + "_activate"] = float(name in self.applied)
        return state | dict.fromkeys(METERS, self.power(self.time))  # the end of the step's


def make_plant(
    fake: FakeBoptest, *, keepalive_s: float = 300.0, wall: FakeTime | None = None
) -> BoptestPlant:
    """A plant on the fake; with ``wall``, its retries and recoveries run on fake time."""
    timing: dict[str, Any] = {} if wall is None else {"clock": wall.clock, "sleep": wall.sleep}
    client = BoptestClient(URL, poll_s=0.0, backoff_s=0.001, transport=fake.transport(), **timing)
    return BoptestPlant(client, keepalive_s=keepalive_s, **timing)


@pytest.mark.asyncio
async def test_content_type_only_with_a_body() -> None:
    fake = FakeBoptest()
    plant = make_plant(fake)
    await plant.start(START)
    await plant.step({})
    kpis = await plant.kpis()
    await plant.stop()
    for request in fake.requests:
        assert ("content-type" in request.headers) == bool(request.content), request.url.path
    assert "content-type" not in fake.calls(SELECT)[0].headers  # a POST without a body
    assert kpis["ener_tot"] == 0.2 and kpis["pgas_tot"] is None


@pytest.mark.asyncio
async def test_every_managed_input_is_sent_every_step() -> None:
    fake = FakeBoptest()
    plant = make_plant(fake)
    await plant.start(START)
    held = await plant.step({CORE: 301.15, SOUTH_DAMPER: 0.0})
    released = await plant.step({CORE: None, SOUTH_DAMPER: None})
    later = await plant.step({})
    await plant.stop()
    bodies = fake.bodies(f"/advance/{TESTID}")
    for body in bodies:  # all ten, every step, even on a step with no overrides
        assert MANAGED_ACTIVATE <= body.keys()
    assert [{k for k in body if k.endswith("_u")} for body in bodies] == [
        {CORE, SOUTH_DAMPER},
        set(),
        set(),
    ]
    assert [body[CORE_ACTIVATE] for body in bodies] == [1, 0, 0]  # released: _activate 0
    assert bodies[0][SOUTH_DAMPER] == 0.0  # zero is a value, not "no override"
    assert (held[CORE], released[CORE], later[CORE]) == (301.15, 297.15, 297.15)
    assert "hvac_reaAhu_dp_sup_y" not in held  # a null from BOPTEST is left out


@pytest.mark.asyncio
async def test_other_inputs_become_managed_once_used() -> None:
    fake = FakeBoptest()
    plant = make_plant(fake)
    await plant.start(START)
    await plant.step({HEATING: 290.15})
    await plant.step({})
    await plant.stop()
    first, second = fake.bodies(f"/advance/{TESTID}")
    heating_activate = HEATING.removesuffix("_u") + "_activate"
    assert (first[HEATING], first[heating_activate], second[heating_activate]) == (290.15, 1, 0)
    assert HEATING not in second


@pytest.mark.asyncio
async def test_bad_inputs_never_reach_boptest() -> None:
    fake = FakeBoptest()
    plant = make_plant(fake)
    await plant.start(START)
    with pytest.raises(ValueError, match="unknown BOPTEST inputs"):
        await plant.step({"hvac_oveZonSupCor_TZonCooSet_y": 301.15})
    with pytest.raises(ValueError, match="finite"):
        await plant.step({CORE: float("nan")})
    await plant.stop()
    assert not fake.calls(f"/advance/{TESTID}")


@pytest.mark.asyncio
async def test_select_retries_only_what_created_nothing_and_waits_for_running() -> None:
    fake = FakeBoptest()
    fake.once["testcases"] = [httpx.ConnectError("connection refused"), 429]
    fake.once["status"] = [503]  # a status poll is safe to retry on any transient failure
    fake.queued_polls = 2
    client = BoptestClient(URL, poll_s=0.0, backoff_s=0.001, transport=fake.transport())
    assert await client.select(CATALOG.testcase) == TESTID
    assert (len(fake.calls(SELECT)), len(fake.calls(f"/status/{TESTID}"))) == (3, 4)
    await client.stop(TESTID)
    await client.aclose()


@pytest.mark.parametrize(
    "failure", [503, httpx.ReadTimeout("no answer")], ids=["server-error", "no-answer"]
)
@pytest.mark.asyncio
async def test_select_fails_fast_when_a_test_may_have_been_created(failure: Any) -> None:
    fake = FakeBoptest()
    fake.once["testcases"] = [failure]
    client = BoptestClient(URL, backoff_s=0.001, transport=fake.transport())
    with pytest.raises(BoptestError, match="may have been created .* 15 idle minutes"):
        await client.select(CATALOG.testcase)
    assert len(fake.calls(SELECT)) == 1  # a retry could start a second test
    await client.aclose()


@pytest.mark.asyncio
async def test_select_does_not_retry_a_client_error() -> None:
    fake = FakeBoptest()
    client = BoptestClient(URL, backoff_s=0.001, transport=fake.transport())
    with pytest.raises(BoptestError, match="HTTP 404"):
        await client.select("no_such_test_case")
    assert len(fake.requests) == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_a_test_still_queued_is_stopped() -> None:
    fake = FakeBoptest()
    fake.queued_polls = 10**6
    client = BoptestClient(URL, select_wait_s=0.05, poll_s=0.01, transport=fake.transport())
    with pytest.raises(BoptestError, match="still Queued"):
        await client.select(CATALOG.testcase)
    assert fake.stopped == [TESTID]
    await client.aclose()


@pytest.mark.asyncio
async def test_a_failed_start_stops_the_test() -> None:
    fake = FakeBoptest()
    fake.fail["initialize"] = 500
    plant = make_plant(fake)
    with pytest.raises(BoptestError, match="initialize failed"):
        await plant.start(START)
    assert fake.stopped == [TESTID]
    assert plant.testid is None


@pytest.mark.asyncio
async def test_a_failed_step_raises_and_the_test_is_stopped_once() -> None:
    fake = FakeBoptest()
    plant = make_plant(fake)
    await plant.start(START)
    fake.fail["advance"] = 500
    with pytest.raises(BoptestError, match="HTTP 500"):
        await plant.step({})
    assert len(fake.calls(f"/advance/{TESTID}")) == 1  # never retried: it may have run
    await plant.stop()
    await plant.stop()
    assert fake.stopped == [TESTID]
    assert [name for name, _ in plant.client.calls][-1] == "PUT stop"


ADVANCE = f"/advance/{TESTID}"
OVERRIDES = {CORE: 301.15, SOUTH_DAMPER: 0.0}


async def first_step(fake: FakeBoptest, wall: FakeTime) -> tuple[BoptestPlant, dict[str, float]]:
    """A plant started on ``fake`` and its first step, with ``OVERRIDES``."""
    plant = make_plant(fake, wall=wall)
    await plant.start(START)
    return plant, await plant.step(OVERRIDES)


async def what_advance_answers() -> dict[str, float]:
    """The first step's state when nothing goes wrong."""
    plant, state = await first_step(FakeBoptest(), FakeTime())
    await plant.stop()
    return state


def warnings_into(lines: list[str]) -> int:
    return logger.add(lambda message: lines.append(message.record["message"]), level="WARNING")


@pytest.mark.parametrize(
    "lost",
    [httpx.ReadTimeout(""), httpx.RemoteProtocolError("Server disconnected"), 502],
    ids=["read-timeout", "dropped", "tunnel-502"],
)
@pytest.mark.asyncio
async def test_a_step_whose_answer_was_lost_is_read_back_from_the_stored_results(
    lost: int | Exception,
) -> None:
    wall = FakeTime()
    fake = FakeBoptest(wall.clock)
    fake.lose["advance"] = [lost]  # BOPTEST stepped, but its answer never came back
    warnings: list[str] = []
    sink = warnings_into(warnings)
    try:
        plant, state = await first_step(fake, wall)
    finally:
        logger.remove(sink)
    await plant.stop()
    assert state == await what_advance_answers()  # BOPTEST's own state, exactly
    assert (fake.advances, len(fake.calls(ADVANCE)), plant.connection_recoveries) == (1, 1, 1)
    t_next = START.start_time_s + START.step_s
    probe, read_back = fake.bodies(RESULTS)[:2]
    assert (probe["start_time"], probe["final_time"]) == (START.start_time_s, t_next + 900)
    assert (read_back["start_time"], read_back["final_time"]) == (t_next, t_next)
    assert {"time", *read_back["point_names"]} == fake.state().keys()  # what advance carries
    assert any(f"step to {t_next:.0f} s lost its connection" in w for w in warnings)


@pytest.mark.asyncio
async def test_an_advance_that_never_got_there_is_resent() -> None:
    wall = FakeTime()
    fake = FakeBoptest(wall.clock)
    fake.once["advance"] = [httpx.ConnectError("connection refused")]
    plant, state = await first_step(fake, wall)
    await plant.stop()
    assert state == await what_advance_answers()
    assert (fake.advances, len(fake.calls(ADVANCE)), plant.connection_recoveries) == (1, 2, 1)
    calls = [r.url.path for r in fake.requests if r.url.path in (ADVANCE, RESULTS)]
    assert calls == [ADVANCE, ADVANCE, RESULTS]  # resent at once; then the step's power means


@pytest.mark.asyncio
async def test_an_advance_that_never_arrived_is_resent_only_after_the_advance_timeout() -> None:
    wall = FakeTime()
    fake = FakeBoptest(wall.clock)
    fake.once["advance"] = [httpx.ReadTimeout("")]  # lost before BOPTEST got it
    plant, state = await first_step(fake, wall)
    await plant.stop()
    sent, resent = fake.times(ADVANCE)
    assert resent - sent >= ADVANCE_TIMEOUT_S
    reads = [t for t in fake.times(RESULTS) if t < resent]
    assert len(reads) > 1  # read again and again until then, BOPTEST still at the start
    assert (fake.advances, plant.connection_recoveries) == (1, 1)
    assert state == await what_advance_answers()


@pytest.mark.asyncio
async def test_an_advance_that_lands_late_is_read_back_not_resent() -> None:
    wall = FakeTime()
    fake = FakeBoptest(wall.clock)
    fake.late_s = 5.0  # BOPTEST runs it 5 s after the drop
    plant, state = await first_step(fake, wall)
    await plant.stop()
    assert (fake.advances, len(fake.calls(ADVANCE)), plant.connection_recoveries) == (1, 1, 1)
    assert 5.0 <= wall.now < ADVANCE_TIMEOUT_S
    assert state == await what_advance_answers()


@pytest.mark.parametrize(
    ("ambiguous", "tried"),
    [(False, r"tried 0 reads .* and \d+ resends"), (True, r"tried \d+ reads .* and 0 resends")],
    ids=["unsent", "ambiguous"],
)
@pytest.mark.asyncio
async def test_an_outage_past_the_recovery_budget_fails_the_step_clearly(
    ambiguous: bool, tried: str
) -> None:
    wall = FakeTime()
    fake = FakeBoptest(wall.clock)
    plant = make_plant(fake, wall=wall)
    await plant.start(START)
    if ambiguous:  # the advance's answer is lost, then nothing gets through
        fake.once["advance"] = [httpx.ReadTimeout("")]
        fake.once["results"] = [httpx.ConnectError("connection refused")] * 10_000
    else:
        fake.down_until = math.inf  # the tunnel never comes back
    with pytest.raises(BoptestError) as raised:
        await plant.step({})
    message = str(raised.value)
    assert f"no state for the step within {RECOVERY_S:.0f} s of its connection failing" in message
    first = "ReadTimeout after 120 s" if ambiguous else "ConnectError"
    assert f"failing (POST /advance/{TESTID}: {first}" in message
    assert re.search(tried, message) and message.endswith("ConnectError: connection refused")
    assert wall.now < RECOVERY_S + 1  # it waited at most RECOVERY_S, and the last call
    resends = zip(fake.times(ADVANCE)[1:], fake.calls(ADVANCE)[1:], strict=True)
    timeouts = [(sent, call.extensions["timeout"]["read"]) for sent, call in resends]
    assert all(sent + timeout_s <= RECOVERY_S + 1 for sent, timeout_s in timeouts)  # none later
    assert (fake.advances, plant.connection_recoveries) == (0, 0)
    fake.once.clear()
    fake.down_until = -math.inf
    await plant.stop()


@pytest.mark.asyncio
async def test_a_refused_step_is_never_recovered() -> None:
    wall = FakeTime()
    fake = FakeBoptest(wall.clock)
    plant = make_plant(fake, wall=wall)
    await plant.start(START)
    fake.once["advance"] = [400]  # BOPTEST answered: nothing to recover
    with pytest.raises(BoptestError, match="HTTP 400"):
        await plant.step({})
    assert (len(fake.calls(ADVANCE)), len(fake.calls(RESULTS)), wall.now) == (1, 0, 0.0)
    await plant.stop()


@pytest.mark.asyncio
async def test_a_step_applied_twice_is_an_error_not_a_state() -> None:
    wall = FakeTime()
    fake = FakeBoptest(wall.clock)
    plant = make_plant(fake, wall=wall)
    await plant.start(START)
    fake.step = 2 * START.step_s  # BOPTEST ends up two steps on
    fake.lose["advance"] = [httpx.ReadTimeout("")]
    with pytest.raises(BoptestError, match="advanced more than once"):
        await plant.step({})
    assert plant.connection_recoveries == 0
    await plant.stop()


@pytest.mark.asyncio
async def test_a_timeout_names_the_exception_and_the_seconds_it_allowed() -> None:
    fake = FakeBoptest()
    fake.once["kpi"] = [httpx.ReadTimeout("")]  # httpx's message is empty
    client = BoptestClient(URL, transport=fake.transport())
    with pytest.raises(BoptestError) as raised:
        await client.kpis(TESTID)
    await client.aclose()
    assert str(raised.value) == f"GET /kpi/{TESTID}: ReadTimeout after 120 s"
    assert raised.value.status is None and not raised.value.unsent


def make_client(fake: FakeBoptest, wall: FakeTime) -> BoptestClient:
    return BoptestClient(
        URL, transport=fake.transport(), clock=wall.clock, sleep=wall.sleep, backoff_s=1.0
    )


@pytest.mark.asyncio
async def test_stop_rides_out_a_short_outage() -> None:
    wall = FakeTime()
    fake = FakeBoptest(wall.clock)
    fake.down_until = 10.0  # the forward restarts
    warnings: list[str] = []
    sink = warnings_into(warnings)
    try:
        await make_client(fake, wall).stop(TESTID)
    finally:
        logger.remove(sink)
    assert fake.stopped == [TESTID]
    assert len(fake.calls(f"/stop/{TESTID}")) > 1 and wall.now >= 10.0
    assert not [w for w in warnings if "could not stop" in w]


@pytest.mark.asyncio
async def test_stop_after_a_longer_outage_logs_how_to_stop_the_test() -> None:
    wall = FakeTime()
    fake = FakeBoptest(wall.clock)
    fake.down_until = math.inf
    warnings: list[str] = []
    sink = warnings_into(warnings)
    try:
        await make_client(fake, wall).stop(TESTID)  # never raises
    finally:
        logger.remove(sink)
    assert fake.stopped == [] and wall.now <= STOP_RETRY_S
    [warning] = [w for w in warnings if "could not stop" in w]
    assert f"could not stop test {TESTID}: PUT /stop/{TESTID}: ConnectError" in warning
    assert warning.endswith(f"stop it now with: curl -X PUT {URL}/stop/{TESTID}")


@pytest.mark.asyncio
async def test_a_retried_stop_that_finds_the_test_gone_has_stopped_it() -> None:
    wall = FakeTime()
    fake = FakeBoptest(wall.clock)
    fake.lose["stop"] = [httpx.ReadTimeout("")]  # stopped, but the answer was lost
    warnings: list[str] = []
    sink = warnings_into(warnings)
    try:
        await make_client(fake, wall).stop(TESTID)
    finally:
        logger.remove(sink)
    assert fake.stopped == [TESTID] and len(fake.calls(f"/stop/{TESTID}")) == 2
    assert not [w for w in warnings if "could not stop" in w]


@pytest.mark.asyncio
async def test_the_end_of_the_scenario_period_is_an_error() -> None:
    fake = FakeBoptest()
    plant = make_plant(fake)
    await plant.start(START)
    fake.period_over = True
    with pytest.raises(BoptestError, match="period is over"):
        await plant.step({})
    await plant.stop()


@pytest.mark.asyncio
async def test_scenario_at_the_period_start_initialize_elsewhere() -> None:
    fake = FakeBoptest()
    plant = make_plant(fake)
    period_start = CATALOG.period_start_s("peak_cool_day")
    first = await plant.start(
        PlantStartParams(start_time_s=period_start, step_s=900.0, time_period="peak_cool_day")
    )
    await plant.start(START.model_copy(update={"time_period": "peak_cool_day"}))  # a restart
    await plant.stop()
    assert first["time"] == period_start == 16_588_800
    assert fake.bodies(f"/scenario/{TESTID}") == [
        {"time_period": "peak_cool_day", "electricity_price": "constant"}
    ]
    assert fake.bodies(f"/initialize/{TESTID}") == [
        {"start_time": START.start_time_s, "warmup_period": START.warmup_s}
    ]
    assert fake.bodies(f"/step/{TESTID}") == [{"step": 900}, {"step": 900}]
    assert len(fake.calls(SELECT)) == 1  # a restart reuses the test


@pytest.mark.asyncio
async def test_keep_alive_pings_an_idle_test_until_stopped() -> None:
    fake = FakeBoptest()
    plant = make_plant(fake, keepalive_s=0.02)
    await plant.start(START)
    await asyncio.sleep(0.15)
    await plant.stop()
    pings = len(fake.calls(f"/name/{TESTID}"))
    await asyncio.sleep(0.06)
    assert pings >= 1
    assert len(fake.calls(f"/name/{TESTID}")) == pings


@pytest.mark.asyncio
async def test_keep_alive_waits_for_a_long_call_in_flight() -> None:
    fake = FakeBoptest()
    fake.delays["initialize"] = 0.3  # a warm-up six keep-alive periods long
    plant = make_plant(fake, keepalive_s=0.05)
    await plant.start(START)
    await plant.stop()
    assert fake.pings_while_busy == 0


@pytest.mark.asyncio
async def test_keep_alive_stays_quiet_while_calls_flow() -> None:
    fake = FakeBoptest()
    plant = make_plant(fake, keepalive_s=0.05)
    await plant.start(START)
    for _ in range(15):
        await plant.step({})
        await asyncio.sleep(0.01)
    await plant.stop()
    assert not fake.calls(f"/name/{TESTID}")


@pytest.mark.asyncio
async def test_a_cancelled_stop_still_frees_the_worker() -> None:
    fake = FakeBoptest()
    fake.delays["stop"] = 0.1
    plant = make_plant(fake)
    await plant.start(START)
    stopping = asyncio.ensure_future(plant.stop())
    await asyncio.sleep(0.02)  # the stop request is in flight
    stopping.cancel()  # a second Ctrl+C
    with pytest.raises(asyncio.CancelledError):
        await stopping
    await asyncio.sleep(0.2)
    assert fake.stopped == [TESTID]
    await plant.client.aclose()


@pytest.mark.asyncio
async def test_start_logs_its_call_latencies_on_one_line() -> None:
    fake = FakeBoptest()
    plant = make_plant(fake)
    records: list[Any] = []
    sink = logger.add(lambda message: records.append(message.record), level="INFO")
    try:
        await plant.start(START)
    finally:
        logger.remove(sink)
        await plant.stop()
    lines = [r for r in records if r["message"].startswith("BOPTEST start:")]
    assert len(lines) == 1
    assert lines[0]["level"].name == "INFO"
    assert lines[0]["message"].startswith(f"BOPTEST start: test={TESTID} select_s=")
    assert {"select_s", "initialize_s", "put_step_s"} <= lines[0]["extra"].keys()


@pytest.mark.asyncio
async def test_version_and_call_log() -> None:
    fake = FakeBoptest()
    client = BoptestClient(URL, transport=fake.transport())
    assert await client.version() == "0.9.0"
    assert [name for name, _ in client.calls] == ["GET version"]
    assert client.idle_s >= 0
    await client.aclose()


@pytest.mark.asyncio
async def test_results_ask_for_points_over_a_window_and_retry_a_transient_failure() -> None:
    fake = FakeBoptest()
    fake.time = 990.0  # stored up to here
    fake.once["results"] = [503]
    client = BoptestClient(URL, backoff_s=0.001, transport=fake.transport())
    samples = await client.results(TESTID, METERS[:2], 900.0, 960.0)
    await client.aclose()
    assert [request.method for request in fake.calls(RESULTS)] == ["PUT", "PUT"]  # one retry
    assert fake.calls(RESULTS)[-1].headers["content-type"] == "application/json"
    assert fake.bodies(RESULTS)[-1] == {
        "point_names": list(METERS[:2]),
        "start_time": 900.0,
        "final_time": 960.0,
    }
    assert samples == {"time": [900.0, 930.0, 960.0]} | dict.fromkeys(METERS[:2], [295.0] * 3)
    assert [name for name, _ in client.calls] == ["PUT results"] * 2


@pytest.mark.asyncio
async def test_results_cap_each_attempt_at_the_time_left_of_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "QUICK_TIMEOUT_S", 2.0)  # read at each call
    timeouts: list[float] = []

    async def busy_then_answer(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"]["read"])
        if len(timeouts) == 1:
            await asyncio.sleep(0.3)
            return answer(None, 503, "busy")
        return answer({"time": [0.0, 30.0], "p": [1.0, 1.0]})

    client = BoptestClient(URL, backoff_s=0.001, transport=httpx.MockTransport(busy_then_answer))
    assert await client.results(TESTID, ["p"], 0.0, 30.0) == {"time": [0.0, 30.0], "p": [1.0, 1.0]}
    await client.aclose()
    assert timeouts[0] == pytest.approx(2.0, abs=0.05)  # the whole budget
    assert timeouts[1] < 2.0 - 0.25  # what the first attempt left of it


def test_step_means_weigh_each_sample_by_the_time_it_covers() -> None:
    # An event point at 10 s: the power jumps from 5 W to 50 W and holds until 100 s.
    samples = {"time": [0.0, 10.0, 10.0, 100.0], "p": [5.0, 5.0, 50.0, 50.0]}
    assert step_means(samples, ["p"]) == {"p": pytest.approx(45.5)}  # the samples' mean: 27.5


@pytest.mark.asyncio
async def test_power_that_spikes_inside_a_step_is_metered_by_its_mean() -> None:
    fake = FakeBoptest()
    fake.power = spiky
    plant = make_plant(fake)
    meter = EnergyMeter()

    def total_kw(raw: dict[str, float]) -> float:
        return sum(raw[name] for name in METERS) / 1000

    initial = await plant.start(START)
    meter.add(START.start_time_s, total_kw(initial))  # instantaneous: never counted
    steps = [await plant.step({}) for _ in range(4)]
    end = fake.state()  # what BOPTEST measured at the end of the last step
    await plant.stop()
    for raw in steps:
        meter.add(raw["time"], total_kw(raw))
    assert fake.bodies(RESULTS)[-1] == {
        "point_names": list(METERS),
        "start_time": START.start_time_s + 3 * START.step_s,
        "final_time": START.start_time_s + 4 * START.step_s,
    }
    assert all(raw[name] == pytest.approx(SPIKE_MEAN_W) for raw in steps for name in METERS)
    # Only the meters change: every other value is the one at the end of the step.
    assert {name for name, value in steps[-1].items() if value != end[name]} == set(METERS)
    assert meter.kwh == pytest.approx(len(METERS) * SPIKE_MEAN_W / 1000)  # an hour of means
    # The meters at the ends of the steps miss every spike: 7.0 kWh, 35 % short.
    assert end[METERS[0]] == IDLE_W
    assert len(METERS) * IDLE_W / 1000 < 0.7 * meter.kwh
    assert plant.power_fallbacks == 0


def samples_at(times: list[float]) -> dict[str, list[Any]]:
    """A results payload: every meter at 1 kW at each time."""
    return {"time": times} | {name: [IDLE_W] * len(times) for name in METERS}


@pytest.mark.parametrize(
    ("status", "payload", "calls"),
    [
        (500, None, 5),  # retried until the attempts run out
        (400, None, 1),  # a client error is never retried
        (200, samples_at([0.0, 30.0]) | {METERS[0]: [IDLE_W, None]}, 1),  # NaN arrives as null
        (200, samples_at([0.0, 30.0]) | {METERS[0]: [IDLE_W]}, 1),
        (200, samples_at([30.0, 0.0]), 1),
        (200, samples_at([0.0]), 1),
        (200, samples_at([0.0, 0.0]), 1),
    ],
    ids=["server-error", "client-error", "null", "short", "backwards", "one-sample", "no-span"],
)
@pytest.mark.asyncio
async def test_without_means_a_step_keeps_the_meters_at_its_end_and_goes_on(
    status: int, payload: Any, calls: int
) -> None:
    fake = FakeBoptest()
    fake.power = spiky
    fake.results = payload
    if status != 200:
        fake.fail["results"] = status
    plant = make_plant(fake)
    warnings: list[str] = []
    sink = logger.add(lambda message: warnings.append(message.record["message"]), level="WARNING")
    try:
        await plant.start(START)
        raw = await plant.step({})
    finally:
        logger.remove(sink)
        await plant.stop()
    assert all(raw[name] == IDLE_W for name in METERS)  # the values at the end of the step
    assert plant.power_fallbacks == 1
    assert len(fake.calls(RESULTS)) == calls
    assert [w for w in warnings if w.startswith(f"BOPTEST: no power means for test {TESTID}")]
