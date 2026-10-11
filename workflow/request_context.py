import asyncio
import logging
import re
from uuid import uuid4

from starlette.responses import JSONResponse

from workflow.model import Failure, invalid

logger = logging.getLogger("workflow.requests")


def error_content(failure, trace):
    content = {"error": failure.code, "message": "Solicitud no disponible.", "traceId": trace}
    if failure.field:
        content["details"] = [{"field": failure.field, "message": "Valor invalido."}]
    return content


class RequestContext:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        pairs = [(key.lower(), value.decode("latin1")) for key, value in scope["headers"]]
        headers = dict(pairs)
        supplied = headers.get(b"x-correlation-id")
        bad = supplied is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", supplied)
        trace = str(uuid4()) if supplied is None or bad else supplied
        scope.setdefault("state", {})["trace_id"] = trace
        status, started = 500, False

        async def correlated(message):
            nonlocal status, started
            if message["type"] == "http.response.start":
                status, started = message["status"], True
                message["headers"] = [(key, value) for key, value in message.get("headers", []) if key.lower() != b"x-correlation-id"]
                message["headers"].append((b"x-correlation-id", trace.encode("ascii")))
            await send(message)

        try:
            for name in (b"authorization", b"idempotency-key", b"content-length", b"content-type", b"x-correlation-id"):
                if sum(key == name for key, _ in pairs) > 1:
                    raise invalid(name.decode("ascii"))
            if bad:
                raise invalid("X-Correlation-Id")
            length = headers.get(b"content-length")
            if length is not None and not re.fullmatch(r"[0-9]{1,10}", length):
                raise invalid("Content-Length")
            if length is not None and int(length) > 65536:
                raise Failure(413, "PAYLOAD_TOO_LARGE")
            data = bytearray()
            async with asyncio.timeout(15):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(data) + len(chunk) > 65536:
                        raise Failure(413, "PAYLOAD_TOO_LARGE")
                    data.extend(chunk)
                    if not message.get("more_body", False):
                        break
            delivered = False

            async def buffered():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(data), "more_body": False}
                return await receive()

            await self.app(scope, buffered, correlated)
        except TimeoutError:
            await JSONResponse(error_content(Failure(408, "REQUEST_TIMEOUT"), trace), status_code=408)(scope, receive, correlated)
        except Failure as failure:
            await JSONResponse(error_content(failure, trace), status_code=failure.status)(scope, receive, correlated)
        except Exception:
            if started:
                raise
            await JSONResponse(error_content(Failure(500, "INTERNAL_ERROR"), trace), status_code=500)(scope, receive, correlated)
        finally:
            logger.info("request method=%s status=%s traceId=%s", scope["method"], status, trace)
