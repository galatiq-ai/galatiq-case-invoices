"""Observability helpers: structured logging and Prometheus metrics."""
from __future__ import annotations

import logging
from logging import Logger
from typing import Optional, Any

try:
    from pythonjsonlogger import jsonlogger
except Exception:  # pragma: no cover - optional dependency
    jsonlogger = None

# Prometheus
try:
    from prometheus_client import Counter, Histogram, start_http_server
except Exception:  # pragma: no cover - optional dependency
    Counter = None
    Histogram = None
    start_http_server = None


_metrics_started = False
_metrics_port = 8000

# Define metrics (will be None if prometheus_client not installed)
# Use Any for runtime-optional types to avoid static import-time typing issues.
invoices_processed_total: Any = None
invoices_failed_total: Any = None
audit_events_total: Any = None
stage_duration_seconds: Any = None


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger for structured JSON output when available."""
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler()
    if jsonlogger:
        fmt = jsonlogger.JsonFormatter('%(asctime)s %(levelname)s %(name)s %(message)s')
        handler.setFormatter(fmt)
    else:
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> Logger:
    setup_logging()
    return logging.getLogger(name)


def init_metrics(port: int = 8000) -> None:
    """Initialize Prometheus metrics and optional HTTP server on `port`.

    This is safe to call multiple times.
    """
    global _metrics_started, _metrics_port
    if Counter is None or Histogram is None:
        get_logger(__name__).warning("prometheus_client not available; metrics disabled")
        return

    if _metrics_started:
        return

    global invoices_processed_total, invoices_failed_total, audit_events_total, stage_duration_seconds
    invoices_processed_total = Counter(
        "invoices_processed_total", "Total invoices processed"
    )
    invoices_failed_total = Counter(
        "invoices_failed_total", "Total invoices that failed processing"
    )
    audit_events_total = Counter(
        "audit_events_total", "Total audit events written"
    )
    stage_duration_seconds = Histogram(
        "stage_duration_seconds", "Time spent in pipeline stages (seconds)"
    )

    try:
        if start_http_server:
            start_http_server(port)
            _metrics_started = True
            _metrics_port = port
            get_logger(__name__).info(f"Prometheus metrics server started on :{port}")
    except Exception:
        get_logger(__name__).exception("failed to start prometheus metrics server")


def metrics_server_info() -> dict:
    return {"enabled": _metrics_started, "port": _metrics_port}
