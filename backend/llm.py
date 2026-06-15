"""xAI client wrapper.

Two invariants live here:
- Every LLM output that enters the pipeline passes through a strict JSON schema
  (Pydantic). On validation failure the model is re-prompted with the exact
  error (self-correction), bounded by MAX_SCHEMA_RETRIES.
- Every request/response pair is persisted to the per-invoice trace.
"""

import json
import time
from dataclasses import dataclass

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from . import config, tracing
from .unit_of_work import unit_of_work


class SchemaRetryExceeded(Exception):
    def __init__(self, node: str, errors: list[str]):
        super().__init__(f"{node}: LLM output failed schema validation {len(errors)} times: {errors[-1]}")
        self.errors = errors


@dataclass
class RunContext:
    """Carries the invoice id and the bound wide event through the pipeline. It
    holds no unit of work on purpose: each LLM exchange is traced in its own short
    transaction (see `_emit_llm_event`), and each pipeline node is handed its own
    uow. So no write transaction is ever held across an LLM call, by construction."""
    invoice_id: int
    event: object = None              # WideEvent, for binding each trace write's unit of work


_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if _client is None:
        if not config.XAI_API_KEY:
            raise RuntimeError("XAI_API_KEY is not set — add it to .env (see README)")
        _client = OpenAI(api_key=config.XAI_API_KEY, base_url=config.XAI_BASE_URL)
    return _client


def strict_schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()

    def forbid_extras(node):
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
            for v in node.values():
                forbid_extras(v)
        elif isinstance(node, list):
            for v in node:
                forbid_extras(v)

    forbid_extras(schema)
    return schema


def _call(messages: list[dict], *, response_format: dict | None = None, tools: list[dict] | None = None):
    kwargs: dict = {"model": config.XAI_MODEL, "messages": messages}
    if response_format:
        kwargs["response_format"] = response_format
    if tools:
        kwargs["tools"] = tools
    return client().chat.completions.create(**kwargs)


def _persistable(messages: list[dict]) -> list[dict]:
    """Copy of messages safe for the trace: base64 page images are elided
    (hundreds of KB each; they bloat the DB and the trace view)."""
    out = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            content = [
                {"type": "image_url", "image_url": {"url": "<page image elided>"}}
                if isinstance(part, dict) and part.get("type") == "image_url" else part
                for part in content
            ]
            m = {**m, "content": content}
        out.append(m)
    return out


def _emit_llm_event(ctx: RunContext, node: str, kind: str, payload: dict, response, started: float) -> None:
    """Record one LLM exchange in its own short transaction. Self-contained on
    purpose: the LLM layer owns durably tracing each call, and committing here
    means no write lock is held when the caller makes its next call."""
    if "messages" in payload:
        payload = {**payload, "messages": _persistable(payload["messages"])}
    usage = response.usage
    with unit_of_work(ctx.event) as uow:
        tracing.emit(
            uow,
            ctx.invoice_id,
            node,
            kind,
            payload,
            tokens_in=usage.prompt_tokens if usage else None,
            tokens_out=usage.completion_tokens if usage else None,
            duration_ms=int((time.monotonic() - started) * 1000),
        )


def structured(
    ctx: RunContext,
    node: str,
    messages: list[dict],
    output_model: type[BaseModel],
) -> BaseModel:
    """Call the model, requiring output that validates against output_model.

    On invalid output the validation error is fed back verbatim and the model
    retries — this is the schema self-correction loop.
    """
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": output_model.__name__, "strict": True, "schema": strict_schema(output_model)},
    }
    convo = list(messages)
    errors: list[str] = []
    for attempt in range(1 + config.MAX_SCHEMA_RETRIES):
        started = time.monotonic()
        response = _call(convo, response_format=response_format)
        message = response.choices[0].message
        reasoning = getattr(message, "reasoning_content", None)
        try:
            result = output_model.model_validate_json(message.content)
            _emit_llm_event(
                ctx, node, "llm_call",
                {
                    "attempt": attempt + 1,
                    "messages": convo,
                    "output": json.loads(message.content),
                    "reasoning": reasoning,
                    "schema": output_model.__name__,
                },
                response, started,
            )
            return result
        except (ValidationError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
            _emit_llm_event(
                ctx, node, "llm_schema_retry",
                {
                    "attempt": attempt + 1,
                    "messages": convo,
                    "invalid_output": message.content,
                    "validation_error": str(exc),
                    "schema": output_model.__name__,
                },
                response, started,
            )
            convo = convo + [
                {"role": "assistant", "content": message.content},
                {
                    "role": "user",
                    "content": "Your previous output failed schema validation:\n"
                    f"{exc}\nReturn a corrected JSON object that satisfies the schema exactly.",
                },
            ]
    raise SchemaRetryExceeded(node, errors)
