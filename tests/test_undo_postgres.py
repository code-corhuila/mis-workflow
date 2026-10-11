from dataclasses import asdict
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

import test_postgres
from test_core import Participants
from workflow.core import Runner
from workflow.model import Reception, STEPS
from workflow.postgres import PostgresStore


@unittest.skipUnless(os.environ.get("WORKFLOW_TEST_DSN"), "Disposable PostgreSQL not configured")
class UndoPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_postgres.PostgresTests.setUpClass()
        cls.dsn = test_postgres.PostgresTests.app_dsn

    def test_crash_after_each_undo_preserves_phase_and_recovers_from_new_connection(self):
        class CrashStore(PostgresStore):
            def save(inner, saga):
                if saga.compensating and len(saga.compensated) == inner.crash_at:
                    raise RuntimeError("Simulated crash before durable undo progress")
                super().save(saga)

        for position in (1, 2):
            actor = str(uuid4())
            store = CrashStore(self.dsn)
            store.crash_at = position
            participants = Participants()
            participants.failure = ("confirm-receipt", "UPSTREAM_REJECTED", False)
            runner = Runner(store, participants, lambda: "service-first")
            key = "undo-crash-" + str(uuid4())
            with self.assertRaises(RuntimeError):
                runner.start(actor, ["INVENTORY"], key, test_postgres.BODY, "trace-undo-pg")
            saved, created = PostgresStore(self.dsn).create(actor, key, Reception.parse(test_postgres.BODY), "new-trace")
            self.assertFalse(created)
            self.assertTrue(saved.compensating)
            self.assertEqual(position - 1, len(saved.compensated))
            repeated = ("cancel-entry", "void-receipt")[position - 1]
            self.assertEqual(1, participants.effects.count(repeated))
            self.assertNotIn(repeated, saved.compensated)
            before_recovery = len(participants.calls)
            recovered = Runner(PostgresStore(self.dsn), participants, lambda: "service-renewed").resume(actor, ["INVENTORY"], saved.id)
            self.assertEqual("COMPENSATED", recovered.status)
            self.assertEqual(recovered, PostgresStore(self.dsn).get(saved.id))
            self.assertEqual(1, participants.effects.count("cancel-entry"))
            self.assertEqual(1, participants.effects.count("void-receipt"))
            self.assertNotIn("activate-entry", participants.effects)
            retries = [call for call in participants.calls if call[0] == repeated]
            self.assertEqual(2, len(retries))
            self.assertEqual((saved.id + ":" + repeated, "trace-undo-pg"), (retries[0][1], retries[0][3]))
            self.assertEqual((retries[0][1], retries[0][3]), (retries[1][1], retries[1][3]))
            self.assertEqual(("service-first", "service-renewed"), (retries[0][2], retries[1][2]))
            self.assertTrue(all(call[0] in ("cancel-entry", "void-receipt")
                                for call in participants.calls[before_recovery:]))

    def test_upgrade_v1_retains_completed_saga_and_repeated_upgrade(self):
        options = conninfo_to_dict(os.environ["WORKFLOW_TEST_DSN"])
        self.assertEqual("workflow_test", options["dbname"])
        database = "workflow_upgrade_" + uuid4().hex[:8]
        with psycopg.connect(os.environ["WORKFLOW_TEST_DSN"], autocommit=True) as admin:
            if not admin.execute("SELECT FROM pg_database WHERE datname=%s", (database,)).fetchone():
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        dsn = make_conninfo(os.environ["WORKFLOW_TEST_DSN"], dbname=database)
        identifier = str(uuid4())
        request = Reception.parse(test_postgres.BODY)
        with psycopg.connect(dsn, autocommit=True) as admin:
            initial = Path("db/001-sagas.sql").read_text(encoding="utf-8").split("\n", 1)[1]
            admin.execute(initial)
            admin.execute("INSERT INTO workflow.saga(id,actor,request_key,fingerprint,request,trace,status,completed,receipt_id,entry_id) VALUES (%s,%s,%s,%s,%s,%s,'COMPLETED',%s,%s,%s)",
                (identifier, test_postgres.ACTOR, "upgrade-" + identifier, request.fingerprint(), Jsonb(asdict(request)), "trace-upgrade", list(STEPS), uuid4(), uuid4()))
        spec = importlib.util.spec_from_file_location("undo_upgrade", Path("db/migrate.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        environment = {"PGHOST": options["host"], "PGDATABASE": database, "PGUSER": options["user"],
                       "PGPASSWORD": "", "WORKFLOW_APP_PASSWORD": "synthetic-disposable-workflow-password"}
        with patch.dict(os.environ, environment):
            module.migrate()
            store = PostgresStore(make_conninfo(dsn, user="workflow_app"))
            before = store.get(identifier)
            self.assertEqual("COMPLETED", before.status)
            self.assertFalse(before.compensating)
            self.assertEqual((), before.compensated)
            module.migrate()
            self.assertEqual(before, store.get(identifier))
            store.health()
