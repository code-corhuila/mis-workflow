from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from workflow.model import Failure, Reception, Saga


class PostgresStore:
    def __init__(self, dsn):
        self.dsn = dsn
        self.owner = ContextVar("workflow_connection", default=None)

    @contextmanager
    def connection(self):
        current = self.owner.get()
        try:
            if current is not None:
                yield current[1]
            else:
                with psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row,
                                      connect_timeout=5) as connection:
                    yield connection
        except psycopg.Error:
            raise Failure(503, "WORKFLOW_STORAGE_UNAVAILABLE") from None

    @staticmethod
    def decode(row):
        if row is None:
            raise Failure(404, "NOT_FOUND")
        try:
            return Saga(str(row["id"]), str(row["actor"]), Reception.parse(row["request"]),
                        row["trace"], row["created_at"], row["status"], tuple(row["completed"]),
                        str(row["receipt_id"]) if row["receipt_id"] else None,
                        str(row["entry_id"]) if row["entry_id"] else None,
                        row["failed_step"], row["failure_code"])
        except (Failure, KeyError, TypeError, ValueError):
            raise Failure(503, "INVALID_SAGA_STATE") from None

    def create(self, actor, key, request, trace):
        with self.connection() as connection:
            row = connection.execute("""
                INSERT INTO workflow.saga (id, actor, request_key, fingerprint, request, trace)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (actor, request_key) DO NOTHING RETURNING *
                """, (uuid4(), actor, key, request.fingerprint(), Jsonb(asdict(request)), trace)).fetchone()
            created = row is not None
            if not created:
                row = connection.execute("SELECT * FROM workflow.saga WHERE actor=%s AND request_key=%s",
                                         (actor, key)).fetchone()
                if row is None:
                    raise Failure(503, "WORKFLOW_STORAGE_UNAVAILABLE")
                if row["fingerprint"] != request.fingerprint():
                    raise Failure(409, "IDEMPOTENCY_CONFLICT")
            return self.decode(row), created

    def get(self, saga_id):
        with self.connection() as connection:
            return self.decode(connection.execute("SELECT * FROM workflow.saga WHERE id=%s",
                                                   (saga_id,)).fetchone())

    def save(self, saga):
        owner = self.owner.get()
        if owner is None or owner[0] != saga.id:
            raise Failure(503, "SAGA_LOCK_REQUIRED")
        # Never reconnect after losing the session that owns the execution lock.
        with self.connection() as connection:
            result = connection.execute("""
                UPDATE workflow.saga SET status=%s, completed=%s, receipt_id=%s, entry_id=%s,
                    failed_step=%s, failure_code=%s, updated_at=clock_timestamp()
                WHERE id=%s
                """, (saga.status, list(saga.completed), saga.receipt_id, saga.entry_id,
                      saga.failed_step, saga.failure_code, saga.id))
            if result.rowcount != 1:
                raise Failure(404, "NOT_FOUND")

    @contextmanager
    def exclusive(self, saga_id):
        if self.owner.get() is not None:
            raise Failure(409, "SAGA_BUSY")
        with self.connection() as connection:
            locked = connection.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS locked",
                                        (saga_id,)).fetchone()["locked"]
            if not locked:
                raise Failure(409, "SAGA_BUSY")
            token = self.owner.set((saga_id, connection))
            try:
                yield
            finally:
                self.owner.reset(token)
                # Closing this dedicated connection releases its session lock, even on failure.

    def health(self):
        with self.connection() as connection:
            version = connection.execute("SELECT version FROM workflow.schema_version").fetchall()
            connection.execute("SELECT id FROM workflow.saga LIMIT 0")
            if version != [{"version": 1}]:
                raise Failure(503, "WORKFLOW_SCHEMA_MISMATCH")
