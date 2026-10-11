from dataclasses import asdict, dataclass
from datetime import date, datetime
from hashlib import sha256
import json
import re
from uuid import UUID


class Failure(Exception):
    def __init__(self, status, code, field=None):
        super().__init__(code)
        self.status, self.code, self.field = status, code, field


def invalid(field):
    return Failure(400, "VALIDATION_ERROR", field)


def identifier(value, field):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError):
        raise invalid(field) from None
    return value


@dataclass(frozen=True)
class Reception:
    supplierId: str
    medicationId: str
    reference: str
    lotNumber: str
    expiresOn: str
    quantity: int

    @classmethod
    def parse(cls, body):
        fields = set(cls.__dataclass_fields__)
        if not isinstance(body, dict) or set(body) - fields:
            raise invalid("body")
        if fields - set(body):
            raise invalid(sorted(fields - set(body))[0])
        normalized = dict(body)
        for field in ("supplierId", "medicationId"):
            normalized[field] = identifier(body[field], field)
        for field in ("reference", "lotNumber"):
            value = body[field]
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,40}", value.strip()):
                raise invalid(field)
            normalized[field] = value.strip().upper()
        try:
            value = body["expiresOn"]
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                raise ValueError()
            date.fromisoformat(value)
        except ValueError:
            raise invalid("expiresOn") from None
        if type(body["quantity"]) is not int or not 1 <= body["quantity"] <= 1000000:
            raise invalid("quantity")
        return cls(**normalized)

    def fingerprint(self):
        return sha256(json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


STEPS = ("prepare-receipt", "prepare-entry", "confirm-receipt", "activate-entry")


@dataclass(frozen=True)
class Saga:
    id: str
    actor: str
    request: Reception
    trace: str
    created_at: datetime
    status: str = "RUNNING"
    completed: tuple[str, ...] = ()
    receipt_id: str | None = None
    entry_id: str | None = None
    failed_step: str | None = None
    failure_code: str | None = None

    def resource(self):
        return {"id": self.id, "createdBy": self.actor, "createdAt": self.created_at.isoformat(),
                "status": self.status, "completedSteps": list(self.completed), "failedStep": self.failed_step,
                "receiptId": self.receipt_id, "entryId": self.entry_id}
