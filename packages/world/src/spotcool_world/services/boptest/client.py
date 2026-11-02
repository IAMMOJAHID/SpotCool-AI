"""BoptestClient: BOPTEST's HTTP API, with the service's quirks handled.

Rules it enforces (checked against BOPTEST master ``a29024f`` and the hosted v0.9.0):

- ``Content-Type: application/json`` only when there is a body: the hosted service answers
  HTTP 500 to an empty JSON body.
- Test calls answer ``{status, message, payload}``; any status but 200 raises ``BoptestError``.
  ``stop`` answers a plain-text ``OK``, so it is not parsed.
- ``advance`` is never retried here: an advance whose answer was lost may have run, and a
  retry would step the test twice (``BoptestPlant`` recovers such a step from the stored
  results). ``results`` and ``state_at`` change nothing, so they are retried on any transient
  failure.
- ``select`` uses ``select-async`` and polls ``status``, so the test id is known at once and a
  test that queues too long (the hosted queue has passed 900 s) can always be stopped. The
  POST is retried only when it provably created nothing (no connection, or HTTP 429): a
  duplicate test would hold a worker and starve the next run's select.
- ``stop`` rides out a short outage (``STOP_RETRY_S``), never raises, and a second Ctrl+C
  cannot abort it; callers use it on every exit path.
- A transport error names the exception, and a timeout the seconds it allowed.
"""

import asyncio
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, TypeVar

import httpx
from loguru import logger

QUICK_TIMEOUT_S = 30.0
"""Timeout of quick calls (select-async, status, step, results, name, version, stop)."""
SIMULATE_TIMEOUT_S = 900.0
"""Timeout of ``scenario`` and ``initialize``, which simulate the warm-up (``peak_cool_day``'s
seven days took 283 s on a Codespace)."""
ADVANCE_TIMEOUT_S = 120.0
"""Timeout of one ``advance``, and of the KPI calculation."""
STOP_RETRY_S = 60.0
"""How long ``stop`` keeps trying through connection failures, its attempts included."""
_BACKOFF_CAP_S = 30.0

_T = TypeVar("_T")


