from contextlib import AbstractContextManager
from dataclasses import replace
import re
from typing import Callable, Protocol

from workflow.model import Failure, Reception, Saga, STEPS, identifier, invalid


class StepError(Exception):
    def __init__(self, code: str, retryable: bool):
        super().__init__(code)
        self.code, self.retryable = code, retryable


class Store(Protocol):
    def create(self, actor: str, key: str, request: Reception, trace: str) -> tuple[Saga, bool]: ...
    def get(self, saga_id: str) -> Saga: ...
    def save(self, saga: Saga) -> None: ...
    def exclusive(self, saga_id: str) -> AbstractContextManager: ...


class Participants(Protocol):
    def forward(self, step: str, saga: Saga, key: str, service_token: str) -> dict: ...


class Runner:
    def __init__(self, store: Store, participants: Participants, service_token: Callable[[], str]):
        self.store, self.participants, self.service_token = store, participants, service_token

    @staticmethod
    def authorize(actor, roles, write):
        identifier(actor, "actor")
        allowed = {"ADMIN", "INVENTORY"} if write else {"ADMIN", "INVENTORY", "SUPERVISOR"}
        if not allowed.intersection(roles):
            raise Failure(403, "FORBIDDEN")

    def start(self, actor, roles, key, body, trace):
        self.authorize(actor, roles, True)
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", key):
            raise invalid("Idempotency-Key")
        if not isinstance(trace, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", trace):
            raise invalid("X-Correlation-Id")
        saga, created = self.store.create(actor, key, Reception.parse(body), trace)
        # Replay de inicio no reejecuta pasos: recuperar usa un comando separado.
        return (self._run(saga.id) if created else saga), created

    def get(self, actor, roles, saga_id, write=False):
        self.authorize(actor, roles, write)
        saga = self.store.get(identifier(saga_id, "id"))
        if saga.actor != actor and "ADMIN" not in roles and not (not write and "SUPERVISOR" in roles):
            raise Failure(403, "FORBIDDEN")
        return saga

    def resume(self, actor, roles, saga_id):
        self.get(actor, roles, saga_id, write=True)
        return self._run(saga_id)

    def compensate(self, actor, roles, saga_id):
        self.get(actor, roles, saga_id, write=True)
        if "ADMIN" not in roles:
            raise Failure(403, "FORBIDDEN")
        return self._run(saga_id, compensate=True)

    def _run(self, saga_id, compensate=False):
        with self.store.exclusive(saga_id):
            saga = self.store.get(saga_id)
            if (saga.status not in {"RUNNING", "COMPLETED", "FAILED", "COMPENSATED"} or saga.completed != STEPS[:len(saga.completed)]
                    or len(saga.completed) > len(STEPS)
                    or (saga.status == "COMPLETED") != (len(saga.completed) == len(STEPS))
                    or bool(saga.receipt_id) != (len(saga.completed) >= 1)
                    or bool(saga.entry_id) != (len(saga.completed) >= 2)
                    or (saga.status == "FAILED" and not saga.failed_step)
                    or (saga.failed_step is not None and saga.failed_step not in STEPS[len(saga.completed):len(saga.completed)+1])):
                raise Failure(503, "INVALID_SAGA_STATE")
            plan = saga.undo_plan()
            if (saga.compensated != plan[:len(saga.compensated)] or len(saga.compensated) > len(plan)
                    or (not saga.compensating and (saga.compensated or saga.undo_failed_step or saga.status == "COMPENSATED"))
                    or (saga.compensating and (not saga.failed_step or len(saga.completed) == 4))
                    or (saga.status == "COMPENSATED" and (saga.compensated != plan or saga.undo_failed_step))
                    or (saga.undo_failed_step is not None and saga.undo_failed_step not in plan[len(saga.compensated):len(saga.compensated)+1])
                    or ((saga.undo_failed_step is None) != (saga.undo_failure_code is None))):
                raise Failure(503, "INVALID_SAGA_STATE")
            try:
                for value in (saga.receipt_id, saga.entry_id):
                    if value is not None:
                        identifier(value, "participantId")
            except Failure:
                raise Failure(503, "INVALID_SAGA_STATE") from None
            if compensate:
                if saga.status == "COMPENSATED":
                    return saga
                if saga.status != "FAILED" or not saga.compensating:
                    raise Failure(409, "COMPENSATION_NOT_ALLOWED")
                saga = replace(saga, status="RUNNING", compensating=True, undo_failed_step=None, undo_failure_code=None)
                self.store.save(saga)
            if saga.compensating and saga.status == "RUNNING":
                return self._undo(saga)
            if saga.status in {"COMPLETED", "FAILED", "COMPENSATED"}:
                return saga
            for step in STEPS[len(saga.completed):]:
                try:
                    token = self.service_token()
                    if not token:
                        raise StepError("SERVICE_IDENTITY_UNAVAILABLE", True)
                    response = self.participants.forward(step, saga, saga.id + ":" + step, token)
                    expected = {"prepare-receipt": "PENDING", "prepare-entry": "PENDING",
                                "confirm-receipt": "CONFIRMED", "activate-entry": "ACTIVE"}[step]
                    try:
                        result_id = identifier(response.get("id"), "participantId")
                        expected_id = saga.receipt_id if step == "confirm-receipt" else saga.entry_id if step == "activate-entry" else result_id
                        if response.get("status") != expected or result_id != expected_id:
                            raise ValueError()
                    except (AttributeError, Failure, ValueError):
                        raise StepError("INVALID_PARTICIPANT_RESPONSE", True) from None
                except StepError as error:
                    code = error.code if re.fullmatch(r"[A-Z_]{1,40}", error.code) else "PARTICIPANT_FAILURE"
                    saga = replace(saga, status="RUNNING", failed_step=step, failure_code=code, compensating=not error.retryable)
                    self.store.save(saga)
                    return self._undo(saga) if saga.compensating else saga
                saga = replace(saga, completed=saga.completed + (step,), failed_step=None, failure_code=None,
                    receipt_id=result_id if step == "prepare-receipt" else saga.receipt_id,
                    entry_id=result_id if step == "prepare-entry" else saga.entry_id,
                    status="COMPLETED" if step == "activate-entry" else "RUNNING")
                self.store.save(saga)
            return saga

    def _undo(self, saga):
        # Cancel the entry atomically before voiding a receipt, including a confirmed receipt.
        for step in saga.undo_plan()[len(saga.compensated):]:
            try:
                token = self.service_token()
                if not token:
                    raise StepError("SERVICE_IDENTITY_UNAVAILABLE", True)
                response = self.participants.forward(step, saga, saga.id + ":" + step, token)
                expected_id = saga.entry_id if step == "cancel-entry" else saga.receipt_id
                expected_status = "CANCELLED" if step == "cancel-entry" else "VOIDED"
                if not isinstance(response, dict) or response.get("id") != expected_id or response.get("status") != expected_status:
                    raise StepError("INVALID_PARTICIPANT_RESPONSE", True)
            except StepError as error:
                code = error.code if re.fullmatch(r"[A-Z_]{1,40}", error.code) else "PARTICIPANT_FAILURE"
                saga = replace(saga, status="FAILED", undo_failed_step=step, undo_failure_code=code)
                self.store.save(saga)
                return saga
            saga = replace(saga, compensated=saga.compensated + (step,))
            self.store.save(saga)
        saga = replace(saga, status="COMPENSATED")
        self.store.save(saga)
        return saga
