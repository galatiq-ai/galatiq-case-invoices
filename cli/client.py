"""CLI as an API client: in-process via ASGITransport by default, a base URL for a live backend."""

import asyncio
import json
import platform
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx
from rich.console import Console
from rich.table import Table

from backend.statuses import TERMINAL, Status

console = Console()

# Statuses at which the pipeline has stopped working the invoice: terminal, or
# parked awaiting a human. Anything else means a background job is still running.
_SETTLED = {s.value for s in TERMINAL} | {Status.NEEDS_REVIEW.value}

_STATUS_COLOR = {
    "paid": "green", "approved": "green", "needs_review": "yellow",
    "rejected": "red", "failed": "red", "superseded": "dim", "processing": "cyan",
}


def _client(server: str | None) -> httpx.AsyncClient:
    if server:
        return httpx.AsyncClient(base_url=server, timeout=180.0)
    from backend.app import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cli", timeout=180.0)


def _client_header(action: str) -> str:
    ctx = {
        "kind": "cli",
        "page": "cli",
        "user_agent": f"cli/python-{platform.python_version()}",
        "action": action,
    }
    return quote(json.dumps(ctx))


async def process(invoice_path: str, server: str | None = None) -> None:
    path = Path(invoice_path)
    if not path.exists():
        console.print(f"[red]file not found:[/red] {invoice_path}")
        raise SystemExit(1)
    headers = {"x-trace-id": f"trc_{uuid.uuid4().hex[:12]}", "x-client": _client_header("process")}
    async with _client(server) as client:
        with path.open("rb") as fh:
            files = {"file": (path.name, fh, "application/octet-stream")}
            res = await client.post("/api/invoices", headers=headers, files=files)
        res.raise_for_status()
        invoice_id = res.json()["invoice"]["id"]
        result = await _await_settled(client, invoice_id, headers)
    _render(result, server or "in-process (ASGITransport)")


async def _await_settled(
    client: httpx.AsyncClient, invoice_id: int, headers: dict,
    *, timeout: float = 180.0, interval: float = 0.5,
) -> dict:
    """Poll the invoice until it reaches a resting status, or the timeout lapses
    (in which case the still-in-flight invoice is returned as-is to render)."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    with console.status("[cyan]processing…", spinner="dots"):
        while True:
            res = await client.get(f"/api/invoices/{invoice_id}", headers=headers)
            res.raise_for_status()
            result = res.json()
            if result["invoice"]["status"] in _SETTLED or loop.time() >= deadline:
                return result
            await asyncio.sleep(interval)


async def approve(invoice_id: int, server: str | None = None) -> None:
    """Human review resolution from the CLI: clear a held invoice, which pays it."""
    headers = {"x-trace-id": f"trc_{uuid.uuid4().hex[:12]}", "x-client": _client_header("approve")}
    async with _client(server) as client:
        res = await client.post(f"/api/invoices/{invoice_id}/approve", headers=headers)
    if res.status_code == 409:
        console.print(f"[yellow]{res.json()['detail']}[/yellow]")
        raise SystemExit(1)
    res.raise_for_status()
    _render(res.json(), server or "in-process (ASGITransport)")


def _render(result: dict, where: str) -> None:
    inv = result["invoice"]
    status = inv["status"]
    color = _STATUS_COLOR.get(status, "white")
    console.print()
    console.print(f"[bold]{inv.get('invoice_number') or '(no number)'}[/bold]   [{color}]{status.upper()}[/{color}]")
    console.print(f"[dim]vendor:[/dim] {inv.get('vendor_raw') or '—'}    "
                  f"[dim]total:[/dim] {inv.get('stated_total')} {inv.get('currency') or ''}")

    if inv.get("review_category"):
        level = inv.get("review_level") or ""
        console.print(f"[dim]review:[/dim] [yellow]{inv['review_category']}[/yellow]"
                      f"{f'  ({level})' if level else ''}")
    if inv.get("review_summary"):
        console.print(f"[dim]{inv['review_summary']}[/dim]")

    items = result.get("line_items", [])
    if items:
        table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
        table.add_column("item")
        table.add_column("matched", style="dim")
        table.add_column("qty", justify="right")
        table.add_column("unit", justify="right")
        table.add_column("note", style="dim")
        for li in items:
            table.add_row(li["item_raw"], li.get("matched_item") or "—", str(li["quantity"]),
                          "" if li.get("unit_price") is None else str(li["unit_price"]), li.get("note") or "")
        console.print(table)

    findings = result.get("findings", [])
    if findings:
        console.print("[dim]findings:[/dim]")
        for f in findings:
            mark = {"error": "[red]✗[/red]", "warning": "[yellow]•[/yellow]"}.get(f["severity"], "[dim]·[/dim]")
            console.print(f"  {mark} [dim]({f['source']})[/dim] {f['message']}")

    trace = " → ".join(f"{t['stage']}/{t['kind']}" for t in result.get("trace", []))
    console.print(f"[dim]trace:[/dim] {trace}")
    console.print(f"[dim]via {where} · invoice id {inv['id']}[/dim]")
