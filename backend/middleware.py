"""Per-request wide event: opened before the handler, stamps the response, persisted after."""

import logging
from urllib.parse import unquote

from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .wide_event import ClientContext, WideEvent, _current

log = logging.getLogger("wide_event")


def _apply_client(event: WideEvent, raw: str) -> None:
    try:
        event.client = ClientContext.model_validate_json(unquote(raw))
    except (ValidationError, ValueError) as exc:
        event.escalate("warn")
        event.set_business(
            "client_validation_error",
            {"message": "invalid x-client header", "raw": raw[:120], "error": str(exc)[:200]},
        )
        log.warning("invalid x-client header: %s", exc)


class WideEventMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        event = WideEvent("request", "backend", request.headers.get("x-trace-id"))
        event.method = request.method
        event.path = request.url.path

        client_raw = request.headers.get("x-client")
        if client_raw:
            _apply_client(event, client_raw)

        token = _current.set(event)
        try:
            response = await call_next(request)
            event.status_code = response.status_code
            if response.status_code >= 500:
                event.escalate("error")
            elif response.status_code >= 400:
                event.escalate("warn")
            response.headers["x-trace-id"] = event.trace_id
            response.headers["x-event-id"] = event.id
            return response
        except Exception as exc:
            event.set_error(type(exc).__name__, str(exc))
            event.status_code = 500
            raise
        finally:
            _current.reset(token)
            event.persist()
