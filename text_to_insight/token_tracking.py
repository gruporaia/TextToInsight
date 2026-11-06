"""Node-level token usage extraction, state delta builders, and pure LangGraph reducer."""

from collections.abc import Iterable, Mapping
from typing import Any, TypedDict


class NodeTokenUsage(TypedDict):
    """Accumulated token metrics and LLM invocation count for a single graph node."""

    input: int
    output: int
    total: int
    calls: int


def extract_tokens(response: Any) -> tuple[int, int, int]:
    """Extract (input_tokens, output_tokens, total_tokens) across LangChain providers."""
    if response is None:
        return 0, 0, 0

    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, Mapping) and usage:
        in_tokens = int(usage.get("input_tokens", 0) or 0)
        out_tokens = int(usage.get("output_tokens", 0) or 0)
        raw_total = int(usage.get("total_tokens", 0) or 0)
        total_tokens = raw_total if raw_total else (in_tokens + out_tokens)
        return in_tokens, out_tokens, total_tokens

    resp_meta = getattr(response, "response_metadata", None)
    if isinstance(resp_meta, Mapping) and resp_meta:
        token_usage = resp_meta.get("token_usage") or resp_meta.get("usage")
        if isinstance(token_usage, Mapping) and token_usage:
            in_tokens = int(
                token_usage.get("input_tokens", token_usage.get("prompt_tokens", 0)) or 0
            )
            out_tokens = int(
                token_usage.get("output_tokens", token_usage.get("completion_tokens", 0)) or 0
            )
            raw_total = int(token_usage.get("total_tokens", 0) or 0)
            total_tokens = raw_total if raw_total else (in_tokens + out_tokens)
            return in_tokens, out_tokens, total_tokens

    return 0, 0, 0


def merge_tokens_by_node(
    current: dict[str, NodeTokenUsage] | None,
    update: dict[str, NodeTokenUsage] | None,
) -> dict[str, NodeTokenUsage]:
    """Pure LangGraph channel reducer that merges per-node token usage without in-place mutation."""
    merged: dict[str, NodeTokenUsage] = {}
    if current:
        for node_name, metrics in current.items():
            merged[node_name] = {
                "input": int(metrics.get("input", 0)),
                "output": int(metrics.get("output", 0)),
                "total": int(metrics.get("total", 0)),
                "calls": int(metrics.get("calls", 0)),
            }
    if update:
        for node_name, delta in update.items():
            if node_name in merged:
                prev = merged[node_name]
                merged[node_name] = {
                    "input": prev["input"] + int(delta.get("input", 0)),
                    "output": prev["output"] + int(delta.get("output", 0)),
                    "total": prev["total"] + int(delta.get("total", 0)),
                    "calls": prev["calls"] + int(delta.get("calls", 0)),
                }
            else:
                merged[node_name] = {
                    "input": int(delta.get("input", 0)),
                    "output": int(delta.get("output", 0)),
                    "total": int(delta.get("total", 0)),
                    "calls": int(delta.get("calls", 0)),
                }
    return merged


def build_node_tokens_delta(
    node_name: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    calls: int = 1,
) -> dict[str, NodeTokenUsage]:
    """Build a single-node token delta dictionary from raw integer primitives."""
    return {
        node_name: {
            "input": int(input_tokens),
            "output": int(output_tokens),
            "total": int(total_tokens),
            "calls": int(calls),
        }
    }


def build_token_usage_update(node_name: str, response: Any) -> dict[str, Any]:
    """Build the composite state update dict for a single llm.invoke() call (calls=1)."""
    in_tokens, out_tokens, total_tokens = extract_tokens(response)
    return {
        "tokens_input": in_tokens,
        "tokens_output": out_tokens,
        "tokens_total": total_tokens,
        "tokens_by_node": build_node_tokens_delta(
            node_name, in_tokens, out_tokens, total_tokens, calls=1
        ),
    }


def build_batch_token_usage_update(
    node_name: str, responses: Iterable[Any]
) -> dict[str, Any]:
    """Build the composite state update dict for a batch llm.batch() call (calls=len(responses))."""
    resp_list = list(responses)
    if not resp_list:
        return {
            "tokens_input": 0,
            "tokens_output": 0,
            "tokens_total": 0,
            "tokens_by_node": {},
        }

    total_in = 0
    total_out = 0
    total_all = 0
    for resp in resp_list:
        in_tok, out_tok, tot_tok = extract_tokens(resp)
        total_in += in_tok
        total_out += out_tok
        total_all += tot_tok

    return {
        "tokens_input": total_in,
        "tokens_output": total_out,
        "tokens_total": total_all,
        "tokens_by_node": build_node_tokens_delta(
            node_name, total_in, total_out, total_all, calls=len(resp_list)
        ),
    }
