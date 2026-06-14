"""CLI as an API client: in-process via ASGITransport by default, a base URL for a live backend."""

import json
import platform
import uuid
from urllib.parse import quote

import httpx
from rich.console import Console

console = Console()


def _client(server: str | None) -> httpx.AsyncClient:
    if server:
        return httpx.AsyncClient(base_url=server, timeout=30.0)
    from backend.app import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cli")


def _client_header(action: str) -> str:
    ctx = {
        "kind": "cli",
        "page": "cli",
        "user_agent": f"cli/python-{platform.python_version()}",
        "action": action,
    }
    return quote(json.dumps(ctx))


async def hello(server: str | None = None) -> None:
    trace_id = f"trc_{uuid.uuid4().hex[:12]}"
    async with _client(server) as client:
        headers = {
            "x-trace-id": trace_id,
            "x-client": _client_header("hello"),
        }
        res = await client.post("/api/hello", headers=headers)
        res.raise_for_status()
        body = res.json()

    where = server or "in-process (ASGITransport)"
    console.print(f"[bold green]{body['message']}[/bold green]")
    console.print(f"[dim]transport:[/dim] {where}")
    console.print(f"[dim]trace:[/dim]     {body['traceId']}")
    console.print(f"[dim]event:[/dim]     {body['eventId']}")
