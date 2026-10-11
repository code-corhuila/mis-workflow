from dataclasses import asdict
from http.client import HTTPException, IncompleteRead
import json
import random
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from workflow.core import StepError
from workflow.model import identifier


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpParticipants:
    def __init__(self, suppliers_url, inventory_url, *, timeout=1.5, sleep=time.sleep):
        self.suppliers = self.origin(suppliers_url)
        self.inventory = self.origin(inventory_url)
        if not 0 < timeout <= 2:
            raise ValueError("Participant timeout must be between zero and two seconds")
        self.timeout, self.sleep = timeout, sleep
        # Never send service credentials to a redirect target or an environment proxy.
        self.http = build_opener(ProxyHandler({}), NoRedirect())

    @staticmethod
    def origin(value):
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or re.search(r"[\s\\]", value)):
            raise ValueError("Participant URL must be a configured HTTP origin")
        parsed.port
        return value.rstrip("/")

    def command(self, step, saga):
        if step == "prepare-receipt":
            return self.suppliers + "/api/v1/receipts", {
                **asdict(saga.request), "workflowId": saga.id, "initiatedBy": saga.actor}
        if step == "prepare-entry":
            body = asdict(saga.request)
            del body["reference"]
            return self.inventory + "/api/v1/entries", body
        if step in {"confirm-receipt", "activate-entry"}:
            value = saga.receipt_id if step == "confirm-receipt" else saga.entry_id
            participant = identifier(value, "participantId")
            route = "/api/v1/receipts/" + participant + "/confirm" if step == "confirm-receipt" else "/api/v1/entries/" + participant + "/activate"
            return (self.suppliers if step == "confirm-receipt" else self.inventory) + route, {}
        raise ValueError("Unsupported workflow step")

    def forward(self, step, saga, key, service_token):
        if (not isinstance(service_token, str) or len(service_token) > 16384
                or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", service_token)):
            raise StepError("SERVICE_IDENTITY_UNAVAILABLE", True)
        url, body = self.command(step, saga)
        encoded = json.dumps(body, separators=(",", ":")).encode()
        headers = {"Authorization": "Bearer " + service_token, "Content-Type": "application/json",
                   "Accept": "application/json", "Idempotency-Key": key, "X-Correlation-Id": saga.trace}
        for attempt in range(2):
            try:
                with self.http.open(Request(url, data=encoded, headers=headers, method="POST"),
                                    timeout=self.timeout) as response:
                    accepted = {200, 201} if step.startswith("prepare-") else {200}
                    if response.status not in accepted:
                        raise StepError("INVALID_PARTICIPANT_RESPONSE", True)
                    lengths = response.headers.get_all("Content-Length", [])
                    if lengths and (len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,10}", lengths[0])
                                    or int(lengths[0]) > 65536):
                        raise StepError("INVALID_PARTICIPANT_RESPONSE", True)
                    raw = response.read(65537)
                    if lengths and len(raw) != int(lengths[0]):
                        raise IncompleteRead(raw)
                    if len(raw) > 65536 or response.headers.get_content_type() != "application/json":
                        raise StepError("INVALID_PARTICIPANT_RESPONSE", True)
                    try:
                        result = json.loads(raw)
                        if not isinstance(result, dict):
                            raise ValueError()
                        return result
                    except (ValueError, UnicodeDecodeError):
                        raise StepError("INVALID_PARTICIPANT_RESPONSE", True) from None
            except HTTPError as error:
                status = error.code
                error.close()
                if status in {401, 403}:
                    raise StepError("SERVICE_IDENTITY_REJECTED", True) from None
                retryable = status == 429 or 500 <= status <= 599
                if not retryable:
                    # Never persist upstream error bodies or confidential endpoint details.
                    raise StepError("UPSTREAM_REJECTED" if 400 <= status <= 499 else "INVALID_PARTICIPANT_RESPONSE",
                                    300 <= status <= 399) from None
            except (URLError, OSError, HTTPException):
                pass
            if attempt == 1:
                raise StepError("UPSTREAM_UNAVAILABLE", True) from None
            self.sleep(random.uniform(0.05, 0.15))
