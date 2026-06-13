"""Compatibility wrapper that delegates to src.app."""
from src.app import main as _main
from src.observability import setup_logging, init_metrics, get_logger
import os


if __name__ == "__main__":
    setup_logging()
    logger = get_logger(__name__)
    try:
        port = int(os.environ.get("METRICS_PORT", "8000"))
    except Exception:
        port = 8000
    init_metrics(port=port)
    logger.info("starting app", extra={"metrics_port": port})
    _main()