class BoptestError(RuntimeError):
    """A BOPTEST call failed.

    Args:
        message: What failed.
        status: HTTP or BOPTEST status code, or ``None`` when no answer came back.
        unsent: The request never reached BOPTEST (the connection failed), so it changed
            nothing there.
    """

    def __init__(self, message: str, *, status: int | None = None, unsent: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.unsent = unsent

    @property
    def retryable(self) -> bool:
        """Whether trying again may help: no answer at all, or an overloaded server."""
        return self.status is None or self.status == 429 or self.status >= 500


class BoptestClient:
    """Calls one BOPTEST service.

    Args:
        base_url: Service URL, e.g. ``http://127.0.0.1:8000``.
        select_wait_s: How long ``select`` waits for a queued test to start running.
        poll_s: Seconds between status polls while a test is queued.
        retries: Attempts at a select call: the POST on no connection or HTTP 429, a status
            poll on no answer or HTTP 429/5xx.
        backoff_s: First retry delay; it doubles with each attempt, with full jitter.
        transport: httpx transport; tests pass an ``httpx.MockTransport``.
        clock: Wall clock in seconds for the retry deadlines; tests inject a fake one.
        sleep: Waits between attempts and status polls; tests inject a fake one.
    """

    def __init__(
        self,
        base_url: str,
        *,
        select_wait_s: float = 600.0,
        poll_s: float = 2.0,
        retries: int = 5,
        backoff_s: float = 1.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url, timeout=QUICK_TIMEOUT_S, transport=transport
        )
        self._select_wait_s = select_wait_s
        self._poll_s = poll_s
        self._retries = retries
        self._backoff_s = backoff_s
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[tuple[str, float]] = deque(maxlen=256)
        self._last_request_s = time.perf_counter()
        self._in_flight = 0

    @property
    def base_url(self) -> str:
        """The service URL."""
        return str(self._http.base_url)

    @property
    def calls(self) -> Sequence[tuple[str, float]]:
        """The latest calls (up to 256) as ``(call, wall seconds)``, oldest first."""
        return tuple(self._calls)

    @property
    def idle_s(self) -> float:
        """Wall seconds since the last call finished; 0 while a call is in flight."""
        return 0.0 if self._in_flight else time.perf_counter() - self._last_request_s

    async def version(self, *, timeout_s: float = QUICK_TIMEOUT_S) -> str:
        """The service's BOPTEST version.

        Args:
            timeout_s: How long to wait for the answer.
        """
        payload = await self._payload("GET", "version", timeout_s=timeout_s)
        return str(_mapping(payload, "version").get("version"))

    async def select(self, testcase: str) -> str:
        """Start a test of ``testcase`` and wait until a worker runs it.

        Calls are retried with exponential backoff and full jitter: the POST only when it
        created nothing (no connection, or HTTP 429), status polls on any transient failure. A
        test still queued after ``select_wait_s`` is stopped.

        Args:
            testcase: Test case name.

        Returns:
            The running test's id.

        Raises:
            BoptestError: If no test is running in time; a queued test is stopped first.
        """
        deadline = self._clock() + self._select_wait_s
        try:
            answer = await self._retrying(
                lambda: self._json("POST", f"testcases/{testcase}/select-async"),
                deadline,
                retry=lambda exc: exc.unsent or exc.status == 429,
                attempts=self._retries,
            )
        except BoptestError as exc:
            if not exc.unsent and (exc.status is None or exc.status >= 500):
                raise BoptestError(
                    f"select {testcase}: {exc}; a test may have been created anyway, and "
                    "BOPTEST stops it after 15 idle minutes",
                    status=exc.status,
                ) from exc
            raise
        testid = answer.get("testid") if isinstance(answer, dict) else None
        if not isinstance(testid, str):
            raise BoptestError(f"select {testcase}: no testid in {str(answer)[:200]}", status=200)
        try:
            while True:
                status = await self._retrying(
                    lambda: self.status(testid),
                    deadline,
                    retry=lambda exc: exc.retryable,
                    attempts=self._retries,
                )
                if status == "Running":
                    break
                if self._clock() >= deadline:
                    raise BoptestError(
                        f"test {testid} still {status} after {self._select_wait_s:.0f} s; is a "
                        "worker free? A local stack runs as many tests as --scale worker=N"
                    )
                await self._sleep(self._poll_s)
        except BaseException:
            await asyncio.shield(self.stop(testid))
            raise
        logger.info(f"BOPTEST: test {testid} of {testcase} is running")
        return testid

    async def status(self, testid: str) -> str:
        """A test's status, ``Queued`` or ``Running``.

        Args:
            testid: The test.
        """
        return str(await self._json("GET", f"status/{testid}"))

    async def set_scenario(
        self, testid: str, *, time_period: str, electricity_price: str = "constant"
    ) -> dict[str, float]:
        """Set the scenario; BOPTEST re-initializes the test at the period's start.

        The period starts seven days before its day and lasts 14 days; BOPTEST first runs a
        seven-day warm-up, which takes minutes.

        Args:
            testid: The test.
            time_period: Scenario time period (e.g. ``peak_cool_day``).
            electricity_price: ``constant``, ``dynamic`` or ``highly_dynamic``.

        Returns:
            The measurements at the start of the period.
        """
        body = {"time_period": time_period, "electricity_price": electricity_price}
        payload = await self._payload(
            "PUT", f"scenario/{testid}", body, timeout_s=SIMULATE_TIMEOUT_S
        )
        return _state(_mapping(payload, "scenario").get("time_period"), "scenario")

    async def initialize(
        self, testid: str, start_time_s: float, warmup_s: float
    ) -> dict[str, float]:
        """Initialize the test at a start time after a warm-up; resets its KPIs.

        Args:
            testid: The test.
            start_time_s: Simulation time to start at.
            warmup_s: Simulated seconds to run before it.

        Returns:
            The measurements at ``start_time_s``.
        """
        body = {"start_time": start_time_s, "warmup_period": warmup_s}
        payload = await self._payload(
            "PUT", f"initialize/{testid}", body, timeout_s=SIMULATE_TIMEOUT_S
        )
        return _state(payload, "initialize")

    async def set_step(self, testid: str, step_s: float) -> None:
        """Set the simulation seconds each ``advance`` covers.

        Args:
            testid: The test.
            step_s: Seconds per step.
        """
        await self._payload("PUT", f"step/{testid}", {"step": step_s})

    async def advance(
        self, testid: str, inputs: Mapping[str, float], *, timeout_s: float = ADVANCE_TIMEOUT_S
    ) -> dict[str, float]:
        """Advance one step.

        Args:
            testid: The test.
            inputs: ``<x>_u`` values and ``<x>_activate`` flags. Inputs latch: BOPTEST keeps
                applying an override until it is sent ``<x>_activate = 0``.
            timeout_s: How long to wait for the answer.

        Returns:
            The measurements at the end of the step; empty once the scenario period is over.
        """
        payload = await self._payload("POST", f"advance/{testid}", inputs, timeout_s=timeout_s)
        return _state(payload, "advance")

    async def results(
        self, testid: str, point_names: Sequence[str], start_time_s: float, final_time_s: float
    ) -> dict[str, list[float]]:
        """Stored trajectories of some points between two times (``PUT /results``).

        BOPTEST stores every point each 30 simulated seconds and at each step's end. Any
        transient failure is retried with backoff. ``QUICK_TIMEOUT_S`` bounds the whole call:
        each attempt's timeout is capped at the time left, and no retry starts after it.

        Args:
            testid: The test.
            point_names: Outputs (``_y``) or inputs (``_u``) to read.
            start_time_s: First simulation time, included.
            final_time_s: Last simulation time, included.

        Returns:
            Each point's samples and, under ``time``, their times, oldest first.

        Raises:
            BoptestError: If BOPTEST fails, or a series is not all numbers (a NaN arrives as
                null).
        """
        stored = await self._stored(testid, point_names, start_time_s, final_time_s)
        series: dict[str, list[float]] = {}
        for name, values in stored.items():
            if not isinstance(values, list) or not all(isinstance(v, int | float) for v in values):
                raise BoptestError(f"results: {name} is not a list of numbers", status=200)
            series[name] = [float(value) for value in values]
        return series

    async def state_at(
        self, testid: str, point_names: Sequence[str], time_s: float
    ) -> dict[str, float]:
        """Stored values of some points at one time: what an advance that ended then answered.

        Reads ``PUT /results`` over ``[time_s, time_s]``, retried like ``results``. BOPTEST
        filters its stored samples without interpolating, so the values are its own. A value
        it reports as null is left out, as ``advance`` leaves it out.

        Args:
            testid: The test.
            point_names: Outputs (``_y``) and inputs (``_u``, ``_activate``) to read.
            time_s: A simulation time BOPTEST has stored.

        Returns:
            Each point's value and, under ``time``, ``time_s``.

        Raises:
            BoptestError: If BOPTEST fails, or holds no single sample at ``time_s``.
        """
        stored = await self._stored(testid, point_names, time_s, time_s)
        if stored.get("time") != [time_s]:
            times = str(stored.get("time"))[:200]
            raise BoptestError(f"results: no single sample at {time_s} s, got {times}", status=200)
        last = {name: v[-1] for name, v in stored.items() if isinstance(v, list) and len(v) == 1}
        return _state(last, "results")

    async def kpis(self, testid: str) -> dict[str, float | None]:
        """The test's KPIs since it was initialized (``ener_tot``, ``tdis_tot``, ...).

        Args:
            testid: The test.
        """
        payload = await self._payload("GET", f"kpi/{testid}", timeout_s=ADVANCE_TIMEOUT_S)
        return {
            name: float(value) if isinstance(value, int | float) else None
            for name, value in _mapping(payload, "kpi").items()
        }

    async def inputs(self, testid: str) -> dict[str, Any]:
        """The test's input catalog, input name to metadata.

        Args:
            testid: The test.
        """
        return dict(_mapping(await self._payload("GET", f"inputs/{testid}"), "inputs"))

    async def measurements(self, testid: str) -> dict[str, Any]:
        """The test's measurement catalog, output name to metadata.

        Args:
            testid: The test.
        """
        payload = await self._payload("GET", f"measurements/{testid}")
        return dict(_mapping(payload, "measurements"))

    async def keep_alive(self, testid: str) -> None:
        """Reset the test's idle timer (BOPTEST stops a test after 15 idle minutes).

        Only calls that reach the test's worker count; ``status`` does not.

        Args:
            testid: The test.
        """
        await self._payload("GET", f"name/{testid}")

    async def stop(self, testid: str) -> None:
        """Stop a test, queued or running, and free its worker. Never raises; failures are logged.

        No answer and HTTP 429/5xx are retried with backoff for up to ``STOP_RETRY_S`` in all:
        each attempt's timeout is capped at the time left. A retry that finds the test gone
        means an earlier request stopped it. A test still running afterwards is logged as a
        warning with the command that stops it.

        Args:
            testid: The test.
        """
        deadline = self._clock() + STOP_RETRY_S
        attempts = 0

        def attempt() -> Awaitable[httpx.Response]:
            nonlocal attempts
            attempts += 1
            left_s = min(QUICK_TIMEOUT_S, max(deadline - self._clock(), 1e-3))
            return self._request("PUT", f"stop/{testid}", timeout_s=left_s)

        try:
            await self._retrying(attempt, deadline, retry=lambda exc: exc.retryable, attempts=None)
        except BoptestError as exc:
            if attempts > 1 and exc.status in (400, 404):  # BOPTEST no longer knows the test
                logger.info(f"BOPTEST: stopped test {testid} (an earlier request got through)")
                return
            command = f"curl -X PUT {self._http.base_url.join(f'stop/{testid}')}"
            logger.warning(
                f"BOPTEST: could not stop test {testid}: {exc}. It holds a worker until BOPTEST "
                f"stops it after 15 idle minutes; stop it now with: {command}"
            )
        else:
            logger.info(f"BOPTEST: stopped test {testid}")

    async def aclose(self) -> None:
        """Close the connection pool. Safe to call twice."""
        await self._http.aclose()

    async def _stored(
        self, testid: str, point_names: Sequence[str], start_time_s: float, final_time_s: float
    ) -> Mapping[str, Any]:
        """``PUT /results``'s payload, retried like ``results``."""
        body = {
            "point_names": list(point_names),
            "start_time": start_time_s,
            "final_time": final_time_s,
        }
        deadline = self._clock() + QUICK_TIMEOUT_S

        def attempt() -> Awaitable[Any]:
            left_s = max(deadline - self._clock(), 1e-3)
            return self._payload("PUT", f"results/{testid}", body, timeout_s=left_s)

        payload = await self._retrying(
            attempt, deadline, retry=lambda exc: exc.retryable, attempts=self._retries
        )
        return _mapping(payload, "results")

    async def _retrying(
        self,
        call: Callable[[], Awaitable[_T]],
        deadline: float,
        *,
        retry: Callable[[BoptestError], bool],
        attempts: int | None,
    ) -> _T:
        """``call()`` until it succeeds, retrying the failures ``retry`` accepts.

        Each retry follows a backoff (``backoff_s``). There are at most ``attempts`` calls in
        all (``None``: no limit), and no retry whose backoff would end past ``deadline``.
        """
        attempt = 0
        while True:
            try:
                return await call()
            except BoptestError as exc:
                attempt += 1
                delay = backoff_s(attempt, self._backoff_s)
                late = self._clock() + delay > deadline
                if not retry(exc) or (attempts is not None and attempt >= attempts) or late:
                    raise
                logger.warning(f"BOPTEST: {exc}; retry {attempt} in {delay:.1f} s")
                await self._sleep(delay)

    async def _payload(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        *,
        timeout_s: float = QUICK_TIMEOUT_S,
    ) -> Any:
        data = await self._json(method, path, body, timeout_s=timeout_s)
        if not isinstance(data, dict) or "payload" not in data:
            raise BoptestError(f"{method} /{path}: unexpected answer {str(data)[:200]}", status=200)
        if data.get("status") != 200:
            raise BoptestError(
                f"{method} /{path}: {data.get('message')}", status=data.get("status")
            )
        return data["payload"]

    async def _json(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        *,
        timeout_s: float = QUICK_TIMEOUT_S,
    ) -> Any:
        response = await self._request(method, path, body, timeout_s=timeout_s)
        try:
            return response.json()
        except ValueError as exc:
            raise BoptestError(
                f"{method} /{path}: not JSON: {response.text[:200]}", status=200
            ) from exc

    async def _request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        *,
        timeout_s: float = QUICK_TIMEOUT_S,
    ) -> httpx.Response:
        started = time.perf_counter()
        self._in_flight += 1
        try:
            # httpx sends Content-Type: application/json only with json=; an empty body gets
            # none, because the hosted service answers HTTP 500 to an empty JSON body.
            response = await self._http.request(
                method, path, json=dict(body) if body else None, timeout=timeout_s
            )
        except httpx.HTTPError as exc:
            # Without a connection the request never reached BOPTEST, so it changed nothing.
            unsent = isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout)
            failure = _failure(exc, timeout_s)
            raise BoptestError(f"{method} /{path}: {failure}", unsent=unsent) from exc
        finally:
            self._in_flight -= 1
            self._last_request_s = time.perf_counter()
            elapsed = self._last_request_s - started
            self._calls.append((_call_name(method, path), elapsed))
            logger.debug(f"BOPTEST {method} /{path}: {elapsed * 1000:.0f} ms")
        if response.status_code != 200:
            raise BoptestError(
                f"{method} /{path}: HTTP {response.status_code}: {_message(response)}",
                status=response.status_code,
            )
        return response


