"""Compatibility layer for LLM structured output / tool binding.

Provides a single `invoke_model_with_schema` helper that attempts, in order:
  1. Call the LLM with a structured/schema binding argument (best-effort).
  2. Fall back to calling the LLM normally and parsing JSON from the text.

The implementation is defensive and accepts a variety of LLM client shapes used
by different LangChain/OpenAI wrappers.
"""
from __future__ import annotations

import json
from typing import Any, Optional


def _extract_response(response: Any) -> Any:
    """Normalize various response shapes into a Python object or JSON text.

Looks for common attributes (`parsed`, `content`, `text`) then falls back
to trying to JSON-decode a string representation.
"""
    # If the model returned a ready Python structure
    if isinstance(response, dict):
        return response
    if hasattr(response, "parsed"):
        return getattr(response, "parsed")
    if hasattr(response, "structured"):
        return getattr(response, "structured")

    # Extract text-like fields
    text = None
    if hasattr(response, "content"):
        text = getattr(response, "content")
    elif hasattr(response, "text"):
        text = getattr(response, "text")
    elif isinstance(response, str):
        text = response

    if text is None:
        # Last resort: str() and attempt JSON
        text = str(response)

    if not isinstance(text, str):
        try:
            return json.loads(text)  # type: ignore[arg-type]
        except Exception:
            return text

    text = text.strip()
    # Strip markdown fences often emitted by models
    if text.startswith("```"):
        # remove leading ```json or ```
        import re

        text = re.sub(r"^```[a-zA-Z0-9]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

    try:
        return json.loads(text)
    except Exception:
        # Not JSON — return raw text for caller to handle
        return text


def invoke_model_with_schema(
    llm: Any,
    messages: list[Any],
    schema: Optional[Any] = None,
    timeout: Optional[float] = None,
) -> Any:
    """Invoke the LLM and attempt structured binding, falling back to JSON parse.

    Args:
        llm: The LLM client object (may be None).
        messages: A list of `HumanMessage`/`SystemMessage` objects or plain strings.
        schema: Optional schema or descriptor object to request structured output.
        timeout: Optional timeout value (best-effort, depends on the client).

    Returns:
        Parsed Python object (dict/list) when possible, otherwise the raw text.
    """
    if llm is None:
        raise RuntimeError("No LLM client provided")

    # Try calling with a schema-binding kwarg (best-effort)
    try:
        call_kwargs = {}
        if timeout is not None:
            call_kwargs["timeout"] = timeout
        if schema is not None:
            # Try common kwarg names used by various wrappers
            for name in ("response_schema", "schema", "response_format", "functions"):
                try:
                    kw = {name: schema}
                    res = llm.invoke(messages, **{**call_kwargs, **kw})
                    return _extract_response(res)
                except TypeError:
                    # This client doesn't accept that kwarg — try the next
                    continue
                except Exception:
                    # If invocation itself failed, let fallback try a plain call
                    break

        # If no schema binding succeeded, call normally
        res = llm.invoke(messages, **call_kwargs)
        parsed = _extract_response(res)
        if parsed is None:
            # defensive: try to parse content/text explicitly
            content = getattr(res, "content", None) or getattr(res, "text", None) or str(res)
            try:
                return json.loads(content)
            except Exception:
                return content
        return parsed
    except TypeError:
        # Some lightweight clients may use a different method name
        try:
            res = llm.generate(messages)
            return _extract_response(res)
        except Exception as e:
            raise
    except Exception:
        # Propagate the exception to the caller to allow safe fallbacks
        raise
