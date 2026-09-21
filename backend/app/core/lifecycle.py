"""Request draining and bounded application lifecycle state."""

from __future__ import annotations

import asyncio
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from .exceptions import error_response


class ServiceLifecycleState:
    """Track readiness and in-flight application requests on one event loop."""

    def __init__(self) -> None:
        self.ready = False
        self.shutting_down = False
        self._active_tasks: set[asyncio.Task[Any]] = set()
        self._idle = asyncio.Event()
        self._idle.set()

    @property
    def active_requests(self) -> int:
        return len(self._active_tasks)

    def mark_ready(self) -> None:
        if not self.shutting_down:
            self.ready = True

    def begin_shutdown(self) -> None:
        self.shutting_down = True
        self.ready = False

    def begin_request(self) -> bool:
        if self.shutting_down:
            return False
        task = asyncio.current_task()
        if task is not None:
            self._active_tasks.add(task)
            self._idle.clear()
        return True

    def finish_request(self) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._active_tasks.discard(task)
        if not self._active_tasks:
            self._idle.set()

    async def drain(
        self,
        timeout_seconds: float = 20.0,
        cancellation_seconds: float = 1.0,
    ) -> None:
        """Wait for requests, then cancel any that exceed the drain budget."""
        self.begin_shutdown()
        try:
            await asyncio.wait_for(self._idle.wait(), timeout=timeout_seconds)
            return
        except TimeoutError:
            pass

        current = asyncio.current_task()
        overdue = [task for task in self._active_tasks if task is not current]
        for task in overdue:
            task.cancel()
        if overdue:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*overdue, return_exceptions=True),
                    timeout=cancellation_seconds,
                )
            except TimeoutError:
                # Cleanup must proceed inside the fixed shutdown budget even
                # when application code mishandles cancellation.
                return


class RequestDrainMiddleware:
    """Reject new application traffic after draining begins."""

    def __init__(self, app: ASGIApp, state: ServiceLifecycleState) -> None:
        self.app = app
        self._lifecycle = state

    def __getattr__(self, name: str) -> Any:
        return getattr(self.app, name)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in {"/health", "/ready"}:
            await self.app(scope, receive, send)
            return

        if not self._lifecycle.begin_request():
            response = error_response(
                status_code=503,
                code="SERVICE_UNAVAILABLE",
                message="Service temporarily unavailable",
                headers={"Retry-After": "1"},
            )
            await response(scope, receive, send)
            return

        try:
            await self.app(scope, receive, send)
        finally:
            self._lifecycle.finish_request()
