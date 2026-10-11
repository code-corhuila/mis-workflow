import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location("migration_role", Path(__file__).resolve().parents[1] / "db/migrate.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class MigrationRoleTests(unittest.TestCase):
    def test_role_policy_uses_names_and_rejects_missing_or_privileged_columns(self):
        role = dict(rolcanlogin=True, rolbypassrls=False, rolreplication=False,
                    rolinherit=False, rolcreaterole=False, rolcreatedb=False, rolsuper=False)
        self.assertTrue(migration.safe_role(role))
        for name in role:
            with self.subTest(name=name):
                changed = {**role, name: not role[name]}
                self.assertFalse(migration.safe_role(changed))
                missing = {key: value for key, value in role.items() if key != name}
                self.assertFalse(migration.safe_role(missing))
