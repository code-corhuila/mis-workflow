import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import psycopg
from psycopg.conninfo import conninfo_to_dict


@unittest.skipUnless(os.environ.get("WORKFLOW_TEST_DSN"), "Disposable PostgreSQL not configured")
class MigrateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        options = conninfo_to_dict(os.environ["WORKFLOW_TEST_DSN"])
        if options.get("dbname") != "workflow_test":
            raise RuntimeError("Migration tests require disposable workflow_test")
        cls.environment = {"PGHOST": options["host"], "PGDATABASE": "workflow_test", "PGUSER": options["user"],
                           "PGPASSWORD": "", "WORKFLOW_APP_PASSWORD": "synthetic-disposable-workflow-password"}
        spec = importlib.util.spec_from_file_location("workflow_migration_test", Path("db/migrate.py"))
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_empty_password_rejected(self):
        with patch.dict(os.environ, {**self.environment, "WORKFLOW_APP_PASSWORD": ""}), self.assertRaises(ValueError):
            self.module.migrate()

    def test_provision_and_repeat_preserve_existing_state(self):
        from workflow.model import Reception
        from workflow.postgres import PostgresStore
        from test_postgres import ACTOR, BODY
        with patch.dict(os.environ, self.environment):
            self.module.migrate()
            store = PostgresStore(os.environ["WORKFLOW_TEST_DSN"] + " user=workflow_app")
            saga, _ = store.create(ACTOR, "migration-repeat-001", Reception.parse(BODY), "trace-migration")
            self.module.migrate()
            self.assertEqual(saga, store.get(saga.id))

    def test_unsafe_role_fails_without_silently_changing_it(self):
        with patch.dict(os.environ, self.environment):
            self.module.migrate()
            with psycopg.connect(os.environ["WORKFLOW_TEST_DSN"], autocommit=True) as admin:
                admin.execute("ALTER ROLE workflow_app CREATEROLE")
                try:
                    with self.assertRaises(ValueError):
                        self.module.migrate()
                    self.assertTrue(admin.execute("SELECT rolcreaterole FROM pg_roles WHERE rolname='workflow_app'").fetchone()[0])
                finally:
                    admin.execute("ALTER ROLE workflow_app NOCREATEROLE")
