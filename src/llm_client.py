"""Swappable LLM client backed by either NVIDIA NIM or xAI Grok.

Both providers expose an OpenAI-compatible API, so a single ChatOpenAI
instance (from langchain-openai) works for both with only a different
base_url and api_key.  Select the provider via the LLM_PROVIDER env var.

Usage:
    from src.llm_client import get_llm
    llm = get_llm()                        # plain chat completions
    llm_with_tools = llm.bind_tools(tools) # enable function calling
    structured = llm.with_structured_output(MyPydanticModel)
"""

import os
import logging
from functools import lru_cache

from langchain_openai import ChatOpenAI

from config import NVIDIA_MODEL, GROK_MODEL

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_llm() -> ChatOpenAI:
    """Return a configured ChatOpenAI instance for the active provider.

    The instance is cached so repeated calls return the same object, which
    keeps connection-pool overhead low within a single pipeline run.
    """
    provider = os.getenv("LLM_PROVIDER", "nvidia").lower()

    if provider == "nvidia":
        api_key = os.getenv("NVIDIA_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "NVIDIA_API_KEY is not set. "
                "Copy .env.example to .env and fill in your key."
            )
        llm = ChatOpenAI(
            api_key=api_key,
            base_url="https://integrate.api.nvidia.com/v1",
            model=NVIDIA_MODEL,
            temperature=0,
            max_retries=3,
        )
        logger.info("LLM: NVIDIA NIM / %s", NVIDIA_MODEL)
        return llm

    elif provider == "grok":
        api_key = os.getenv("XAI_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "XAI_API_KEY is not set. "
                "Copy .env.example to .env and fill in your key."
            )
        llm = ChatOpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
            model=GROK_MODEL,
            temperature=0,
            max_retries=3,
        )
        logger.info("LLM: xAI Grok / %s", GROK_MODEL)
        return llm

    else:
        raise ValueError(
            f"Unsupported LLM_PROVIDER '{provider}'. "
            "Set LLM_PROVIDER=nvidia or LLM_PROVIDER=grok."
        )


def get_provider_name() -> str:
    """Return the active provider name."""
    return os.getenv("LLM_PROVIDER", "nvidia").lower()
