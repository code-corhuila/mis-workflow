from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import replace
from threading import Lock, RLock
import unittest
from uuid import uuid4

from workflow.core import Runner, StepError
from workflow.model import Failure, Reception, Saga, STEPS


class MemoryStore:
    """Doble de puerto, no persistencia productiva."""
    def __init__(self):
        self.records, self.requests, self.locks = {}, {}, {}
        self.guard, self.crash_at = RLock(), None

    def create(self, actor, key, request, trace):
        with self.guard:
            if (actor, key) in self.requests:
                saga = self.records[self.requests[(actor, key)]]
                if saga.request.fingerprint() != request.fingerprint():
                    raise Failure(409, "IDEMPOTENCY_CONFLICT")
                return saga, False
            saga = Saga(str(uuid4()), actor, request, trace, datetime.now(timezone.utc))
            self.records[saga.id] = saga
            self.requests[(actor, key)] = saga.id
            self.locks[saga.id] = Lock()
            return saga, True

    def get(self, saga_id):
        with self.guard:
            if saga_id not in self.records:
                raise Failure(404, "NOT_FOUND")
            return self.records[saga_id]

    def save(self, saga):
        if self.crash_at == len(saga.completed):
            self.crash_at = None
            raise RuntimeError("Caida simulada antes de guardar.")
        with self.guard:
            self.records[saga.id] = saga

    @contextmanager
    def exclusive(self, saga_id):
        lock = self.locks[saga_id]
        if not lock.acquire(blocking=False):
            raise Failure(409, "SAGA_BUSY")
        try:
            yield
        finally:
            lock.release()


