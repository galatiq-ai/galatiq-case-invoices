"""Central config: loads environment variables and provides the LLM client."""

from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv()  # reads .env file automatically

XAI_API_KEY = os.getenv("XAI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
XAI_MODEL = os.getenv("XAI_MODEL", "grok-3")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
VERBOSE = False  # Set to True by main.py when --verbose is passed


def get_llm():
    """Return a configured LangChain chat model, or None if no key is available."""
    from langchain_openai import ChatOpenAI

    if XAI_API_KEY:
        # xAI Grok is OpenAI-compatible; point ChatOpenAI at the xAI endpoint.
        return ChatOpenAI(
            model=XAI_MODEL,
            api_key=XAI_API_KEY,
            base_url="https://api.x.ai/v1",
            temperature=0,
        )
    elif OPENAI_API_KEY:
        return ChatOpenAI(
            model=OPENAI_MODEL,
            api_key=OPENAI_API_KEY,
            temperature=0,
        )
    else:
        return None  # No API key — fall back to deterministic extraction/critique
