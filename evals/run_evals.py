"""Run the invoice evaluation suite.

Usage:
    python -m evals.run_evals
    python -m evals.run_evals --only inv_2005

The runner uses a fresh SQLite database at evals/eval.db for full runs and
writes evals/results.json with the observed outcomes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent
EVAL_DB = EVAL_DIR / "eval.db"
RESULTS = EVAL_DIR / "results.json"

os.environ.setdefault("GALATIQ_DB_PATH", str(EVAL_DB))

from backend import config, db  # noqa: E402
from backend.app import app  # noqa: E402
from backend.statuses import TERMINAL, Status  # noqa: E402

from .expectations import EXPECTATIONS  # noqa: E402


def _remove_eval_db() -> None:
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(EVAL_DB) + suffix)
        if path.exists():
            path.unlink()
    db._initialized.discard(EVAL_DB)


def _codes(result: dict) -> set[str]:
    return {finding["code"] for finding in result.get("findings", [])}


def _payment(result: dict) -> bool:
    return any(t["kind"] == "payment" for t in result.get("trace", []))


SETTLED = {s.value for s in TERMINAL} | {Status.NEEDS_REVIEW.value}
SETUP_CASES = {
    "data/invoices/invoice_1001.txt",
    "data/invoices/invoice_1004.json",
    "data/invoices/invoice_1011.pdf",
}
DEPENDENCIES = {
    "data/invoices/invoice_1004_revised.json",
    "data/invoices/invoice_1011.txt",
    "data/invoices/invoice_1016.json",
    "data/test_invoices/inv_2005_duplicate.txt",
}


def _headers(action: str) -> dict:
    client = quote(json.dumps({"kind": "cli", "page": "evals", "action": action}))
    return {"x-trace-id": f"trc_{uuid.uuid4().hex[:12]}", "x-client": client}


async def _process(path: Path) -> dict:
    headers = _headers("process")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://eval", timeout=240.0) as client:
        with path.open("rb") as fh:
            files = {"file": (path.name, fh, "application/octet-stream")}
            res = await client.post("/api/invoices", headers=headers, files=files)
        res.raise_for_status()
        invoice_id = res.json()["invoice"]["id"]
        deadline = asyncio.get_event_loop().time() + 240.0
        while True:
            res = await client.get(f"/api/invoices/{invoice_id}", headers=headers)
            res.raise_for_status()
            result = res.json()
            if result["invoice"]["status"] in SETTLED:
                return result
            if asyncio.get_event_loop().time() >= deadline:
                return result
            await asyncio.sleep(0.5)


async def _run_case(case: dict) -> dict:
    path = REPO_ROOT / case["file"]
    started = time.monotonic()
    for attempt in range(4):
        try:
            result = await _process(path)
            break
        except Exception as exc:
            if "database is locked" not in str(exc).lower() or attempt == 3:
                raise
            await asyncio.sleep(0.75 * (attempt + 1))
    status = result["invoice"]["status"]
    actual_codes = _codes(result)
    paid = _payment(result)
    watched = set(case.get("watch_findings", set()))
    watched_present = watched & actual_codes
    observations = {
        "watched_findings_present": sorted(watched_present),
        "watched_findings_missing": [] if watched_present or not watched else sorted(watched),
    }
    safety_checks = {
        "not_paid_when_unsafe": not case.get("must_not_pay", False) or not paid,
        "settled": status in SETTLED,
    }
    inv = result["invoice"]
    return {
        "file": case["file"],
        "scenario": case["scenario"],
        "risk": case.get("risk", "unspecified"),
        "actual_status": status,
        "review_category": inv.get("review_category"),
        "review_level": inv.get("review_level"),
        "model_summary": inv.get("review_summary"),
        "watch_findings": sorted(watched),
        "actual_findings": sorted(actual_codes),
        "finding_details": result.get("findings", []),
        "paid": paid,
        "safety_checks": safety_checks,
        "observations": observations,
        "passed": all(safety_checks.values()),
        "invoice_id": inv["id"],
        "duration_s": round(time.monotonic() - started, 1),
    }


def _runner_error(case: dict, exc: Exception) -> dict:
    return {
        "file": case["file"],
        "scenario": case["scenario"],
        "risk": case.get("risk", "unspecified"),
        "actual_status": "runner_error",
        "watch_findings": sorted(case.get("watch_findings", set())),
        "actual_findings": [],
        "paid": False,
        "safety_checks": {"runner": False},
        "observations": {},
        "passed": False,
        "invoice_id": None,
        "duration_s": 0,
        "error": f"{type(exc).__name__}: {exc}",
    }


async def _run_one(index: int, total: int, case: dict, sem: asyncio.Semaphore) -> tuple[int, dict]:
    async with sem:
        try:
            outcome = await _run_case(case)
            print(
                f"[{index + 1:>2}/{total}] {case['file']} ... "
                f"{'PASS' if outcome['passed'] else 'FAIL'} "
                f"({outcome['actual_status']}, {outcome['duration_s']}s)",
                flush=True,
            )
        except Exception as exc:
            outcome = _runner_error(case, exc)
            print(f"[{index + 1:>2}/{total}] {case['file']} ... ERROR ({outcome['error']})", flush=True)
        return index, outcome


async def _run_group(items: list[tuple[int, dict]], total: int, jobs: int) -> list[tuple[int, dict]]:
    sem = asyncio.Semaphore(jobs)
    tasks = [_run_one(index, total, case, sem) for index, case in items]
    return await asyncio.gather(*tasks)


async def _run(cases: list[dict], jobs: int) -> list[dict]:
    total = len(cases)
    indexed = list(enumerate(cases))
    setup = [(i, c) for i, c in indexed if c["file"] in SETUP_CASES]
    main = [(i, c) for i, c in indexed if c["file"] not in SETUP_CASES and c["file"] not in DEPENDENCIES]
    dependent = [(i, c) for i, c in indexed if c["file"] in DEPENDENCIES]

    results: list[tuple[int, dict]] = []
    if setup:
        print(f"running {len(setup)} setup case(s) first...")
        results.extend(await _run_group(setup, total, min(jobs, len(setup))))
    if main:
        print(f"running {len(main)} independent case(s) with concurrency {jobs}...")
        results.extend(await _run_group(main, total, jobs))
    if dependent:
        print(f"running {len(dependent)} dependent case(s) after setup...")
        results.extend(await _run_group(dependent, total, jobs))
    return [r for _, r in sorted(results, key=lambda item: item[0])]


def _print_summary(results: list[dict]) -> None:
    passed = sum(1 for r in results if r["passed"])
    print("\n" + "=" * 112)
    print(f"{'FILE':<38} {'RISK':<14} {'ACTUAL':<16} {'WATCHED FINDINGS':<24} SAFETY")
    print("-" * 112)
    for r in results:
        missing = r.get("observations", {}).get("watched_findings_missing", [])
        watched = "ok" if not missing else "missing " + ",".join(missing)
        print(
            f"{Path(r['file']).name:<38} "
            f"{r.get('risk', '—'):<14} "
            f"{r['actual_status']:<16} "
            f"{watched:<24} "
            f"{'PASS' if r['passed'] else 'FAIL'}"
        )
    print("-" * 112)
    print(f"{passed}/{len(results)} safety invariants passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="run cases whose path contains this text")
    parser.add_argument("--jobs", type=int, default=4, help="number of invoices to process at once")
    parser.add_argument(
        "--keep-db",
        action="store_true",
        help="do not reset evals/eval.db before running; useful with --only",
    )
    args = parser.parse_args()

    if not config.XAI_API_KEY:
        print("error: XAI_API_KEY is not set")
        return 2

    cases = [c for c in EXPECTATIONS if not args.only or args.only in c["file"]]
    if not cases:
        print(f"no cases match --only={args.only}")
        return 2

    if not args.keep_db:
        _remove_eval_db()
    db.connect().close()

    jobs = max(1, args.jobs)
    results = asyncio.run(_run(cases, jobs))
    _print_summary(results)

    if not args.only:
        passed = sum(1 for r in results if r["passed"])
        RESULTS.write_text(json.dumps({
            "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": config.XAI_MODEL,
            "db": str(EVAL_DB),
            "passed": passed,
            "total": len(results),
            "mode": "safety_report",
            "cases": results,
        }, indent=2))
        print(f"results written to {RESULTS}")

    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
