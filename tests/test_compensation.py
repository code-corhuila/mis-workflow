from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import unittest
from uuid import uuid4

from test_core import MemoryStore, Participants
from workflow.core import Runner, StepError
from workflow.model import Failure, Reception, STEPS


class CompensationTests(unittest.TestCase):
    def setUp(self):
        self.actor = str(uuid4())
        self.body = {"supplierId": str(uuid4()), "medicationId": str(uuid4()), "reference": "REF-UNDO",
                     "lotNumber": "LOT-UNDO", "expiresOn": "2099-01-01", "quantity": 10}
        self.store, self.participants = MemoryStore(), Participants()
        self.runner = Runner(self.store, self.participants, lambda: "service-credential")

    def start(self):
        return self.runner.start(self.actor, {"INVENTORY"}, "compensation-001", self.body, "trace-undo")[0]

    def rejected(self, step):
        self.participants.failure = (step, "UPSTREAM_REJECTED", False)
        return self.start()

    def test_definitive_failure_undo_plan_at_each_forward_step(self):
        for step, plan in zip(STEPS, ((), ("void-receipt",), ("cancel-entry", "void-receipt"), ("cancel-entry", "void-receipt"))):
            with self.subTest(step=step):
                self.setUp()
                saga = self.rejected(step)
                self.assertEqual("COMPENSATED", saga.status)
                self.assertEqual(plan, saga.compensated)
                self.assertEqual(step, saga.resource()["failedStep"])
                self.assertNotIn("UPSTREAM_REJECTED", str(saga.resource()))
                for undo, key, token, trace in self.participants.calls[len(saga.completed)+1:]:
                    self.assertEqual((saga.id + ":" + undo, "service-credential", "trace-undo"), (key, token, trace))
                calls = len(self.participants.calls)
                self.start()
                self.runner.resume(self.actor, {"INVENTORY"}, saga.id)
                self.runner.compensate(str(uuid4()), {"ADMIN"}, saga.id)
                self.assertEqual(calls, len(self.participants.calls))

    def test_transient_failure_never_compensates_uncertain_outcome(self):
        for step in STEPS:
            with self.subTest(step=step):
                self.setUp()
                self.participants.failure = (step, "UPSTREAM_UNAVAILABLE", True)
                saga = self.start()
                self.assertEqual("RUNNING", saga.status)
                self.assertFalse(saga.compensating)
                self.assertEqual((), saga.compensated)
                with self.assertRaises(Failure) as failure:
                    self.runner.compensate(self.actor, {"ADMIN"}, saga.id)
                self.assertEqual(409, failure.exception.status)

    def failed_undo(self, undo):
        original = self.participants.forward

        def forward(step, saga, key, token):
            if step == undo:
                raise StepError("PRIVATE_UNDO_ERROR", True)
            return original(step, saga, key, token)

        self.participants.forward = forward
        saga = self.rejected("confirm-receipt")
        self.participants.forward = original
        return saga

    def test_failed_compensation_stops_and_requires_explicit_admin_decision(self):
        saga = self.failed_undo("void-receipt")
        self.assertEqual(("FAILED", ("cancel-entry",), "void-receipt"),
                         (saga.status, saga.compensated, saga.resource()["failedStep"]))
        self.assertNotIn("PRIVATE_UNDO_ERROR", str(saga.resource()))
        count = len(self.participants.calls)
        self.runner.resume(self.actor, {"ADMIN"}, saga.id)
        self.assertEqual(count, len(self.participants.calls))
        for roles in ({"INVENTORY"}, {"SUPERVISOR"}, {"CONSULTATION"}):
            with self.assertRaises(Failure) as error:
                self.runner.compensate(self.actor, roles, saga.id)
            self.assertEqual(403, error.exception.status)
        recovered = self.runner.compensate(str(uuid4()), {"ADMIN"}, saga.id)
        self.assertEqual(("COMPENSATED", ("cancel-entry", "void-receipt")), (recovered.status, recovered.compensated))
        self.assertEqual(1, self.participants.effects.count("cancel-entry"))

    def test_active_entry_guard_never_voids_confirmed_receipt(self):
        original = self.participants.forward
        state = {"receipt": "PENDING", "entry": "PENDING", "stock": 0, "movements": ()}

        def forward(step, saga, key, token):
            if step == "cancel-entry" and state["entry"] == "ACTIVE":
                self.participants.calls.append((step, key, token, saga.trace))
                raise StepError("ENTRY_ACTIVE", False)
            result = original(step, saga, key, token)
            if step == "confirm-receipt":
                # Synthetic participant anomaly: the entry is already irreversible when rejection arrives.
                state.update(receipt="CONFIRMED", entry="ACTIVE", stock=saga.request.quantity,
                             movements=(("ENTRY", saga.entry_id, saga.request.quantity),))
                raise StepError("UPSTREAM_REJECTED", False)
            if step == "void-receipt":
                state["receipt"] = "VOIDED"
            if step == "cancel-entry":
                state["entry"] = "CANCELLED"
            return result

        self.participants.forward = forward
        saga = self.start()
        before = dict(state)
        self.assertEqual({"receipt": "CONFIRMED", "entry": "ACTIVE", "stock": 10,
                          "movements": (("ENTRY", saga.entry_id, 10),)}, before)
        self.assertEqual("FAILED", saga.status)
        self.assertEqual("cancel-entry", saga.resource()["failedStep"])
        self.assertEqual((), saga.compensated)
        count = len(self.participants.calls)
        self.runner.resume(self.actor, {"ADMIN"}, saga.id)
        self.assertEqual(count, len(self.participants.calls))
        recovered = self.runner.compensate(str(uuid4()), {"ADMIN"}, saga.id)
        self.assertEqual(("FAILED", "cancel-entry", ()),
                         (recovered.status, recovered.resource()["failedStep"], recovered.compensated))
        self.assertEqual(before, state)
        self.assertNotIn("void-receipt", [call[0] for call in self.participants.calls])
        self.assertNotIn("activate-entry", [call[0] for call in self.participants.calls])
        cancels = [call for call in self.participants.calls if call[0] == "cancel-entry"]
        self.assertEqual(2, len(cancels))
        self.assertEqual(cancels[0][1:], cancels[1][1:])

    def test_lost_undo_response_is_recovered_with_same_key_and_single_effect(self):
        self.participants.lost = "void-receipt"
        saga = self.rejected("prepare-entry")
        self.assertEqual("FAILED", saga.status)
        self.assertEqual(1, self.participants.effects.count("void-receipt"))
        recovered = Runner(self.store, self.participants, lambda: "renewed-service-credential").compensate(self.actor, {"ADMIN"}, saga.id)
        self.assertEqual("COMPENSATED", recovered.status)
        self.assertEqual(1, self.participants.effects.count("void-receipt"))
        self.assertEqual(self.participants.calls[-2][1], self.participants.calls[-1][1])

    def test_crash_after_undo_effect_before_save_resumes_only_undo(self):
        for position in (1, 2):
            with self.subTest(position=position):
                self.setUp()
                original_save = self.store.save

                def crash(saga):
                    if saga.compensating and len(saga.compensated) == position:
                        raise RuntimeError("Simulated crash after undo effect")
                    original_save(saga)

                self.store.save = crash
                with self.assertRaises(RuntimeError):
                    self.rejected("confirm-receipt")
                saga = next(iter(self.store.records.values()))
                self.assertTrue(saga.compensating)
                self.assertEqual(position - 1, len(saga.compensated))
                self.store.save = original_save
                recovered = self.runner.resume(self.actor, {"INVENTORY"}, saga.id)
                self.assertEqual("COMPENSATED", recovered.status)
                self.assertEqual(1, self.participants.effects.count("cancel-entry"))
                self.assertEqual(1, self.participants.effects.count("void-receipt"))
                self.assertNotIn("activate-entry", self.participants.effects)

    def test_legacy_failed_and_completed_sagas_cannot_be_blindly_compensated(self):
        saga, _ = self.store.create(self.actor, "legacy-failed-001", Reception.parse(self.body), "trace-legacy")
        self.store.records[saga.id] = replace(saga, status="FAILED", failed_step="prepare-receipt", failure_code="UPSTREAM_REJECTED")
        for value in (saga.id, self.start().id):
            with self.assertRaises(Failure) as error:
                self.runner.compensate(self.actor, {"ADMIN"}, value)
            self.assertEqual(409, error.exception.status)

    def test_concurrent_admin_decisions_create_one_undo_effect(self):
        saga = self.failed_undo("void-receipt")
        def attempt(_):
            try:
                return self.runner.compensate(self.actor, {"ADMIN"}, saga.id).status
            except Failure as error:
                self.assertEqual("SAGA_BUSY", error.code)
                return "BUSY"
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(attempt, range(10)))
        self.assertIn("COMPENSATED", results)
        self.assertEqual(1, self.participants.effects.count("void-receipt"))

    def test_corrupt_undo_progress_performs_no_actions(self):
        saga = self.failed_undo("void-receipt")
        for change in ({"compensated": ("void-receipt",)}, {"compensating": False}, {"status": "COMPENSATED"}):
            self.store.records[saga.id] = replace(saga, **change)
            with self.assertRaises(Failure) as error:
                self.runner.compensate(self.actor, {"ADMIN"}, saga.id)
            self.assertEqual("INVALID_SAGA_STATE", error.exception.code)
