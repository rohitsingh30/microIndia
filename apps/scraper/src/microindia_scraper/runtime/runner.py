"""The runner loop: lease a task of my kinds, borrow a tab, call the handler, apply the result.

Crash-only: if the browser connection dies the runner exits non-zero and the
supervisor restarts it; on start it requeues whatever it still had leased.

Nothing a slot awaits may hang forever: tab operations have timeouts, a runner
whose own leases expire knows its slots are stuck and exits, and a stop that
cannot finish ends the process after ``hard_exit_seconds``. A lost network is a
pause (``Offline``), not a failed attempt.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import time
from typing import Any, Callable, Dict, List, Optional

from .browser import BrowserSession, TabPool
from .page import INSTAGRAM_ORIGIN, open_url, page_shows_login
from .registry import TaskContext, get_handler
from .pacing import Throttled
from .results import AuthBlocked, AuthRequired, Offline, Result, Retry, is_offline_error
from .tasks import TaskStore

Scheduler = Callable[[TaskStore], Any]


def log(**event: Any) -> None:
    print(json.dumps({"ts": round(time.time(), 3), **event}, sort_keys=True, default=str), flush=True)


class BrowserDead(RuntimeError):
    pass


class Runner:
    def __init__(
        self,
        *,
        tasks: TaskStore,
        session: BrowserSession,
        kinds: List[str],
        tabs: int,
        database: str,
        runner_id: str,
        poll_seconds: float = 2.0,
        lease_seconds: float = 900.0,
        housekeeping_seconds: float = 15.0,
        auth_check_seconds: float = 60.0,
        schedulers: Optional[List[Scheduler]] = None,
        max_tasks: int = 0,
        state_dir: str = "run/tabs",
        gate: Optional[Callable[[TaskStore], Dict[str, int]]] = None,
        tab_op_timeout: float = 60.0,
        acquire_timeout: float = 180.0,
        stuck_lease_grace: float = 120.0,
        hard_exit_seconds: Optional[float] = None,
        start_timeout: float = 120.0,
        network_probe: Optional[Callable[[], Any]] = None,
    ) -> None:
        if not kinds:
            raise ValueError("runner has no task kinds to work on")
        self.tasks = tasks
        self.session = session
        self.kinds = kinds
        self.tabs = tabs
        self.database = database
        self.runner_id = runner_id
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self.housekeeping_seconds = housekeeping_seconds
        self.auth_check_seconds = auth_check_seconds
        self.schedulers = schedulers or []
        self.max_tasks = max_tasks
        self.state_dir = state_dir
        self.gate = gate  # returns {kind: minimum priority allowed right now} (backpressure)
        self.tab_op_timeout = tab_op_timeout
        self.acquire_timeout = acquire_timeout
        self.stuck_lease_grace = stuck_lease_grace
        self.hard_exit_seconds = hard_exit_seconds  # None in tests; set by run.py for real workers
        self.start_timeout = start_timeout
        self.network_probe = network_probe or _instagram_reachable
        self.processed = 0
        self.pool: Optional[TabPool] = None
        self._stop = asyncio.Event()
        self._fatal: Optional[BaseException] = None
        self._last_auth_check = 0.0

    @property
    def needs_pages(self) -> bool:
        return any(get_handler(kind) and get_handler(kind).needs_page for kind in self.kinds)

    async def run(self) -> None:
        released = self.tasks.release_leases(self.runner_id)
        # Heartbeat before touching the browser, so a hang during startup is visible to the watchdog.
        self.tasks.heartbeat(self.runner_id, kinds=self.kinds, tabs=self.tabs, status="starting", processed=0)
        log(event="runner_start", runner=self.runner_id, kinds=self.kinds, tabs=self.tabs, released_leases=released)
        if self.needs_pages:
            try:
                await asyncio.wait_for(self.session.start(), timeout=self.start_timeout)
                ledger = os.path.join(self.state_dir, f"tabs-{re.sub(r'[^A-Za-z0-9_.-]', '_', self.runner_id)}.json")
                self.pool = TabPool(self.session, self.tabs, ledger=ledger)
                closed = await asyncio.wait_for(self.pool.start(), timeout=self.start_timeout)
            except asyncio.TimeoutError as exc:
                raise BrowserDead(f"browser did not start within {self.start_timeout:.0f}s") from exc
            if closed:
                log(event="closed_leftover_tabs", runner=self.runner_id, count=len(closed))
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                # Graceful stop on supervisor restarts: finish the loop, close our tabs.
                loop.add_signal_handler(signum, self.stop)
            except (NotImplementedError, RuntimeError):
                pass
        try:
            await asyncio.gather(
                *(self._slot(index) for index in range(self.tabs)),
                self._housekeeping(),
            )
        except BaseException as exc:
            # Anything escaping a slot (a locked database, a handler returning garbage) must stop
            # every slot and arm the hard exit, or the process can linger without working.
            self.stop(None if isinstance(exc, asyncio.CancelledError) else exc)
            raise
        finally:
            status = "crashed" if self._fatal else "stopped"
            try:
                self.tasks.heartbeat(self.runner_id, kinds=self.kinds, tabs=self.tabs, status=status,
                                     processed=self.processed, last_error=repr(self._fatal) if self._fatal else None)
            except Exception:
                pass
            if self.pool is not None:
                try:
                    await asyncio.wait_for(self.pool.stop(), timeout=self.tab_op_timeout)
                except Exception:
                    pass
            try:
                await asyncio.wait_for(self.session.stop(), timeout=self.tab_op_timeout)
            except Exception:
                pass
        if self._fatal:
            raise self._fatal

    def stop(self, error: Optional[BaseException] = None) -> None:
        if error is not None and self._fatal is None:
            self._fatal = error
        if not self._stop.is_set() and self.hard_exit_seconds:
            # A slot stuck in a browser call would keep the process alive forever; the
            # supervisor only restarts processes that exit.
            try:
                asyncio.get_running_loop().call_later(self.hard_exit_seconds, self._hard_exit)
            except RuntimeError:
                pass
        self._stop.set()

    def _hard_exit(self) -> None:
        code = 1 if self._fatal else 0
        if not self._fatal:
            # A normal stop (deploy, /restart-worker) cut these tasks short: not their fault, so
            # give the attempt back. After a crash or stuck slots the attempt stays charged, so a
            # task that keeps killing its runner still stops at max_attempts.
            try:
                refunded = self.tasks.refund_leases(self.runner_id)
                log(event="leases_refunded", runner=self.runner_id, count=refunded)
            except Exception:
                pass
        log(event="runner_hard_exit", runner=self.runner_id, code=code)
        os._exit(code)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    # -- work slots -----------------------------------------------------------

    async def _slot(self, index: int) -> None:
        while not self._stop.is_set():
            floors = self.gate(self.tasks) if self.gate else {}
            if self.tasks.auth_blocked() or self.tasks.network_down():
                await self._sleep(max(self.poll_seconds, 5.0))
                continue
            task = self.tasks.lease(self.runner_id, self.kinds, lease_seconds=self.lease_seconds, priority_floor=floors)
            if task is None:
                await self._sleep(self.poll_seconds)
                continue
            await self.run_task(task, slot=index)
            self.processed += 1
            if self.max_tasks and self.processed >= self.max_tasks:
                self.stop()

    async def run_task(self, task: dict, *, slot: int = 0) -> str:
        spec = get_handler(task["kind"])
        started = time.time()
        page = None
        replace_page = False
        if spec is not None and spec.timeout_seconds + self.stuck_lease_grace > self.lease_seconds:
            # A long handler holds its lease for its whole timeout, so a slow task never looks stuck.
            self.tasks.renew(task, self.runner_id, lease_seconds=spec.timeout_seconds + self.stuck_lease_grace)
        if spec is None:
            result: Result = Retry(f"no handler registered for {task['kind']}", after_seconds=300)
        elif spec.needs_page and (page := await self._acquire_tab()) is None:
            result = Retry("no free tab in time; browser looks stuck", after_seconds=60)
        else:
            try:
                context = TaskContext(
                    page=page,
                    browser=self.session,
                    database=self.database,
                    tasks=self.tasks,
                    log=lambda **event: log(runner=self.runner_id, task_id=task["task_id"], **event),
                )
                result = await asyncio.wait_for(spec.fn(context, task), timeout=spec.timeout_seconds)
            except AuthRequired as exc:
                result = AuthBlocked(str(exc))
            except Throttled as exc:
                result = Retry(f"throttled: {exc}", after_seconds=180)
                self.tasks.connection.execute(
                    "UPDATE tasks SET attempts = MAX(0, attempts - 1) WHERE task_id = ?", (task["task_id"],))
                self.tasks.connection.commit()
            except asyncio.TimeoutError:
                result = Retry(f"timed out after {spec.timeout_seconds:.0f}s")
                replace_page = True
            except Exception as exc:
                result = Retry(f"{type(exc).__name__}: {exc}")
                replace_page = True
            finally:
                if page is not None:
                    operation, verb = (self.pool.replace, "replace") if replace_page else (self.pool.finish, "recycle")
                    try:
                        await asyncio.wait_for(operation(page), timeout=self.tab_op_timeout)
                    except Exception as exc:
                        self.stop(BrowserDead(f"could not {verb} tab: {exc!r}"))
        if isinstance(result, Retry) and is_offline_error(result.reason) and not await self._network_ok():
            # Only a confirmed outage refunds the attempt and pauses everyone; if Instagram still
            # answers, the error was this task's own and counts as a normal retry.
            result = Offline(result.reason)
        state = self.tasks.apply(task, self.runner_id, result, duration=time.time() - started)
        log(
            event="task_finished",
            runner=self.runner_id,
            slot=slot,
            task_id=task["task_id"],
            kind=task["kind"],
            key=task["key"],
            attempt=task["attempts"],
            outcome=type(result).__name__,
            state=state,
            seconds=round(time.time() - started, 2),
            reason=getattr(result, "reason", None),
            follow_ups=len(getattr(result, "follow_ups", []) or []),
        )
        return state

    async def _acquire_tab(self) -> Any:
        try:
            return await asyncio.wait_for(self.pool.acquire(), timeout=self.acquire_timeout)
        except Exception as exc:
            self.stop(BrowserDead(f"could not get a tab within {self.acquire_timeout:.0f}s: {exc!r}"))
            return None

    # -- housekeeping -----------------------------------------------------------

    async def _housekeeping(self) -> None:
        while not self._stop.is_set():
            try:
                for scheduler in self.schedulers:
                    scheduler(self.tasks)
                if self.tasks.network_down():
                    await self._maybe_clear_network()
                stuck = self.tasks.expired_leases(self.runner_id, grace_seconds=self.stuck_lease_grace)
                if stuck:
                    raise BrowserDead(f"{stuck} task(s) held past their lease: slots are stuck")
                if self.needs_pages and not self.tasks.network_down():
                    await self._check_browser()
                    if self.tasks.auth_blocked():
                        await self._maybe_clear_auth()
                paused = "auth_blocked" if self.tasks.auth_blocked() else "network_down" if self.tasks.network_down() else None
                floors = self.gate(self.tasks) if self.gate else {}
                self.tasks.heartbeat(
                    self.runner_id,
                    kinds=self.kinds,
                    tabs=self.tabs,
                    status=paused or "running",
                    processed=self.processed,
                    due=0 if paused else self.tasks.due_count(self.kinds, priority_floor=floors, runner_id=self.runner_id),
                )
            except BrowserDead as exc:
                log(event="browser_dead", runner=self.runner_id, error=repr(exc))
                self.stop(exc)
                return
            except Exception as exc:
                log(event="housekeeping_error", runner=self.runner_id, error=repr(exc))
            await self._sleep(self.housekeeping_seconds)

    async def _check_browser(self) -> None:
        try:
            await asyncio.wait_for(self.session.ping(), timeout=20)
        except Exception as exc:
            raise BrowserDead(repr(exc)) from exc

    async def _network_ok(self) -> bool:
        try:
            return bool(await self.network_probe())
        except Exception:
            return False

    async def _maybe_clear_network(self) -> None:
        reachable = await self._network_ok()
        if reachable:
            made_due = self.tasks.clear_network_down()
            log(event="network_back", runner=self.runner_id, made_due=made_due)
        else:
            log(event="network_down", runner=self.runner_id, reason=self.tasks.network_down())

    async def _maybe_clear_auth(self) -> None:
        if time.time() - self._last_auth_check < self.auth_check_seconds:
            return
        self._last_auth_check = time.time()
        page = await self.pool.acquire()
        try:
            try:
                await open_url(page, INSTAGRAM_ORIGIN + "/")
                blocked = await page_shows_login(page)
            except AuthRequired:
                blocked = True
        finally:
            self.pool.release(page)
        if blocked:
            log(event="auth_still_blocked", runner=self.runner_id, reason=self.tasks.auth_blocked())
            return
        requeued = self.tasks.clear_auth_blocked()
        log(event="auth_cleared", runner=self.runner_id, requeued=requeued)


async def _instagram_reachable(host: str = "www.instagram.com", port: int = 443, timeout: float = 5.0) -> bool:
    """Cheap connectivity check: can we open a TCP connection to Instagram right now?"""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return True

