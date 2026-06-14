"""The wide event: one structured record per request or job, persisted whole."""

import contextlib
import contextvars
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Iterator, Literal

from pydantic import BaseModel

from . import db

EventType = Literal["request", "job"]
EventLevel = Literal["info", "warn", "error"]

_LEVEL_RANK: dict[EventLevel, int] = {"info": 0, "warn": 1, "error": 2}


class ClientContext(BaseModel):
    kind: Literal["web", "cli"]
    page: str | None = None
    user_agent: str | None = None
    action: str | None = None


class Performance(BaseModel):
    db_queries: int = 0
    db_duration_ms: float = 0.0


class ErrorInfo(BaseModel):
    code: str
    message: str


_current: contextvars.ContextVar["WideEvent | None"] = contextvars.ContextVar(
    "current_wide_event", default=None
)


def get_current_event() -> "WideEvent | None":
    return _current.get()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WideEvent:
    def __init__(self, kind: EventType, source: str, trace_id: str | None = None) -> None:
        self.id = uuid.uuid4().hex
        self.kind: EventType = kind
        self.source = source
        self.trace_id = trace_id or f"trc_{uuid.uuid4().hex[:12]}"
        self.created_at = _now_iso()
        self._start = time.perf_counter()

        self.level: EventLevel = "info"
        self.has_error = False
        self.method: str | None = None
        self.path: str | None = None
        self.status_code: int | None = None
        self.duration_ms: float | None = None
        self.job_name: str | None = None

        self.client: ClientContext | None = None
        self.performance = Performance()
        self.error: ErrorInfo | None = None
        self.business: dict = {}

    def escalate(self, level: EventLevel) -> "WideEvent":
        if _LEVEL_RANK[level] > _LEVEL_RANK[self.level]:
            self.level = level
            if level in ("warn", "error"):
                self.has_error = True
        return self

    def set_error(self, code: str, message: str) -> "WideEvent":
        self.escalate("error")
        self.error = ErrorInfo(code=code, message=message)
        return self

    def set_business(self, key: str, value: object) -> "WideEvent":
        self.business[key] = value
        return self

    def add_db_query(self, duration_ms: float) -> None:
        self.performance.db_queries += 1
        self.performance.db_duration_ms = round(self.performance.db_duration_ms + duration_ms, 3)

    def finalize(self) -> "WideEvent":
        if self.duration_ms is None:
            self.duration_ms = round((time.perf_counter() - self._start) * 1000, 3)
        return self

    def _headline(self) -> str | None:
        if self.error is not None:
            return self.error.message
        for value in self.business.values():
            if isinstance(value, dict) and isinstance(value.get("message"), str):
                return value["message"]
        return None

    def to_data(self) -> dict:
        data: dict = {"performance": self.performance.model_dump()}
        if self.kind == "request":
            data["request"] = {"method": self.method, "path": self.path}
        if self.job_name is not None:
            data["job"] = {"name": self.job_name}
        if self.client is not None:
            data["client"] = self.client.model_dump()
        if self.error is not None:
            data["error"] = self.error.model_dump()
        if self.business:
            data["business"] = self.business
        headline = self._headline()
        if headline is not None:
            data["detail"] = headline
        return data

    def persist(self) -> None:
        self.finalize()
        conn = db.connect()
        try:
            conn.execute(
                "INSERT INTO wide_events (id, trace_id, type, level, source, path, method,"
                " status_code, duration_ms, error, data, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self.id,
                    self.trace_id,
                    self.kind,
                    self.level,
                    self.source,
                    self.path,
                    self.method,
                    self.status_code,
                    self.duration_ms,
                    int(self.has_error),
                    json.dumps(self.to_data(), default=str),
                    self.created_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()


@contextlib.contextmanager
def run_job(name: str, source: str, trace_id: str | None = None) -> Iterator[WideEvent]:
    event = WideEvent("job", source, trace_id)
    event.job_name = name
    token = _current.set(event)
    try:
        yield event
    except Exception as exc:
        event.set_error(type(exc).__name__, str(exc))
        raise
    finally:
        _current.reset(token)
        event.persist()
