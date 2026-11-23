"""Model client for the 2.0 graph's LLM nodes: structured output, retry, availability fallback.

    res = structured("v2_classify", system, user, ClassifyOut, trace_id=tid)
    res.parsed        # validated Pydantic object, or None (res.error says why)
    res.model         # which model answered (base, or the fallback after an Anthropic API error)

Primary: the base model (config llm.base) via Anthropic structured outputs (messages.parse with a
Pydantic output_format). The SDK already retries 429 / 5xx / connection errors; if the call still fails
with an Anthropic API error, the same request goes to the fallback model (config llm.fallback, OpenAI
GPT-6 Luna) with JSON-schema structured output. A reply that does not validate is retried once on the
same model. Refusals are not retried and do not fall back. Every call is a generation in the trace and
a line in the spend ledger (with that model's price).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import anthropic
from pydantic import BaseModel, ValidationError

from ax15.config import fallback_llm, node_cfg
from ax15.trust import spend, tracing


@dataclass
class LLMResult:
    parsed: BaseModel | None
    model: str
    provider: str
    calls: int = 0
    fell_back: bool = False
    error: str | None = None
    usage: dict = field(default_factory=dict)
    latency_s: float = 0.0


_clients: dict[str, object] = {}


def _anthropic():
    if "anthropic" not in _clients:
        _clients["anthropic"] = anthropic.Anthropic(max_retries=2, timeout=60)
    return _clients["anthropic"]


def _openai():
    if "openai" not in _clients:
        from openai import OpenAI
        _clients["openai"] = OpenAI(max_retries=1, timeout=60)
    return _clients["openai"]


def _add(total: dict, u: dict) -> None:
    for k, v in u.items():
        total[k] = total.get(k, 0) + (v or 0)


def _call_anthropic(client, cfg: dict, system: str, user: str, schema: type[BaseModel], note: str):
    resp = client.messages.parse(
        model=cfg["model"], max_tokens=int(cfg.get("max_tokens", 3000)), system=system,
        messages=[{"role": "user", "content": user + note}], output_format=schema,
        output_config={"effort": cfg.get("effort", "low")},
    )
    u = resp.usage
    usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
             "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
             "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0}
    if resp.stop_reason == "refusal":
        return None, usage, "refused"
    if resp.stop_reason == "max_tokens":
        return None, usage, "max_tokens"
    return resp.parsed_output, usage, None


def _call_openai(client, cfg: dict, system: str, user: str, schema: type[BaseModel], note: str):
    resp = client.chat.completions.create(
        model=cfg["model"],
        messages=[{"role": "system", "content": system + "\n\nReply with one JSON object matching this schema:\n"
                   + json.dumps(schema.model_json_schema())},
                  {"role": "user", "content": user + note}],
        response_format={"type": "json_object"},
    )
    u = resp.usage
    usage = {"input_tokens": getattr(u, "prompt_tokens", 0) or 0, "output_tokens": getattr(u, "completion_tokens", 0) or 0}
    text = resp.choices[0].message.content or ""
    return schema.model_validate_json(text), usage, None


def structured(node: str, system: str, user: str, schema: type[BaseModel], *, trace_id: str | None = None,
               client=None, fallback_client=None) -> LLMResult:
    """One structured LLM call for a graph node (see module doc)."""
    cfg, fb = node_cfg(node), {**node_cfg(node), **fallback_llm()}
    res = LLMResult(None, cfg["model"], "anthropic")
    t0 = time.perf_counter()
    providers = [("anthropic", cfg, client or None), ("openai", fb, fallback_client or None)]
    for provider, pcfg, pclient in providers:
        res.model, res.provider = pcfg["model"], provider
        res.fell_back = provider != "anthropic"
        for attempt in (1, 2):
            spend.check()
            note = "" if attempt == 1 else "\n\n(Your previous reply did not match the schema. Reply again.)"
            c0 = time.perf_counter()
            res.calls += 1
            try:
                if provider == "anthropic":
                    parsed, usage, err = _call_anthropic(pclient or _anthropic(), pcfg, system, user, schema, note)
                else:
                    parsed, usage, err = _call_openai(pclient or _openai(), pcfg, system, user, schema, note)
            except (ValidationError, ValueError, json.JSONDecodeError) as e:
                parsed, usage, err = None, {}, f"invalid output: {str(e)[:200]}"
            except anthropic.APIError as e:                  # availability: go to the fallback model
                res.error = f"{type(e).__name__}: {str(e)[:200]}"
                break
            except Exception as e:  # noqa: BLE001 - fallback provider errors end the call
                res.error = f"{type(e).__name__}: {str(e)[:200]}"
                if provider == "anthropic":
                    break
                res.latency_s = round(time.perf_counter() - t0, 2)
                return res
            _add(res.usage, usage)
            if trace_id and usage:
                tracing.generation(trace_id, pcfg["model"], usage, time.perf_counter() - c0, name=node,
                                   input=user[:4000], output=parsed.model_dump(mode="json") if parsed else err,
                                   price=pcfg.get("price_per_mtok"))
            if parsed is not None:
                res.parsed, res.error = parsed, None
                res.latency_s = round(time.perf_counter() - t0, 2)
                return res
            res.error = err
            if err == "refused":
                res.latency_s = round(time.perf_counter() - t0, 2)
                return res
        else:
            # two invalid replies from a reachable model: don't switch provider for a content problem
            res.latency_s = round(time.perf_counter() - t0, 2)
            return res
    res.latency_s = round(time.perf_counter() - t0, 2)
    return res
