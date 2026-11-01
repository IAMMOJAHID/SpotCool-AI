"""BOPTEST client and plant against a fake BOPTEST behind httpx.MockTransport.

The fake behaves like the REST API measured on BOPTEST: test calls answer {status, message,
payload}; an empty body sent as JSON gets HTTP 500; an unknown input gets 400; inputs latch
until _activate = 0; overwritten signals come back under their _u keys; stop answers a plain
"OK", and a stopped test id then gets 400.
"""

import asyncio
import json
from typing import Any

import httpx
import pytest

from spotcool_contracts import Zone
from spotcool_world.services import PlantStartParams
from spotcool_world.services.boptest.catalog import (
    MANAGED_INPUTS,
    bms_signal,
    load_catalog,
    zone_damper,
)
from spotcool_world.services.boptest.client import BoptestClient, BoptestError
from spotcool_world.services.boptest.plant import BoptestPlant

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


def answer(payload: Any, status: int = 200, message: str = "ok") -> httpx.Response:
    return httpx.Response(status, json={"status": status, "message": message, "payload": payload})


class FakeBoptest:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.select_failures = 0
        self.queued_polls = 0
        self.fail: dict[str, int] = {}  # route ("advance", "initialize", ...) -> status
        self.period_over = False
        self.applied: dict[str, float] = {}
        self.time = 0.0
        self.step = 3600.0
        self.stopped: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def calls(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == path]

    def bodies(self, path: str) -> list[dict[str, Any]]:
        return [json.loads(r.content) if r.content else {} for r in self.calls(path)]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("content-type") == "application/json" and not request.content:
            return httpx.Response(500, text="Unexpected end of JSON input")
        path = request.url.path
        route = path.strip("/").split("/")[0]
        body = json.loads(request.content) if request.content else {}
        if route in self.fail:
            return answer(None, self.fail[route], f"{route} failed")
        if route == "version":
            return answer({"version": "0.9.0"})
        if route == "testcases":
            if path != SELECT:
                return httpx.Response(404, text="Not Found")
            if self.select_failures:
                self.select_failures -= 1
                return httpx.Response(503, text="Service Unavailable")
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
            return self.advance(body)
        if route == "kpi":
            kpis = dict.fromkeys(["cost_tot", "emis_tot", "idis_tot", "pele_tot", "tdis_tot"], 0.1)
            return answer(
                kpis | {"ener_tot": 0.2, "pdih_tot": None, "pgas_tot": None, "time_rat": 0.0}
            )
        if route == "name":
            return answer({"name": CATALOG.testcase})
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
        return answer(self.state())

    def state(self) -> dict[str, float | None]:
        state: dict[str, float | None] = {"time": self.time, "hvac_reaAhu_dp_sup_y": None}
        for name in CATALOG.outputs:
            if name.removesuffix("_y") + "_u" not in CATALOG.inputs:  # REST drops overwritten _y
                state.setdefault(name, 295.0)
        for name in CATALOG.inputs:
            state[name] = self.applied.get(name, 297.15)
            state[name.removesuffix("_u") + "_activate"] = float(name in self.applied)
        return state


def make_plant(fake: FakeBoptest, *, keepalive_s: float = 300.0) -> BoptestPlant:
    client = BoptestClient(URL, poll_s=0.0, backoff_s=0.001, transport=fake.transport())
    return BoptestPlant(client, keepalive_s=keepalive_s)


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
async def test_select_retries_transient_failures_and_waits_for_running() -> None:
    fake = FakeBoptest()
    fake.select_failures, fake.queued_polls = 2, 2
    client = BoptestClient(URL, poll_s=0.0, backoff_s=0.001, transport=fake.transport())
    assert await client.select(CATALOG.testcase) == TESTID
    assert (len(fake.calls(SELECT)), len(fake.calls(f"/status/{TESTID}"))) == (3, 3)
    await client.stop(TESTID)
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
    await plant.stop()
    await plant.stop()
    assert fake.stopped == [TESTID]
    assert [name for name, _ in plant.client.calls][-1] == "PUT stop"


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
async def test_version_and_call_log() -> None:
    fake = FakeBoptest()
    client = BoptestClient(URL, transport=fake.transport())
    assert await client.version() == "0.9.0"
    assert [name for name, _ in client.calls] == ["GET version"]
    assert client.idle_s >= 0
    await client.aclose()
