"""Admission and drain bookkeeping, with one gate for every device operation.

Callers admit before scheduling work and finish in a finally block. Admission
outlives individual device sessions, so drain also waits for scans/conversion.
No lifecycle lock is held while waiting for the device or running the shim.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import Condition, Lock
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from device import DeviceIdentity


class DrainingError(RuntimeError):
    pass


class BusyError(RuntimeError):
    pass


@dataclass(frozen=True)
class OperationTicket:
    id: str
    kind: str
    device: DeviceIdentity | None = None
    job_id: str | None = None


class OperationCoordinator:
    def __init__(self) -> None:
        self._condition = Condition(Lock())
        self._device_gate = Lock()
        self._tickets: dict[str, OperationTicket] = {}
        self._sessions: dict[str, str] = {}
        self._draining = False

    def admit(self, kind, device=None, job_id=None) -> OperationTicket:
        with self._condition:
            if self._draining:
                raise DrainingError("The application is closing; new work is refused")
            if kind == "import" and any(t.kind == "import" for t in self._tickets.values()):
                raise BusyError("An import batch is already active")
            if device is not None:
                from device import DeviceIdentity
                if not isinstance(device, DeviceIdentity):
                    raise TypeError("device must be a captured DeviceIdentity")
            ticket = OperationTicket(uuid4().hex, kind, device, job_id)
            self._tickets[ticket.id] = ticket
            return ticket

    def _validate(self, ticket):
        if self._tickets.get(ticket.id) is not ticket:
            raise ValueError("Operation ticket is not admitted by this coordinator")

    @contextmanager
    def device_session(self, ticket):
        import device as device_module

        with self._condition:
            self._validate(ticket)
            if ticket.device is None:
                raise ValueError("Device session requires a captured device identity")
            if ticket.id in self._sessions:
                raise ValueError("Operation already has a device session")
            self._sessions[ticket.id] = "pending"
        try:
            with self._device_gate:
                with self._condition:
                    self._sessions[ticket.id] = "active"
                mount = device_module.revalidate_device(ticket.device)
                yield mount
                # A disconnect during work invalidates a nominal success too.
                try:
                    device_module.revalidate_device(ticket.device)
                except device_module.DeviceChangedError as exc:
                    exc.needs_reconcile = True
                    raise
        finally:
            with self._condition:
                self._sessions.pop(ticket.id, None)
                self._condition.notify_all()

    def finish(self, ticket) -> None:
        with self._condition:
            self._validate(ticket)
            if ticket.id in self._sessions:
                raise ValueError("Cannot finish an operation with an open device session")
            del self._tickets[ticket.id]
            self._condition.notify_all()

    def _snapshot(self) -> dict:
        return {
            "busy": bool(self._tickets),
            "draining": self._draining,
            "pending": sum(state == "pending" for state in self._sessions.values()),
            "active": [{"kind": t.kind, "job_id": t.job_id} for t in self._tickets.values()],
        }

    def snapshot(self) -> dict:
        with self._condition:
            return self._snapshot()

    def begin_drain(self) -> dict:
        with self._condition:
            self._draining = True
            self._condition.notify_all()
            return self._snapshot()

    def wait_drained(self, timeout=None) -> bool:
        with self._condition:
            return self._condition.wait_for(lambda: not self._tickets, timeout=timeout)
