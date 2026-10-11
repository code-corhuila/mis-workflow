from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
import json
from pathlib import Path
import subprocess
import sys
import unittest
from uuid import uuid4

from workflow.model import Failure, STEPS
from test_core import Participants

ACTOR = "7df15b6b-690c-48d4-8a57-749d78520a61"
BODY = {"supplierId": "bac9c189-54e3-44e0-8f21-75464bc04513",
        "medicationId": "05375635-14a4-4303-91ca-59b29678224f", "reference": "REF-STORAGE",
        "lotNumber": "LOT-STORAGE", "expiresOn": "2099-01-01", "quantity": 10}


@unittest.skipUnless(os.environ.get("WORKFLOW_TEST_DSN"), "Disposable PostgreSQL not configured")
class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
        from workflow.postgres import PostgresStore
        cls.psycopg = psycopg
        cls.admin_dsn = os.environ["WORKFLOW_TEST_DSN"]
        options = conninfo_to_dict(cls.admin_dsn)
        if options.get("dbname") != "workflow_test":
            raise RuntimeError("Integration tests require the disposable workflow_test database")
        cls.app_dsn = make_conninfo(cls.admin_dsn, user="workflow_app")
        cls.store = PostgresStore(cls.app_dsn)
        cls.sql = Path("db/001-sagas.sql").read_text(encoding="utf-8")
        assert cls.sql.startswith("\\set ON_ERROR_STOP on\n")
        cls.sql = cls.sql.split("\n", 1)[1]
        with psycopg.connect(cls.admin_dsn, autocommit=True) as connection:
            connection.execute("DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='workflow_app') THEN CREATE ROLE workflow_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT; END IF; END $$")
            connection.execute(cls.sql)
            connection.execute(cls.sql)
        cls.store.health()

    def setUp(self):
        with self.psycopg.connect(self.admin_dsn, autocommit=True) as connection:
            connection.execute("TRUNCATE workflow.saga")

    def create(self, key="request-storage-001"):
        from workflow.model import Reception
        return self.store.create(ACTOR, key, Reception.parse(BODY), "trace-storage")

    def assertFailure(self, status, code, callback):
        with self.assertRaises(Failure) as failure:
            callback()
        self.assertEqual((status, code), (failure.exception.status, failure.exception.code))

    def test_concurrent_creation_and_conflict(self):
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(lambda _: self.create(), range(10)))
        self.assertEqual(1, sum(created for _, created in results))
        self.assertEqual(1, len({saga.id for saga, _ in results}))
        from workflow.model import Reception
        request = Reception.parse({**BODY, "quantity": BODY["quantity"] + 1})
        self.assertFailure(409, "IDEMPOTENCY_CONFLICT",
                           lambda: self.store.create(ACTOR, "request-storage-001", request, "other-trace"))
        self.assertEqual("trace-storage", self.create()[0].trace)

    def test_lock_is_exclusive_and_save_requires_owner(self):
        saga, _ = self.create()
        self.assertFailure(503, "SAGA_LOCK_REQUIRED", lambda: self.store.save(saga))
        with self.store.exclusive(saga.id):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(self.assertFailure, 409, "SAGA_BUSY", lambda: self.acquire(saga.id)).result()
            self.assertFailure(503, "SAGA_LOCK_REQUIRED", lambda: self.store.save(replace(saga, id=str(uuid4()))))
        self.acquire(saga.id)

    def acquire(self, saga_id):
        with self.store.exclusive(saga_id):
            pass

    def test_lost_lock_connection_never_reconnects_to_save(self):
        saga, _ = self.create()
        with self.store.exclusive(saga.id):
            pid = self.store.owner.get()[1].info.backend_pid
            with self.psycopg.connect(self.admin_dsn, autocommit=True) as connection:
                connection.execute("SELECT pg_terminate_backend(%s)", (pid,))
            self.assertFailure(503, "WORKFLOW_STORAGE_UNAVAILABLE", lambda: self.store.save(saga))
        self.acquire(saga.id)

    def test_repeated_migration_preserves_state_read_by_another_process(self):
        saga, _ = self.create()
        with self.psycopg.connect(self.admin_dsn, autocommit=True) as connection:
            connection.execute(self.sql)
        child = subprocess.run([sys.executable, "-c",
            "import json,os; from workflow.postgres import PostgresStore; "
            "print(json.dumps(PostgresStore(os.environ['TEST_APP_DSN']).get(os.environ['TEST_SAGA_ID']).resource()))"],
            env={**os.environ, "TEST_APP_DSN": self.app_dsn, "TEST_SAGA_ID": saga.id},
            capture_output=True, text=True, check=True)
        self.assertEqual(saga.resource(), json.loads(child.stdout))

    def test_crash_before_each_save_recovers_from_postgres(self):
        from workflow.core import Runner
        from workflow.postgres import PostgresStore

        class CrashStore(PostgresStore):
            def save(inner, saga):
                if len(saga.completed) == inner.crash_at:
                    raise RuntimeError("Simulated process crash after participant effect")
                super().save(saga)

        for index in range(1, 5):
            with self.subTest(step=index):
                store = CrashStore(self.app_dsn)
                store.crash_at = index
                participants = Participants()
                key = "crash-step-" + str(index)
                runner = Runner(store, participants, lambda: "service-credential")
                with self.assertRaises(RuntimeError):
                    runner.start(ACTOR, ["INVENTORY"], key, BODY, "trace-crash")
                saga, created = store.create(ACTOR, key, self.create("fixture-" + str(index))[0].request, "new-trace")
                self.assertFalse(created)
                self.assertEqual(index - 1, len(saga.completed))
                recovered = Runner(PostgresStore(self.app_dsn), participants, lambda: "service-renewed").resume(
                    ACTOR, ["INVENTORY"], saga.id)
                self.assertEqual("COMPLETED", recovered.status)
                self.assertEqual(4, len(participants.effects))

    def test_restart_and_uncertain_activation_reuse_participant_key(self):
        from workflow.core import Runner
        from workflow.postgres import PostgresStore
        participants = Participants()
        participants.lost = "activate-entry"
        runner = Runner(self.store, participants, lambda: "service-first")
        saga, created = runner.start(ACTOR, ["INVENTORY"], "request-restart-001", BODY, "trace-storage")
        self.assertTrue(created)
        self.assertEqual(STEPS[:3], saga.completed)
        fresh = Runner(PostgresStore(self.app_dsn), participants, lambda: "service-renewed")
        replay, created = fresh.start(ACTOR, ["INVENTORY"], "request-restart-001", BODY, "new-trace")
        self.assertFalse(created)
        self.assertEqual(saga, replay)
        recovered = fresh.resume(ACTOR, ["INVENTORY"], saga.id)
        self.assertEqual("COMPLETED", recovered.status)
        self.assertEqual(4, len(participants.effects))
        self.assertEqual(recovered, PostgresStore(self.app_dsn).get(saga.id))

    def test_save_each_step_survives_new_adapter(self):
        from workflow.postgres import PostgresStore
        saga, _ = self.create()
        with self.store.exclusive(saga.id):
            for index, step in enumerate(STEPS, 1):
                saga = replace(saga, completed=STEPS[:index],
                               receipt_id=saga.receipt_id or str(uuid4()),
                               entry_id=(saga.entry_id or str(uuid4())) if index >= 2 else None,
                               status="COMPLETED" if index == 4 else "RUNNING")
                self.store.save(saga)
                self.assertEqual(saga, PostgresStore(self.app_dsn).get(saga.id))

    def test_database_constraints_and_least_privilege(self):
        saga, _ = self.create()
        statements = [
            "DELETE FROM workflow.saga",
            "UPDATE workflow.saga SET actor=gen_random_uuid()",
            "UPDATE workflow.saga SET request='{}'::jsonb",
            "CREATE TABLE workflow.forbidden (id int)",
            "CREATE SCHEMA forbidden_workflow",
            "SELECT * FROM public.private_probe",
        ]
        with self.psycopg.connect(self.admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS public.private_probe (id int)")
            connection.execute("REVOKE ALL ON public.private_probe FROM PUBLIC")
        for statement in statements:
            with self.subTest(statement=statement):
                with self.psycopg.connect(self.app_dsn, autocommit=True) as connection:
                    with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                        connection.execute(statement)
        with self.store.exclusive(saga.id):
            self.assertFailure(503, "WORKFLOW_STORAGE_UNAVAILABLE",
                               lambda: self.store.save(replace(saga, completed=("activate-entry",))))
        self.assertEqual(saga, self.store.get(saga.id))
        self.assertFailure(404, "NOT_FOUND", lambda: self.store.get(str(uuid4())))