def backoff_s(attempt: int, first_s: float) -> float:
    """The delay before a retry: exponential from ``first_s``, capped at 30 s, full jitter.

    Args:
        attempt: How many attempts have failed so far.
        first_s: The longest delay before the first retry.
    """
    return random.uniform(0.0, min(_BACKOFF_CAP_S, first_s * 2 ** (attempt - 1)))


def _failure(exc: httpx.HTTPError, timeout_s: float) -> str:
    """What failed on the wire. httpx's timeouts carry no message, so they name the timeout."""
    name = type(exc).__name__
    if isinstance(exc, httpx.TimeoutException):
        return f"{name} after {timeout_s:.3g} s"
    return f"{name}: {exc}" if str(exc) else name


def _call_name(method: str, path: str) -> str:
    parts = path.split("/")
    return f"{method} {parts[-1] if parts[0] == 'testcases' else parts[0]}"


def _message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:300]
    return str(data.get("message", data) if isinstance(data, dict) else data)[:300]


def _mapping(payload: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(payload, dict):
        raise BoptestError(f"{what}: expected an object, got {str(payload)[:200]}", status=200)
    return payload


def _state(payload: Any, what: str) -> dict[str, float]:
    # BOPTEST's web service is Node.js: a NaN arrives as null, and such values are left out.
    return {
        name: float(value)
        for name, value in _mapping(payload, what).items()
        if isinstance(value, int | float)
    }
