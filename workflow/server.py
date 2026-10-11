from contextlib import asynccontextmanager
import json
import os

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg.conninfo import make_conninfo
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from workflow.auth import HumanTokens
from workflow.core import Runner, StepError
from workflow.model import Failure, invalid
from workflow.participants import HttpParticipants
from workflow.postgres import PostgresStore
from workflow.request_context import RequestContext, error_content
from workflow.service_identity import ServiceTokenFile


def runtime():
    store = PostgresStore(make_conninfo(host=os.environ["WORKFLOW_PG_HOST"],
        dbname=os.environ["WORKFLOW_PG_DATABASE"], user="workflow_app",
        password=os.environ["WORKFLOW_PG_PASSWORD"], options="-c statement_timeout=5000 -c lock_timeout=2000"))
    service = ServiceTokenFile(os.environ["WORKFLOW_SERVICE_TOKEN_PATH"], os.environ["WORKFLOW_PUBLIC_KEY_PATH"],
        os.environ["WORKFLOW_SERVICE_SUBJECT"], os.environ["WORKFLOW_KEY_ID"])
    participants = HttpParticipants(os.environ["WORKFLOW_SUPPLIERS_URL"], os.environ["WORKFLOW_INVENTORY_URL"])
    return Runner(store, participants, service), HumanTokens(os.environ["WORKFLOW_JWKS_URL"])


def create_app(runner=None, tokens=None):
    @asynccontextmanager
    async def lifespan(app):
        if runner is None:
            app.state.runner, app.state.tokens = runtime()
        else:
            if tokens is None:
                raise ValueError("Explicit authentication required for an injected runner")
            app.state.runner, app.state.tokens = runner, tokens
        await run_in_threadpool(app.state.runner.store.health)
        yield

    app = FastAPI(title="MediStock - Workflow", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(RequestContext)

    @app.exception_handler(Failure)
    async def failure(request, error):
        headers = {"WWW-Authenticate": "Bearer"} if error.status == 401 else None
        return JSONResponse(error_content(error, request.state.trace_id), status_code=error.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation(request, error):
        return await failure(request, invalid("body"))

    @app.exception_handler(HTTPException)
    async def http(request, error):
        code = "NOT_FOUND" if error.status_code == 404 else "METHOD_NOT_ALLOWED" if error.status_code == 405 else "VALIDATION_ERROR"
        return JSONResponse(error_content(Failure(error.status_code, code), request.state.trace_id),
                            status_code=error.status_code, headers=error.headers)

    def identity(request: Request):
        return request.app.state.tokens.authenticate(request.headers.get("Authorization"))

    async def body(request, empty=False):
        raw = await request.body()
        if empty and not raw:
            return {}
        if request.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            raise invalid("body")
        try:
            return json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise invalid("body") from None

    @app.get("/health")
    def health(request: Request):
        request.app.state.runner.store.health()
        try:
            if not request.app.state.runner.service_token():
                raise StepError("SERVICE_IDENTITY_UNAVAILABLE", True)
        except StepError:
            raise Failure(503, "SERVICE_IDENTITY_UNAVAILABLE") from None
        return {"status": "UP"}

    @app.post("/api/v1/sagas/receive-supplies")
    async def start(request: Request, claims=Depends(identity)):
        saga, created = await run_in_threadpool(request.app.state.runner.start, claims["sub"], claims["roles"],
            request.headers.get("Idempotency-Key"), await body(request), request.state.trace_id)
        return JSONResponse(saga.resource(), status_code=201 if created else 200,
                            headers={"Location": "/api/v1/sagas/" + saga.id})

    @app.get("/api/v1/sagas/{saga_id}")
    def find(saga_id: str, request: Request, claims=Depends(identity)):
        return request.app.state.runner.get(claims["sub"], claims["roles"], saga_id).resource()

    @app.post("/api/v1/sagas/{saga_id}/resume")
    async def resume(saga_id: str, request: Request, claims=Depends(identity)):
        if await body(request, empty=True) != {}:
            raise invalid("body")
        return (await run_in_threadpool(request.app.state.runner.resume, claims["sub"], claims["roles"], saga_id)).resource()

    return app


app = create_app()
