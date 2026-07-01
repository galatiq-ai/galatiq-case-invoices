"""Self-learning precedent store for the invoice approval pipeline.

Persists flag-pattern -> decision mappings across runs.  After
PRECEDENT_CONFIDENCE_THRESHOLD consistent decisions for the same flag
combination, future invoices with the identical pattern are auto-decided
without any LLM call — eliminating redundant reasoning on known scenarios.

Pattern key: sorted issue_type values joined with "|" (e.g. "out_of_stock"
or "insufficient_stock|price_mismatch").  Clean invoices (no flags) produce
an empty key and are excluded — they always go through the normal LLM path.

If a human overrides a decision (source="override"), the count resets to 1
so the system treats the pattern as contested until the threshold is reached
again with the new decision.
"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from config import (
    DB_PATH,
    MIN_VENDOR_SUBMISSIONS,
    PRECEDENT_CONFIDENCE_THRESHOLD,
    PRECEDENT_MAX_AMOUNT,
    TRUSTED_VENDOR_APPROVAL_RATE,
    VENDOR_REJECTION_RATE_THRESHOLD,
)

logger = logging.getLogger(__name__)

_initialized = False


# ── DB bootstrap ──────────────────────────────────────────────────────────────


@contextmanager
def _conn():
    """Context manager: yields a WAL-mode connection and always closes it."""
    c = sqlite3.connect(DB_PATH)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=5000")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init_precedent_db() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS precedents (
                flag_pattern  TEXT PRIMARY KEY,
                decision      TEXT NOT NULL,
                count         INTEGER NOT NULL DEFAULT 0,
                source        TEXT NOT NULL DEFAULT 'learned',
                updated_at    TEXT NOT NULL
            )
        """)
    logger.debug("Precedent DB initialised at %s", DB_PATH)


def _ensure_init() -> None:
    global _initialized
    if not _initialized:
        init_precedent_db()
        _initialized = True


# ── Public API ────────────────────────────────────────────────────────────────


def _vendor_tier(vendor_name: str) -> str:
    """Classify vendor trust level for use in precedent pattern keys.

    Tier is derived from the normalized DB so new/trusted/flagged vendors
    don't share auto-decisions just because they share a flag pattern.
    Falls back to 'unknown' if the DB is unavailable.
    """
    try:
        from src.db.queries import get_vendor_risk_profile

        p = get_vendor_risk_profile(vendor_name)
        if not p["known_vendor"]:
            return "new"
        if (
            p["total_submissions"] >= MIN_VENDOR_SUBMISSIONS
            and p["approval_rate"] >= TRUSTED_VENDOR_APPROVAL_RATE
        ):
            return "trusted"
        if (
            p["rejection_rate"] >= VENDOR_REJECTION_RATE_THRESHOLD
            and p["total_submissions"] >= MIN_VENDOR_SUBMISSIONS
        ):
            return "flagged"
        return "known"
    except Exception as exc:
        logger.warning("vendor tier lookup failed, defaulting to 'unknown': %s", exc)
        return "unknown"


def _pattern_key(flags: list[dict], vendor_name: str = "") -> str:
    """Stable, order-independent key from flag types and vendor trust tier.

    Including the vendor tier means a trusted vendor with 'insufficient_stock'
    gets a different auto-decision bucket than a new vendor with the same flag.
    """
    categories = sorted({f["issue_type"] for f in flags if f.get("issue_type")})
    tier = _vendor_tier(vendor_name) if vendor_name else "unknown"
    return f"{tier}|{'|'.join(categories)}" if categories else ""


def lookup_precedent(
    flags: list[dict],
    vendor_name: str = "",
    amount: float = 0.0,
) -> dict | None:
    """Return a stored auto-decision if the flag pattern is well-established.

    Returns None when:
    - flags is empty (clean invoice — let the LLM decide normally)
    - amount exceeds PRECEDENT_MAX_AMOUNT (high-value always goes to LLM)
    - pattern has fewer than PRECEDENT_CONFIDENCE_THRESHOLD consistent decisions
    - DB is unavailable (fail open, not closed)
    """
    _ensure_init()
    key = _pattern_key(flags, vendor_name)
    if not key:
        return None
    if amount > PRECEDENT_MAX_AMOUNT:
        logger.info(
            "Precedent skipped: amount %.2f exceeds ceiling %.2f",
            amount,
            PRECEDENT_MAX_AMOUNT,
        )
        return None
    try:
        with _conn() as c:
            row = c.execute(
                "SELECT decision, count, source FROM precedents WHERE flag_pattern=?",
                (key,),
            ).fetchone()
        if row and row[1] >= PRECEDENT_CONFIDENCE_THRESHOLD:
            logger.info(
                "Precedent hit: pattern=%r -> %s (count=%d, source=%s)",
                key,
                row[0],
                row[1],
                row[2],
            )
            return {
                "decision": row[0],
                "count": row[1],
                "source": row[2],
                "pattern": key,
            }
    except Exception as exc:
        logger.warning("Precedent lookup failed (proceeding normally): %s", exc)
    return None


def learn_from_decision(
    flags: list[dict],
    decision: str,
    source: str = "learned",
    vendor_name: str = "",
) -> None:
    """Record a decision for the given flag pattern.

    If the same decision repeats, count increments toward the threshold.
    If the decision changes (human override or model reversal), count
    resets to 1 so the system re-learns the pattern from scratch.
    """
    _ensure_init()
    key = _pattern_key(flags, vendor_name)
    if not key or decision not in ("approved", "rejected"):
        return
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _conn() as c:
            c.execute(
                """
                INSERT INTO precedents (flag_pattern, decision, count, source, updated_at)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(flag_pattern) DO UPDATE SET
                    count      = CASE WHEN decision = excluded.decision
                                      THEN count + 1
                                      ELSE 1 END,
                    decision   = excluded.decision,
                    source     = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (key, decision, source, now),
            )
        logger.debug(
            "Precedent updated: pattern=%r decision=%s source=%s", key, decision, source
        )
    except Exception as exc:
        logger.warning("Precedent write failed (non-fatal): %s", exc)