class Participants:
    def __init__(self):
        self.calls, self.responses, self.effects = [], {}, []
        self.failure = self.lost = self.malformed = None
        self.guard = Lock()

    def forward(self, step, saga, key, token):
        with self.guard:
            self.calls.append((step, key, token, saga.trace))
            if self.failure and self.failure[0] == step:
                raise StepError(self.failure[1], self.failure[2])
            if key not in self.responses:
                status = dict(zip(STEPS, ("PENDING", "PENDING", "CONFIRMED", "ACTIVE")))[step]
                result_id = saga.receipt_id if step == "confirm-receipt" else saga.entry_id if step == "activate-entry" else str(uuid4())
                self.responses[key] = {"id": result_id, "status": status}
                self.effects.append(step)
            if self.lost == step:
                self.lost = None
                raise StepError("UPSTREAM_TIMEOUT", True)
            if self.malformed == step:
                self.malformed = None
                return {"id": "bad", "status": "ACTIVE"}
            return dict(self.responses[key])


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.actor, self.roles = str(uuid4()), {"INVENTORY"}
        self.body = {"supplierId": str(uuid4()), "medicationId": str(uuid4()), "reference": "REF-001",
                     "lotNumber": "LOT-001", "expiresOn": "2099-01-01", "quantity": 10}
        self.store, self.participants = MemoryStore(), Participants()
        self.runner = Runner(self.store, self.participants, lambda: "service-credential")

    def start(self, key="reception-001", body=None):
        return self.runner.start(self.actor, self.roles, key, self.body if body is None else body, "trace-001")

    def test_success_and_derived_keys(self):
        saga, created = self.start()
        self.assertTrue(created)
        self.assertEqual(("COMPLETED", STEPS), (saga.status, saga.completed))
        self.assertEqual(list(STEPS), self.participants.effects)
        self.assertEqual(saga, self.store.get(saga.id))
        for step, key, token, trace in self.participants.calls:
            self.assertEqual((saga.id + ":" + step, "service-credential", "trace-001"), (key, token, trace))

    def test_replay_does_not_execute(self):
        first, _ = self.start()
        replay, created = self.start()
        self.assertFalse(created)
        self.assertEqual(first, replay)
        self.assertEqual(4, len(self.participants.calls))

    def test_normalization_and_conflicting_body(self):
        first, _ = self.start()
        replay, _ = self.start(body={**self.body, "reference": " ref-001 ", "lotNumber": "lot-001"})
        self.assertEqual(first.id, replay.id)
        with self.assertRaises(Failure) as error:
            self.start(body={**self.body, "quantity": 11})
        self.assertEqual(409, error.exception.status)

    def test_actor_scopes_key(self):
        first, _ = self.start()
        other, created = self.runner.start(str(uuid4()), {"ADMIN"}, "reception-001", self.body, "trace-002")
        self.assertTrue(created)
        self.assertNotEqual(first.id, other.id)

    def test_transient_and_explicit_resume(self):
        self.participants.failure = ("prepare-entry", "UPSTREAM_UNAVAILABLE", True)
        saga, _ = self.start()
        self.assertEqual(("RUNNING", STEPS[:1], "prepare-entry"), (saga.status, saga.completed, saga.failed_step))
        self.start()
        self.assertEqual(2, len(self.participants.calls))
        self.participants.failure = None
        self.assertEqual("COMPLETED", self.runner.resume(self.actor, self.roles, saga.id).status)
        self.assertEqual(list(STEPS), self.participants.effects)

    def test_permanent_failure_is_not_retried_or_exposed(self):
        self.participants.failure = ("prepare-entry", "LOT_EXPIRED", False)
        saga, _ = self.start()
        self.assertEqual("FAILED", saga.status)
        self.runner.resume(self.actor, self.roles, saga.id)
        self.assertEqual(2, len(self.participants.calls))
        self.assertNotIn("LOT_EXPIRED", str(saga.resource()))

    def test_lost_activation_response_replays_without_duplicate_effect(self):
        self.participants.lost = "activate-entry"
        saga, _ = self.start()
        self.assertEqual(("RUNNING", STEPS[:3]), (saga.status, saga.completed))
        self.assertEqual("COMPLETED", self.runner.resume(self.actor, self.roles, saga.id).status)
        self.assertEqual(1, self.participants.effects.count("activate-entry"))
        self.assertEqual(self.participants.calls[-2][1], self.participants.calls[-1][1])

    def test_new_runner_uses_saved_progress_and_renewed_credential(self):
        self.participants.failure = ("confirm-receipt", "UNAVAILABLE", True)
        saga, _ = self.start()
        self.participants.failure = None
        runner = Runner(self.store, self.participants, lambda: "renewed-service-credential")
        self.assertEqual("COMPLETED", runner.resume(self.actor, self.roles, saga.id).status)
        self.assertEqual("renewed-service-credential", self.participants.calls[-1][2])
        self.assertEqual(list(STEPS), self.participants.effects)

    def test_crash_after_effect_before_save_at_each_step(self):
        for position in range(1, 5):
            with self.subTest(position=position):
                self.setUp()
                self.store.crash_at = position
                with self.assertRaises(RuntimeError):
                    self.start()
                saga = next(iter(self.store.records.values()))
                self.assertEqual(STEPS[:position - 1], saga.completed)
                runner = Runner(self.store, self.participants, lambda: "service-credential")
                self.assertEqual("COMPLETED", runner.resume(self.actor, self.roles, saga.id).status)
                self.assertEqual(list(STEPS), self.participants.effects)

    def test_invalid_response_does_not_advance(self):
        self.participants.malformed = "prepare-entry"
        saga, _ = self.start()
        self.assertEqual(STEPS[:1], saga.completed)
        self.assertEqual("COMPLETED", self.runner.resume(self.actor, self.roles, saga.id).status)

    def test_missing_service_token_has_no_effects(self):
        runner = Runner(self.store, self.participants, lambda: "")
        saga, _ = runner.start(self.actor, self.roles, "reception-001", self.body, "trace-001")
        self.assertEqual(("RUNNING", ()), (saga.status, saga.completed))
        self.assertEqual([], self.participants.calls)

    def test_concurrent_start(self):
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(lambda _: self.start(), range(10)))
        self.assertEqual(1, sum(created for _, created in results))
        self.assertEqual(1, len({saga.id for saga, _ in results}))
        self.assertEqual(list(STEPS), self.participants.effects)

    def test_concurrent_resume_is_excluded(self):
        saga, _ = self.store.create(self.actor, "reception-001", Reception.parse(self.body), "trace-001")
        with self.store.exclusive(saga.id), self.assertRaises(Failure) as error:
            self.runner.resume(self.actor, self.roles, saga.id)
        self.assertEqual("SAGA_BUSY", error.exception.code)

    def test_permissions_and_owner(self):
        for roles in ({"CONSULTATION"}, {"SUPERVISOR"}, set()):
            with self.assertRaises(Failure):
                self.runner.start(self.actor, roles, "reception-001", self.body, "trace-001")
        saga, _ = self.start()
        other = str(uuid4())
        self.assertEqual(saga, self.runner.get(other, {"SUPERVISOR"}, saga.id))
        for roles in ({"INVENTORY"}, {"SUPERVISOR"}, {"CONSULTATION"}):
            with self.assertRaises(Failure):
                self.runner.resume(other, roles, saga.id)
        with self.assertRaises(Failure):
            self.runner.get(other, {"INVENTORY"}, saga.id)
        self.assertEqual(saga, self.runner.resume(other, {"ADMIN"}, saga.id))

    def test_keys_trace_and_missing_id(self):
        for key in (None, "short", "k" * 129, "invalid/key"):
            with self.assertRaises(Failure):
                self.start(key=key)
        with self.assertRaises(Failure):
            self.runner.start(self.actor, self.roles, "reception-001", self.body, "bad/trace")
        with self.assertRaises(Failure) as error:
            self.runner.get(self.actor, self.roles, str(uuid4()))
        self.assertEqual(404, error.exception.status)

    def test_invalid_payload_creates_nothing(self):
        cases = [{**self.body, field: value} for field, value in (
            ("quantity", True), ("quantity", "10"), ("quantity", 0), ("quantity", 1000001),
            ("quantity", 1.5), ("expiresOn", "2026-02-30"), ("expiresOn", "0000-01-01"),
            ("supplierId", "bad"), ("reference", "a/b"), ("lotNumber", "a" * 41))]
        cases += [None, [], {**self.body, "unknown": True}, {key: value for key, value in self.body.items() if key != "quantity"}]
        for body in cases:
            with self.subTest(body=body), self.assertRaises(Failure):
                self.runner.start(self.actor, self.roles, "reception-001", body, "trace-001")
        self.assertEqual({}, self.store.records)

    def test_corrupted_progress_cannot_activate_inventory(self):
        for changes in ({"completed": ("activate-entry",)}, {"status": "COMPLETED"}, {"status": "UNKNOWN"},
                        {"completed": STEPS, "status": "RUNNING"}, {"receipt_id": str(uuid4())},
                        {"completed": STEPS[:1], "receipt_id": "invalid-id"}):
            with self.subTest(changes=changes):
                self.setUp()
                saga, _ = self.store.create(self.actor, "reception-001", Reception.parse(self.body), "trace-001")
                self.store.records[saga.id] = replace(saga, **changes)
                with self.assertRaises(Failure) as error:
                    self.runner.resume(self.actor, self.roles, saga.id)
                self.assertEqual("INVALID_SAGA_STATE", error.exception.code)
                self.assertEqual([], self.participants.calls)


if __name__ == "__main__":
    unittest.main()
