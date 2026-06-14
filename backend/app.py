"""FastAPI app: the single API both the frontend and CLI call."""

import json
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import service
from .middleware import WideEventMiddleware
from .unit_of_work import unit_of_work
from .wide_event import get_current_event

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="Galatiq Invoice Pipeline (scaffold)")
app.add_middleware(WideEventMiddleware)

api = APIRouter(prefix="/api")


@api.post("/hello")
async def hello() -> dict:
    event = get_current_event()
    with unit_of_work(event) as uow:
        message = service.greet(uow)

    if event is not None:
        event.set_business("greeting", message)

    return {
        "message": message,
        "traceId": event.trace_id if event else None,
        "eventId": event.id if event else None,
    }


@api.get("/events")
async def list_events(limit: int = 25) -> list[dict]:
    event = get_current_event()
    with unit_of_work(event) as uow:
        rows = uow.query(
            "SELECT id, trace_id, type, level, source, path, method, status_code,"
            " duration_ms, error, data, created_at FROM wide_events"
            " ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
    return [{**dict(r), "error": bool(r["error"]), "data": json.loads(r["data"])} for r in rows]


app.include_router(api)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
