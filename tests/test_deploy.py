from pathlib import Path
import unittest

import yaml


class DeploymentTests(unittest.TestCase):
    def test_runtime_has_only_application_credentials_and_fail_closed_mount(self):
        model = yaml.safe_load(Path("deploy/compose.yml").read_text(encoding="utf-8"))
        server = model["services"]["workflow"]
        self.assertNotIn("ports", server)
        self.assertTrue(server["read_only"])
        self.assertNotIn("PGPASSWORD", server["environment"])
        self.assertNotIn("PGUSER", server["environment"])
        self.assertEqual("${WORKFLOW_APP_PASSWORD:?WORKFLOW_APP_PASSWORD required}", server["environment"]["WORKFLOW_PG_PASSWORD"])
        mount, = server["volumes"]
        self.assertEqual("${WORKFLOW_CREDENTIALS_PATH:?WORKFLOW_CREDENTIALS_PATH required}", mount["source"])
        self.assertEqual("/run/workflow", mount["target"])
        self.assertTrue(mount["read_only"])
        self.assertIs(False, mount["bind"]["create_host_path"])
        self.assertEqual(["/tmp:uid=10001,gid=10001,mode=0700"], server["tmpfs"])

    def test_migration_is_a_separate_non_restarting_job(self):
        model = yaml.safe_load(Path("deploy/compose.yml").read_text(encoding="utf-8"))
        migration = model["services"]["workflow-db-migrate"]
        self.assertEqual("no", migration["restart"])
        self.assertNotIn("ports", migration)
        self.assertNotIn("PGPASSWORD", migration["environment"])
        self.assertEqual("/run/migration/admin-password", migration["environment"]["PGPASSWORD_FILE"])
        mount, = migration["volumes"]
        self.assertEqual("/run/migration/admin-password", mount["target"])
        self.assertTrue(mount["read_only"])
        self.assertIs(False, mount["bind"]["create_host_path"])
        self.assertTrue(migration["read_only"])
        self.assertEqual("service_completed_successfully", model["services"]["workflow"]["depends_on"]["workflow-db-migrate"]["condition"])
