"""Compatibility wrapper so `streamlit run app.py` works.

This simply forwards to `src.streamlit_app.main()` which contains the actual UI.
"""
from .streamlit_app import main
from .observability import setup_logging, init_metrics, get_logger
import os


if __name__ == "__main__":
    # Configure logging and metrics for local runs
    setup_logging()
    logger = get_logger(__name__)
    try:
        port = int(os.environ.get("METRICS_PORT", "8000"))
    except Exception:
        port = 8000
    init_metrics(port=port)
    logger.info("starting app", extra={"metrics_port": port})
    main()
