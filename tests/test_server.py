from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt

from test_core import MemoryStore, Participants
from workflow.auth import HumanTokens
from workflow.core import Runner, StepError
from workflow.model import Failure
from workflow.server import create_app


class Store(MemoryStore):
    def health(self):
        pass


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.actor = str(uuid4())
        self.body = {"supplierId": str(uuid4()), "medicationId": str(uuid4()), "reference": "REF-HTTP",
                     "lotNumber": "LOT-HTTP", "expiresOn": "2099-01-01", "quantity": 10}
        self.store, self.participants = Store(), Participants()
        self.runner = Runner(self.store, self.participants, lambda: "separate-service-credential")
        self.auth = HumanTokens("http://unused.local/jwks")
        self.auth.keys = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=self.private.public_key()))
        self.client = TestClient(create_app(self.runner, self.auth), raise_server_exceptions=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def token(self, role="INVENTORY", changes=None, key=None, kid="server-test-key"):
        now = int(time.time())
        claims = {"sub": self.actor, "exp": now + 300, "iat": now, "iss": "mis-security",
                  "aud": "mis-api", "roles": [role]}
        claims.update(changes or {})
        return jwt.encode(claims, key or self.private, algorithm="RS256", headers={"kid": kid})

    def headers(self, role="INVENTORY", changes=None):
        return {"Authorization": "Bearer " + self.token(role, changes), "Idempotency-Key": "server-reception-001",
                "X-Correlation-Id": "trace-server"}

    def start(self, headers=None, body=None):
        return self.client.post("/api/v1/sagas/receive-supplies", json=self.body if body is None else body,
                                headers=self.headers() if headers is None else headers)

    def assertError(self, response, status, code):
        self.assertEqual(status, response.status_code, response.text)
        self.assertEqual(code, response.json()["error"])
        self.assertEqual(response.headers["X-Correlation-Id"], response.json()["traceId"])
        self.assertEqual({"error", "message", "traceId"}, set(response.json()) - {"details"})

    def test_start_replay_find_resume_and_correlated_service_identity(self):
        first = self.start()
        self.assertEqual(201, first.status_code, first.text)
        self.assertEqual("COMPLETED", first.json()["status"])
        location = first.headers["Location"]
        replay = self.start(headers={**self.headers(), "X-Correlation-Id": "trace-new"})
        self.assertEqual(200, replay.status_code)
        self.assertEqual(first.json(), replay.json())
        self.assertEqual("trace-new", replay.headers["X-Correlation-Id"])
        self.assertEqual(first.json(), self.client.get(location, headers=self.headers()).json())
        self.assertEqual(first.json(), self.client.post(location + "/resume", headers=self.headers()).json())
        self.assertEqual(4, len(self.participants.calls))
        for _, _, token, trace in self.participants.calls:
            self.assertEqual(("separate-service-credential", "trace-server"), (token, trace))

    def test_invalid_authentication_and_service_tokens_cannot_start(self):
        self.assertError(self.start(headers={"X-User-Role": "ADMIN"}), 401, "UNAUTHORIZED")
        now = int(time.time())
        for changes in ({"exp": now - 1}, {"iss": "other"}, {"aud": "other"},
                        {"sub": "not-uuid"}, {"roles": ["UNKNOWN"]}, {"roles": "ADMIN"}, {"roles": [[]]}):
            self.assertError(self.start(headers=self.headers(changes=changes)), 401, "UNAUTHORIZED")
        self.assertError(self.start(headers={"Authorization": "Bearer " + self.token(key=self.wrong)}), 401, "UNAUTHORIZED")
        self.assertError(self.start(headers=self.headers(changes={"service": "mis-workflow"})), 403, "FORBIDDEN")
        self.assertEqual({}, self.store.records)

    def test_role_matrix_and_inventory_ownership(self):
        for role in ("SUPERVISOR", "CONSULTATION"):
            self.assertError(self.start(headers=self.headers(role)), 403, "FORBIDDEN")
        created = self.start()
        location = created.headers["Location"]
        for role in ("ADMIN", "SUPERVISOR"):
            self.assertEqual(200, self.client.get(location, headers=self.headers(role, {"sub": str(uuid4())})).status_code)
        self.assertError(self.client.get(location, headers=self.headers("CONSULTATION")), 403, "FORBIDDEN")
        self.assertError(self.client.get(location, headers=self.headers(changes={"sub": str(uuid4())})), 403, "FORBIDDEN")
        self.assertError(self.client.post(location + "/resume", headers=self.headers("SUPERVISOR")), 403, "FORBIDDEN")
        self.assertError(self.client.post(location + "/resume", headers=self.headers(changes={"sub": str(uuid4())})), 403, "FORBIDDEN")
        self.assertEqual(200, self.client.post(location + "/resume", headers=self.headers("ADMIN", {"sub": str(uuid4())})).status_code)

    def test_body_headers_id_and_conflict_validation(self):
        for body in ({**self.body, "quantity": True}, {**self.body, "extra": "value"}, {}, []):
            self.assertError(self.start(body=body), 400, "VALIDATION_ERROR")
        headers = self.headers()
        del headers["Idempotency-Key"]
        self.assertError(self.start(headers=headers), 400, "VALIDATION_ERROR")
        self.assertError(self.start(headers={**self.headers(), "X-Correlation-Id": "bad value"}), 400, "VALIDATION_ERROR")
        self.assertError(self.client.post("/api/v1/sagas/receive-supplies", headers=self.headers(), content="not-json"), 400, "VALIDATION_ERROR")
        self.assertError(self.client.post("/api/v1/sagas/receive-supplies", headers={**self.headers(), "Content-Type": "application/json"}, content="{"), 400, "VALIDATION_ERROR")
        self.assertError(self.client.get("/api/v1/sagas/not-uuid", headers=self.headers()), 400, "VALIDATION_ERROR")
        self.assertError(self.client.get("/api/v1/sagas/" + str(uuid4()), headers=self.headers()), 404, "NOT_FOUND")
        created = self.start()
        self.assertError(self.start(body={**self.body, "quantity": 11}), 409, "IDEMPOTENCY_CONFLICT")
        self.assertError(self.client.post(created.headers["Location"] + "/resume", json={"unexpected": True}, headers=self.headers()), 400, "VALIDATION_ERROR")

    def test_transport_limits_duplicates_and_generic_errors(self):
        self.assertError(self.client.post("/api/v1/sagas/receive-supplies", headers=self.headers(), content=b"x" * 65537), 413, "PAYLOAD_TOO_LARGE")
        self.assertError(self.client.get("/health", headers=[("X-Correlation-Id", "a"), ("X-Correlation-Id", "b")]), 400, "VALIDATION_ERROR")
        self.assertError(self.client.get("/unknown"), 404, "NOT_FOUND")
        self.assertError(self.client.put("/health"), 405, "METHOD_NOT_ALLOWED")
        with patch.object(self.runner, "start", side_effect=RuntimeError("private postgres password and URL")):
            response = self.start()
        self.assertError(response, 500, "INTERNAL_ERROR")
        self.assertNotIn("password", response.text)

    def test_transient_state_and_resume_never_expose_failure_details(self):
        self.participants.failure = ("prepare-entry", "PRIVATE_UPSTREAM_CODE", True)
        created = self.start()
        self.assertEqual(201, created.status_code)
        self.assertEqual("RUNNING", created.json()["status"])
        self.assertEqual("prepare-entry", created.json()["failedStep"])
        self.assertNotIn("PRIVATE_UPSTREAM_CODE", created.text)
        self.participants.failure = None
        response = self.client.post(created.headers["Location"] + "/resume", json={}, headers=self.headers())
        self.assertEqual("COMPLETED", response.json()["status"])

    def test_health_requires_storage_and_service_credential(self):
        self.assertEqual({"status": "UP"}, self.client.get("/health").json())
        with patch.object(self.store, "health", side_effect=Failure(503, "WORKFLOW_STORAGE_UNAVAILABLE")):
            self.assertError(self.client.get("/health"), 503, "WORKFLOW_STORAGE_UNAVAILABLE")
        with patch.object(self.runner, "service_token", side_effect=StepError("PRIVATE_IDENTITY_DETAIL", True)):
            self.assertError(self.client.get("/health"), 503, "SERVICE_IDENTITY_UNAVAILABLE")

    def test_jwks_public_key_is_fetched_and_signature_verified(self):
        document = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key()))
        document.update(kid="server-test-key", use="sig", alg="RS256")
        payload = json.dumps({"keys": [document]}).encode()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            auth = HumanTokens("http://127.0.0.1:" + str(server.server_port) + "/jwks")
            self.assertEqual(self.actor, auth.authenticate("Bearer " + self.token())["sub"])
            with self.assertRaises(Failure) as error:
                auth.authenticate("Bearer " + self.token(key=self.wrong))
            self.assertEqual(401, error.exception.status)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
