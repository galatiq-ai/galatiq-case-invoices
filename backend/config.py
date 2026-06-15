"""Configuration: xAI credentials + extraction/ingestion policy, loaded from .env."""

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
XAI_BASE_URL = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")
XAI_MODEL = os.environ.get("XAI_MODEL", "grok-4.3")

# Extraction / ingestion policy
MAX_SCHEMA_RETRIES = 2            # schema self-correction rounds per call
RENDER_DPI = 150
MIN_TEXT_CHARS_PER_PAGE = 80      # below this a PDF text layer is considered absent
MIN_PRINTABLE_RATIO = 0.85        # below this a text layer is considered garbage

# Validation / approval policy
APPROVAL_THRESHOLD = 10_000.0    # invoices over this can't pay touchless — a human signs off
MONEY_TOLERANCE = 0.01           # absolute $ slack when reconciling stated arithmetic
PRICE_TOLERANCE = 0.01           # absolute $ slack when matching a line price to the PO
