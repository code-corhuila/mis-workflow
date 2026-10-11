from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from threading import Thread
import time
import unittest
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
from psycopg.conninfo import conninfo_to_dict

import test_postgres


@unittest.skipUnless(os.environ.get("WORKFLOW_TEST_DSN"), "Disposable PostgreSQL not configured")
class RuntimeTests(unittest.TestCase):
    def test_real_server_process_restarts_with_postgres_and_separate_service_jwt(self):
        test_postgres.PostgresTests.setUpClass()
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = private.public_key()
        actor, subject = str(uuid4()), str(uuid4())
        now = int(time.time())
        claims = {"iss": "mis-security", "aud": "mis-api", "iat": now, "exp": now + 300}
        human = jwt.encode({**claims, "sub": actor, "roles": ["INVENTORY"]}, private,
                           algorithm="RS256", headers={"kid": "runtime-key"})
        service = jwt.encode({**claims, "sub": subject, "roles": ["INVENTORY"], "service": "mis-workflow"},
                             private, algorithm="RS256", headers={"kid": "runtime-key"})
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(public))
        jwk.update(kid="runtime-key", use="sig", alg="RS256")
        calls, responses = [], {}

        class Participants(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self.reply(200, {"keys": [jwk]})

            def do_POST(self):
                verified = jwt.decode(self.headers["Authorization"].split()[1], public,
                                      algorithms=["RS256"], issuer="mis-security", audience="mis-api")
                if verified.get("service") != "mis-workflow" or verified["sub"] != subject:
                    return self.reply(403, {})
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append((self.path, self.headers["Idempotency-Key"], self.headers["X-Correlation-Id"], body))
                key = self.headers["Idempotency-Key"]
                created = key not in responses
                if created:
                    if self.path in {"/api/v1/receipts", "/api/v1/entries"}:
                        responses[key] = {"id": str(uuid4()), "status": "PENDING"}
                    else:
                        responses[key] = {"id": self.path.split("/")[-2],
                                          "status": "CONFIRMED" if self.path.endswith("/confirm") else "ACTIVE"}
                self.reply(201 if created and self.path in {"/api/v1/receipts", "/api/v1/entries"} else 200, responses[key])

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Participants)
        thread = Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(upstream.server_close)
        self.addCleanup(upstream.shutdown)
        origin = "http://127.0.0.1:" + str(upstream.server_port)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        folder = Path(directory.name)
        (folder / "public.pem").write_bytes(public.public_bytes(serialization.Encoding.PEM,
                                                serialization.PublicFormat.SubjectPublicKeyInfo))
        (folder / "service.jwt").write_text(service, encoding="ascii")
        options = conninfo_to_dict(os.environ["WORKFLOW_TEST_DSN"])
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            port = available.getsockname()[1]
        url = "http://127.0.0.1:" + str(port)
        environment = {**os.environ, "WORKFLOW_PG_HOST": options["host"], "WORKFLOW_PG_DATABASE": "workflow_test",
            "WORKFLOW_PG_PASSWORD": "", "WORKFLOW_SERVICE_TOKEN_PATH": str(folder / "service.jwt"),
            "WORKFLOW_PUBLIC_KEY_PATH": str(folder / "public.pem"), "WORKFLOW_SERVICE_SUBJECT": subject,
            "WORKFLOW_KEY_ID": "runtime-key", "WORKFLOW_JWKS_URL": origin + "/jwks",
            "WORKFLOW_SUPPLIERS_URL": origin, "WORKFLOW_INVENTORY_URL": origin}

        def stop(process):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        def start():
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "workflow.server:app", "--host",
                "127.0.0.1", "--port", str(port), "--no-access-log"], env=environment,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.addCleanup(stop, process)
            for _ in range(100):
                if process.poll() is not None:
                    self.fail("Workflow process exited before becoming healthy")
                try:
                    if httpx.get(url + "/health", timeout=1, trust_env=False).status_code == 200:
                        return process
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            self.fail("Workflow process did not become healthy")

        first_process = start()
        body = {"supplierId": str(uuid4()), "medicationId": str(uuid4()), "reference": "REF-RUNTIME",
                "lotNumber": "LOT-RUNTIME", "expiresOn": "2099-01-01", "quantity": 10}
        headers = {"Authorization": "Bearer " + human, "Idempotency-Key": "runtime-" + str(uuid4()),
                   "X-Correlation-Id": "trace-runtime"}
        with httpx.Client(base_url=url, timeout=10, trust_env=False) as client:
            created = client.post("/api/v1/sagas/receive-supplies", json=body, headers=headers)
            self.assertEqual(201, created.status_code, created.text)
            self.assertEqual("COMPLETED", created.json()["status"])
            self.assertEqual(actor, created.json()["createdBy"])
            self.assertEqual(4, len(calls))
            self.assertEqual(created.json()["id"], calls[0][3]["workflowId"])
            self.assertEqual(actor, calls[0][3]["initiatedBy"])
            self.assertTrue(all(call[2] == "trace-runtime" for call in calls))
            first_process.kill()
            first_process.wait(timeout=5)
            start()
            replay = client.post("/api/v1/sagas/receive-supplies", json=body, headers=headers)
            self.assertEqual(200, replay.status_code, replay.text)
            self.assertEqual(created.json(), replay.json())
            self.assertEqual(created.json(), client.get(created.headers["Location"], headers=headers).json())
            self.assertEqual(created.json(), client.post(created.headers["Location"] + "/resume", headers=headers).json())
            self.assertEqual(4, len(calls))
