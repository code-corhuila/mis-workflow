from datetime import datetime, timezone
from pathlib import Path
import unittest
from uuid import uuid4

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate

from workflow.model import Reception, Saga, STEPS


class ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = yaml.safe_load((Path(__file__).resolve().parents[1] / "docs/openapi.yaml").read_text(encoding="utf-8"))

    def test_openapi_is_valid(self):
        validate(self.contract)

    def test_core_resources_match_public_schema(self):
        schema = {"$ref": "#/components/schemas/Saga", "components": self.contract["components"]}
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        body = Reception(str(uuid4()), str(uuid4()), "REF-001", "LOT-001", "2099-01-01", 10)
        for count in range(5):
            saga = Saga(str(uuid4()), str(uuid4()), body, "trace-001", datetime.now(timezone.utc),
                status="COMPLETED" if count == 4 else "RUNNING", completed=STEPS[:count],
                receipt_id=str(uuid4()) if count >= 1 else None, entry_id=str(uuid4()) if count >= 2 else None)
            validator.validate(saga.resource())


if __name__ == "__main__":
    unittest.main()
