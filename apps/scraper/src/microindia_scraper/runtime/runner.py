"""The runner loop: lease a task of my kinds, borrow a tab, call the handler, apply the result.

Crash-only: if the browser connection dies the runner exits non-zero and the
supervisor restarts it; on start it requeues whatever it still had leased.
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
from .results import AuthBlocked, AuthRequired, Result, Retry
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
        log(event="runner_start", runner=self.runner_id, kinds=self.kinds, tabs=self.tabs, released_leases=released)
        if self.needs_pages:
            await self.session.start()
            ledger = os.path.join(self.state_dir, f"tabs-{re.sub(r'[^A-Za-z0-9_.-]', '_', self.runner_id)}.json")
            self.pool = TabPool(self.session, self.tabs, ledger=ledger)
            closed = await self.pool.start()
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
        finally:
            status = "crashed" if self._fatal else "stopped"
            self.tasks.heartbeat(self.runner_id, kinds=self.kinds, tabs=self.tabs, status=status,
                                 processed=self.processed, last_error=repr(self._fatal) if self._fatal else None)
            if self.pool is not None:
                try:
                    await self.pool.stop()
                except Exception:
                    pass
            await self.session.stop()
        if self._fatal:
            raise self._fatal

    def stop(self, error: Optional[BaseException] = None) -> None:
        if error is not None and self._fatal is None:
            self._fatal = error
        self._stop.set()

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    # -- work slots -----------------------------------------------------------

    async def _slot(self, index: int) -> None:
        while not self._stop.is_set():
            floors = self.gate(self.tasks) if self.gate else {}
            if self.tasks.auth_blocked():
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
        if spec is None:
            result: Result = Retry(f"no handler registered for {task['kind']}", after_seconds=300)
        else:
            try:
                if spec.needs_page:
                    page = await self.pool.acquire()
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
                    if replace_page:
                        try:
                            await self.pool.replace(page)
                        except Exception as exc:
                            self.stop(BrowserDead(f"could not replace tab: {exc!r}"))
                    else:
                        try:
                            await self.pool.finish(page)
                        except Exception as exc:
                            self.stop(BrowserDead(f"could not recycle tab: {exc!r}"))
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

    # -- housekeeping -----------------------------------------------------------

    async def _housekeeping(self) -> None:
        while not self._stop.is_set():
            try:
                for scheduler in self.schedulers:
                    scheduler(self.tasks)
                if self.needs_pages:
                    await self._check_browser()
                    if self.tasks.auth_blocked():
                        await self._maybe_clear_auth()
                self.tasks.heartbeat(
                    self.runner_id,
                    kinds=self.kinds,
                    tabs=self.tabs,
                    status="auth_blocked" if self.tasks.auth_blocked() else "running",
                    processed=self.processed,
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
