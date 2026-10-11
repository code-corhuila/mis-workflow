import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("admin_secret", Path(__file__).resolve().parents[1] / "db/migrate.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class AdminSecretTests(unittest.TestCase):
    def test_missing_file_does_not_fall_back_to_environment_password(self):
        with patch.dict(os.environ, {"PGPASSWORD": "legacy-secret"}, clear=True):
            with self.assertRaises(KeyError):
                migration.admin_password()
        with patch.dict(os.environ, {"PGPASSWORD_FILE": "/nonexistent/admin-password"}, clear=True):
            with self.assertRaises(FileNotFoundError):
                migration.admin_password()

    def test_secret_validation_and_line_ending(self):
        with TemporaryDirectory() as temporary:
            secret = Path(temporary) / "admin-password"
            with patch.dict(os.environ, {"PGPASSWORD_FILE": str(secret)}, clear=True):
                secret.write_text(" synthetic-password \n", encoding="utf-8")
                self.assertEqual(" synthetic-password ", migration.admin_password())
                for value in ("", "\n", "a\nb", "a\rb", "a\x00b", "x" * 1025):
                    with self.subTest(value_length=len(value)):
                        secret.write_text(value, encoding="utf-8")
                        with self.assertRaises(ValueError):
                            migration.admin_password()
