from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import URLError
from uuid import uuid4

from workflow.core import Runner, StepError
from workflow.model import Reception, Saga, STEPS
from workflow.participants import HttpParticipants
from test_core import MemoryStore


class HttpParticipantsTests(unittest.TestCase):
    def setUp(self):
        self.calls, self.replies, self.waits = [], [], []
        self.truncate_next = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                owner.calls.append((self.path, dict(self.headers), json.loads(raw)))
                status, body, content_type = owner.replies.pop(0)
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                truncated = owner.truncate_next
                owner.truncate_next = False
                self.send_header("Content-Length", str(len(body) + (100 if truncated else 0)))
                if status == 302:
                    self.send_header("Location", owner.url + "/credential-leak")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = "http://127.0.0.1:" + str(self.server.server_port)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = HttpParticipants(self.url, self.url, sleep=self.waits.append)
        self.actor = str(uuid4())
        self.body = {"supplierId": str(uuid4()), "medicationId": str(uuid4()), "reference": "REF-HTTP",
                     "lotNumber": "LOT-HTTP", "expiresOn": "2099-01-01", "quantity": 10}
        self.saga = Saga(str(uuid4()), self.actor, Reception.parse(self.body), "trace-http", datetime.now(timezone.utc))
        self.token = "service.credential.signature"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def reply(self, status=201, body=None):
        body = body if body is not None else {"id": str(uuid4()), "status": "PENDING"}
        self.replies.append((status, json.dumps(body).encode(), "application/json"))
        return body

    def forward(self, step="prepare-receipt", saga=None, token=None):
        saga = saga or self.saga
        return self.client.forward(step, saga, saga.id + ":" + step, token or self.token)

    def error(self, code, retryable, callback=None):
        with self.assertRaises(StepError) as caught:
            (callback or self.forward)()
        self.assertEqual((code, retryable), (caught.exception.code, caught.exception.retryable))

    def test_four_steps_send_service_token_original_trace_and_exact_payload(self):
        receipt, entry = str(uuid4()), str(uuid4())
        self.reply(201, {"id": receipt, "status": "PENDING"})
        self.reply(201, {"id": entry, "status": "PENDING"})
        self.reply(200, {"id": receipt, "status": "CONFIRMED"})
        self.reply(200, {"id": entry, "status": "ACTIVE"})
        runner = Runner(MemoryStore(), self.client, lambda: self.token)
        saga, _ = runner.start(self.actor, ["INVENTORY"], "http-reception-001", self.body, "trace-http")
        self.assertEqual("COMPLETED", saga.status)
        expected_paths = ["/api/v1/receipts", "/api/v1/entries", "/api/v1/receipts/" + receipt + "/confirm",
                          "/api/v1/entries/" + entry + "/activate"]
        self.assertEqual(expected_paths, [call[0] for call in self.calls])
        self.assertEqual({**self.body, "workflowId": saga.id, "initiatedBy": self.actor}, self.calls[0][2])
        self.assertEqual({key: value for key, value in self.body.items() if key != "reference"}, self.calls[1][2])
        self.assertEqual([{}, {}], [call[2] for call in self.calls[2:]])
        for step, (_, headers, _) in zip(STEPS, self.calls):
            self.assertEqual("Bearer " + self.token, headers["Authorization"])
            self.assertEqual(saga.id + ":" + step, headers["Idempotency-Key"])
            self.assertEqual("trace-http", headers["X-Correlation-Id"])
        runner.start(self.actor, ["INVENTORY"], "http-reception-001", self.body, "new-trace")
        self.assertEqual(4, len(self.calls))

    def test_only_transient_statuses_retry_with_identical_request(self):
        for status in (429, 500, 503):
            with self.subTest(status=status):
                self.calls.clear()
                self.waits.clear()
                self.reply(status, {"secret": "private upstream address"})
                expected = self.reply()
                self.assertEqual(expected, self.forward())
                self.assertEqual(self.calls[0], self.calls[1])
                self.assertEqual(1, len(self.waits))
                self.assertTrue(0.05 <= self.waits[0] <= 0.15)

    def test_transient_retry_is_bounded(self):
        self.reply(503)
        self.reply(503)
        self.error("UPSTREAM_UNAVAILABLE", True)
        self.assertEqual(2, len(self.calls))

    def test_rejection_authentication_and_redirect_are_not_automatically_retried(self):
        for status in (400, 404, 409, 422, 401, 403, 302):
            with self.subTest(status=status):
                self.calls.clear()
                self.reply(status, {"secret": "never expose upstream body"})
                code = "SERVICE_IDENTITY_REJECTED" if status in (401, 403) else "INVALID_PARTICIPANT_RESPONSE" if status == 302 else "UPSTREAM_REJECTED"
                self.error(code, status in (401, 403, 302))
                self.assertEqual(1, len(self.calls))
        self.assertEqual([], self.waits)

    def test_invalid_responses_are_uncertain_not_permanent_or_retried(self):
        for raw, kind in ((b"not-json", "application/json"), (b"[]", "application/json"),
                          (b"{}", "text/html"), (b"x" * 65537, "application/json")):
            with self.subTest(kind=kind, size=len(raw)):
                self.calls.clear()
                self.replies.append((200, raw, kind))
                self.error("INVALID_PARTICIPANT_RESPONSE", True)
                self.assertEqual(1, len(self.calls))

    def test_lost_response_retries_without_changing_key(self):
        expected = self.reply()
        original = self.client.http.open
        attempts = []

        def lose_first(request, **kwargs):
            attempts.append(request)
            if len(attempts) == 1:
                raise URLError("connection lost")
            return original(request, **kwargs)

        with patch.object(self.client.http, "open", side_effect=lose_first) as opened:
            self.assertEqual(expected, self.forward())
            self.assertEqual(2, opened.call_count)
            first, second = [call.args[0] for call in opened.call_args_list]
            for header in ("Authorization", "Content-type", "Accept", "Idempotency-key", "X-correlation-id"):
                self.assertEqual(first.get_header(header), second.get_header(header))
            self.assertEqual(first.data, second.data)

    def test_partial_response_retries_same_receipt_identity_and_intention(self):
        expected = {"id": str(uuid4()), "status": "PENDING"}
        self.reply(201, expected)
        self.reply(200, expected)
        self.truncate_next = True
        self.assertEqual(expected, self.forward())
        self.assertEqual(2, len(self.calls))
        self.assertEqual(self.calls[0], self.calls[1])

    def test_network_failure_stops_after_two_attempts(self):
        with patch.object(self.client.http, "open", side_effect=TimeoutError("private network details")) as opened:
            self.error("UPSTREAM_UNAVAILABLE", True)
            self.assertEqual(2, opened.call_count)

    def test_invalid_token_performs_no_http_call(self):
        self.error("SERVICE_IDENTITY_UNAVAILABLE", True, lambda: self.forward(token="human-token\r\ninjected"))
        self.assertEqual([], self.calls)

    def test_configured_origins_reject_credentials_and_dynamic_paths(self):
        for value in ("file:///etc/passwd", "http://user:password@host", "http://host/path",
                      "http://host?url=evil", "http://host#fragment", "http://host\r\ninjected"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                HttpParticipants(value, self.url)
        for timeout in (0, -1, 3):
            with self.assertRaises(ValueError):
                HttpParticipants(self.url, self.url, timeout=timeout)
