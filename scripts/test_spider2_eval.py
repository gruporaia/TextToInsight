#!/usr/bin/env python3
"""
scripts/test_spider2_eval.py

CLI Orchestrator for Spider 2.0 Benchmark Evaluation.

Integrates:
- src.spider.query_executor: Multi-DBMS executor (SQLite, BigQuery, Snowflake, SQLAlchemy)
  with timeout enforcement, mutation blocking, and safe math functions.
- src.spider.metrics: Column-level execution match, F1/Precision/Recall, SQL similarity,
  and canonical evaluation row building.
- src.spider.csv_reporter: CSV persistence, timestamped directories, and node-level token aggregation.
- src.spider.logger: Real-time Tee stream trace capture, structured instance logging, and run summaries.
- text_to_insight.InsightEngine: The TextToInsight multi-agent architecture.
- default_registry: Dynamic LLM provider resolution with secret-leak prevention.

Usage:
    # Quick dry-run smoke test (2 samples, no API key required):
    python scripts/test_spider2_eval.py --dry-run --sample-size 2

    # Standard evaluation on SQLite with RAG and CoT:
    python scripts/test_spider2_eval.py --sample-size 10 --seed 42 --dialect sqlite

    # Specific database evaluation:
    python scripts/test_spider2_eval.py --db-filter E_commerce --sample-size 5
"""

from __future__ import annotations

import argparse
from datetime import datetime
import glob
import json
import os
from pathlib import Path
import random
import re
import sys
import time
from typing import Any

import pandas as pd
from sqlalchemy import create_engine

# Ensure project root is available in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from src.spider.analise_empirica import gerar_relatorio_empirico_completo
from src.spider.csv_reporter import CSVReporter
from src.spider.logger import SpiderExecutionLogger
from src.spider.metrics import (
    build_comparison_row,
    results_exact_match,
    results_f1_score,
    sql_similarity_score,
)
from src.spider.query_executor import (
    BaseSpiderQueryExecutor,
    Spider2QueryExecutor,
    get_spider_executor,
    mask_db_url,
)
from text_to_insight import InsightEngine
from text_to_insight.model_selection import default_registry

load_dotenv()

ENV_VAR_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
SENSITIVE_KEYWORDS = ("key", "token", "secret", "password", "auth", "credential")


def _validate_api_key_env(env_var_name: str | None) -> str | None:
    """
    Validates that --api-key-env receives strictly an environment variable name
    and prevents accidental leakage of actual secret keys on the CLI.
    """
    if not env_var_name:
        return None
    trimmed = env_var_name.strip()
    if not ENV_VAR_PATTERN.match(trimmed) or trimmed.startswith(("sk-", "AIza", "ghp_", "bearer")):
        raise argparse.ArgumentTypeError(
            "Invalid environment variable name for --api-key-env. "
            "Must be an uppercase identifier name (e.g., 'OPENAI_API_KEY', 'GOOGLE_API_KEY'). "
            "Actual secret key values must NEVER be passed as CLI arguments."
        )
    return trimmed


def _model_kwarg(item: str) -> tuple[str, Any]:
    """
    Parses KEY=VALUE pairs for extra LLM constructor arguments with security guardrails.
    Blocks sensitive configuration keys from being passed via CLI arguments.
    """
    key, sep, raw_value = item.partition("=")
    key = key.strip()
    if not sep or not key:
        raise argparse.ArgumentTypeError("Expected KEY=VALUE argument format.")

    key_lower = key.lower()
    if any(s in key_lower for s in SENSITIVE_KEYWORDS):
        raise argparse.ArgumentTypeError(
            "Sensitive configuration parameters (API keys, tokens, secrets, passwords) "
            "cannot be passed via --model-kwargs. Use --api-key-env or environment variables."
        )

    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        value = raw_value
    return key, value


def resolve_model_and_provider(model_identifier: str) -> tuple[str, str]:
    """Resolves provider and concrete model name using default_registry with fallback."""
    try:
        spec, concrete_model = default_registry.parse_model(model_identifier)
        return spec.name, concrete_model
    except Exception:
        if "/" in model_identifier:
            prov, mod = model_identifier.split("/", 1)
            return prov.strip().lower(), mod.strip()
        lowered = model_identifier.lower()
        if "gemini" in lowered or "google" in lowered:
            return "google", model_identifier
        return "openai", model_identifier


def parse_arguments() -> argparse.Namespace:
    """Parses and validates command-line arguments for Spider 2.0 evaluation."""
    parser = argparse.ArgumentParser(
        description="TextToInsight Spider 2.0 Benchmark Evaluation CLI Orchestrator."
    )

    # Model & Hyperparameters with Security Guardrails
    parser.add_argument(
        "--model",
        type=str,
        default=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        help="LLM model name or provider/model specifier (e.g. 'openai/gpt-4o-mini', 'google/gemini-2.5-flash')",
    )
    parser.add_argument(
        "--api-key-env",
        type=_validate_api_key_env,
        default=None,
        help="Environment variable NAME holding the API key (e.g. 'OPENAI_API_KEY'). Do NOT pass raw keys.",
    )
    parser.add_argument(
        "--model-kwargs",
        type=_model_kwarg,
        nargs="+",
        default=[],
        metavar="KEY=VALUE",
        help="Extra kwargs passed to LLM constructor (e.g. temperature=0.0 max_tokens=1024)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="LLM temperature shortcut (merged into model_kwargs)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="LLM max output tokens shortcut (merged into model_kwargs)",
    )

    # Engine Architecture Flags
    parser.add_argument(
        "--rag",
        choices=["on", "off"],
        default="on",
        help="Enable SchemaGraphRAG for schema retrieval (default: on)",
    )
    parser.add_argument(
        "--enrich-rag",
        choices=["on", "off"],
        default="off",
        help="Enable semantic column enrichment in RAG (default: off)",
    )
    parser.add_argument(
        "--cot",
        choices=["on", "off"],
        default="on",
        help="Enable Chain-of-Thought reasoning in Code Agent (default: on)",
    )
    parser.add_argument(
        "--data-exploration",
        choices=["on", "off"],
        default="on",
        help="Enable data exploration step (default: on)",
    )
    parser.add_argument(
        "--exploration-selector",
        choices=["off", "llm", "rag"],
        default="off",
        help="Column selection mode for exploration: off, llm, rag (default: off)",
    )
    parser.add_argument(
        "--infer-fks",
        choices=["on", "off"],
        nargs="?",
        const="on",
        default="off",
        help="Enable virtual foreign key inference (default: off)",
    )
    parser.add_argument(
        "--with-graphs",
        choices=["on", "off"],
        nargs="?",
        const="on",
        default="off",
        help="Enable chart generation and CSV export (default: off)",
    )
    parser.add_argument(
        "--use-evidence",
        choices=["on", "off"],
        default="on",
        help="Inject external knowledge and evidence into prompt (default: on)",
    )

    # Execution & Output Controls
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="SQL query execution timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate execution without live LLM calls (zero API cost, realistic token tracking)",
    )
    parser.add_argument(
        "--log",
        choices=["on", "off"],
        default="on",
        help="Enable detailed execution log file generation (default: on)",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Custom destination for log file (default: auto in run folder)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Custom destination CSV path or run folder",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=None,
        help="Number of instances to evaluate (default: all matching)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducible sampling (default: 42)",
    )
    parser.add_argument(
        "--db-filter",
        type=str,
        default=None,
        help="Filter evaluation to a specific database ID (e.g., E_commerce)",
    )
    parser.add_argument(
        "--question-filter",
        type=str,
        default=None,
        help="Filter evaluation by substring in question",
    )

    # Spider 2 Specific Flags
    parser.add_argument(
        "--dialect",
        choices=["sqlite", "bigquery", "snowflake", "all"],
        default="sqlite",
        help="Filter instances by database dialect (default: sqlite)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="spider2-lite",
        help="Root path to Spider 2.0 dataset (default: spider2-lite)",
    )
    parser.add_argument(
        "--sqlite-dir",
        type=str,
        default="spider2-lite/resource/databases/spider2-localdb",
        help="Path containing Spider 2 local SQLite database files",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Maximum self-correction attempts per instance (default: 3)",
    )
    parser.add_argument(
        "--report-dir",
        type=str,
        default="",
        help="Optional subdirectory under reports/ for output",
    )

    return parser.parse_args()


def load_spider2_examples(data_dir: str | Path) -> list[dict[str, Any]]:
    """Loads all instances from spider2-lite.jsonl."""
    dir_path = Path(data_dir)
    jsonl_candidates = [
        dir_path / "spider2-lite.jsonl",
        PROJECT_ROOT / "spider2-lite" / "spider2-lite.jsonl",
    ]
    jsonl_path = None
    for cand in jsonl_candidates:
        if cand.exists():
            jsonl_path = cand
            break

    if not jsonl_path or not jsonl_path.exists():
        raise FileNotFoundError(f"Spider 2 dataset manifest not found (checked: {jsonl_candidates})")

    examples = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if line_str:
                examples.append(json.loads(line_str))
    return examples


def get_instance_dialect(instance_id: str) -> str:
    """Infers the database dialect from the instance ID prefix."""
    lowered = instance_id.lower()
    if lowered.startswith("local"):
        return "sqlite"
    elif lowered.startswith(("bq", "ga")):
        return "bigquery"
    elif lowered.startswith("sf"):
        return "snowflake"
    return "sqlite"


def get_gold_sql(data_dir: str | Path, instance_id: str) -> str:
    """Reads gold reference SQL from evaluation_suite/gold/sql/ if present."""
    base_dir = Path(data_dir)
    sql_path = base_dir / "evaluation_suite" / "gold" / "sql" / f"{instance_id}.sql"
    if not sql_path.exists():
        return ""
    try:
        with open(sql_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def get_gold_results(data_dir: str | Path, instance_id: str) -> list[list[dict[str, Any]]]:
    """Loads pre-executed gold benchmark CSV results for an instance."""
    exec_dir = Path(data_dir) / "evaluation_suite" / "gold" / "exec_result"
    results = []

    exact_csv = exec_dir / f"{instance_id}.csv"
    if exact_csv.exists():
        try:
            results.append(pd.read_csv(exact_csv).to_dict(orient="records"))
        except Exception:
            pass

    pattern = str(exec_dir / f"{instance_id}_*.csv")
    for f in sorted(glob.glob(pattern)):
        try:
            results.append(pd.read_csv(f).to_dict(orient="records"))
        except Exception:
            pass

    return results


def simulate_agent_run(
    example: dict[str, Any],
    gold_sql: str = "",
) -> dict[str, Any]:
    """
    Simulates realistic agent execution for Spider 2.0 evaluation in dry-run mode.
    Simulates token usage with tokens_by_node for exploration_selector, code_agent, and response.
    """
    instance_id = example.get("instance_id", "sim_0")
    numeric_id = 1
    for char in instance_id:
        if char.isdigit():
            numeric_id = int(char)
            break

    exp_tokens = {"input": 450 + (numeric_id * 15), "output": 40 + (numeric_id * 2), "total": 490 + (numeric_id * 17), "calls": 1}
    code_tokens = {"input": 3200 + (numeric_id * 120), "output": 410 + (numeric_id * 25), "total": 3610 + (numeric_id * 145), "calls": 2}
    resp_tokens = {"input": 600 + (numeric_id * 30), "output": 90 + (numeric_id * 8), "total": 690 + (numeric_id * 38), "calls": 1}

    simulated_tokens_by_node = {
        "planner": {"input": 0, "output": 0, "total": 0, "calls": 0},
        "rag_enrichment": {"input": 0, "output": 0, "total": 0, "calls": 0},
        "exploration_selector": exp_tokens,
        "code_agent": code_tokens,
        "graph_router": {"input": 0, "output": 0, "total": 0, "calls": 0},
        "graph_generator": {"input": 0, "output": 0, "total": 0, "calls": 0},
        "response": resp_tokens,
    }

    tot_in = exp_tokens["input"] + code_tokens["input"] + resp_tokens["input"]
    tot_out = exp_tokens["output"] + code_tokens["output"] + resp_tokens["output"]
    tot_all = exp_tokens["total"] + code_tokens["total"] + resp_tokens["total"]

    candidate_sql = gold_sql if gold_sql else "SELECT 1;"

    if numeric_id % 3 == 0:
        history = [
            {
                "sql": "SELECT * FROM non_existent_table WHERE id = 1",
                "erro": "no such table: non_existent_table",
                "prompt": "Simulated initial prompt to LLM",
                "contexto": "Simulated schema context",
                "raciocinio": "Simulated initial thought before syntax failure",
                "tokens_input": tot_in // 2,
                "tokens_output": tot_out // 2,
                "tokens_total": tot_all // 2,
            },
            {
                "sql": candidate_sql,
                "erro": "",
                "prompt": "Simulated correction prompt with error feedback",
                "contexto": "Simulated schema context",
                "raciocinio": "Simulated self-correction thought fixing the table name",
                "tokens_input": tot_in // 2,
                "tokens_output": tot_out // 2,
                "tokens_total": tot_all // 2,
            },
        ]
        tentativas = 2
    else:
        history = [
            {
                "sql": candidate_sql,
                "erro": "",
                "prompt": "Simulated prompt to LLM",
                "contexto": "Simulated schema context",
                "raciocinio": "Simulated Chain-of-Thought reasoning",
                "tokens_input": tot_in,
                "tokens_output": tot_out,
                "tokens_total": tot_all,
            }
        ]
        tentativas = 1

    return {
        "sql_gerada": candidate_sql,
        "status": "aprovado",
        "feedback_critico": "Approved by critic",
        "erro_execucao": "",
        "tentativas_loop": tentativas,
        "tokens_input": tot_in,
        "tokens_output": tot_out,
        "tokens_total": tot_all,
        "tokens_by_node": simulated_tokens_by_node,
        "historico_tentativas": history,
        "raciocinio_agente": history[-1]["raciocinio"],
        "contexto_prompt_agente": history[-1]["contexto"],
        "ultimo_prompt": history[-1]["prompt"],
    }


def _calculate_statistics(values: list[float]) -> dict[str, float]:
    """Computes distribution statistics (mean, max, min, median, P75, P90, P95)."""
    if not values:
        return {"mean": 0.0, "max": 0.0, "min": 0.0, "median": 0.0, "p75": 0.0, "p90": 0.0, "p95": 0.0}

    sorted_vals = sorted(values)
    n = len(sorted_vals)

    def percentile(p: float) -> float:
        idx = (n - 1) * (p / 100.0)
        idx_floor = int(idx)
        idx_ceil = min(idx_floor + 1, n - 1)
        weight = idx - idx_floor
        return sorted_vals[idx_floor] * (1.0 - weight) + sorted_vals[idx_ceil] * weight

    return {
        "mean": sum(sorted_vals) / n,
        "max": max(sorted_vals),
        "min": min(sorted_vals),
        "median": percentile(50.0),
        "p75": percentile(75.0),
        "p90": percentile(90.0),
        "p95": percentile(95.0),
    }


def generate_markdown_report(
    report_path: Path | str,
    summary: dict[str, Any],
    all_rows: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    config_info: dict[str, Any],
) -> None:
    """Generates a comprehensive Markdown evaluation report written in 100% English."""
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    ts = config_info.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("# Spider 2.0 Evaluation Report")
    lines.append("")
    lines.append(f"**Generated on:** {ts}")
    lines.append("")

    # Configuration section
    lines.append("## Configuration")
    lines.append("")
    lines.append("| Parameter | Value |")
    lines.append("|---|---|")
    lines.append(f"| Model | `{config_info.get('model')}` |")
    lines.append(f"| Provider | `{config_info.get('provider')}` |")
    raw_kwargs = config_info.get("model_kwargs") or {}
    sanitized_kwargs = {
        k: ("***REDACTED***" if any(s in str(k).lower() for s in SENSITIVE_KEYWORDS) else v)
        for k, v in raw_kwargs.items()
    }
    lines.append(f"| ModelKwargs | `{sanitized_kwargs or 'default'}` |")
    lines.append(f"| Dialect | `{config_info.get('dialect')}` |")
    lines.append(f"| Sample Size | {config_info.get('sample_size')} |")
    lines.append(f"| Seed | {config_info.get('seed')} |")
    lines.append(f"| Timeout | {config_info.get('timeout')}s |")
    lines.append(f"| RAG | {'ON' if config_info.get('use_rag') else 'OFF'} |")
    lines.append(f"| EnrichRAG | {'ON' if config_info.get('enrich_rag') else 'OFF'} |")
    lines.append(f"| CoT | {'ON' if config_info.get('use_cot') else 'OFF'} |")
    lines.append(f"| DataExploration | {'ON' if config_info.get('use_data_exploration') else 'OFF'} |")
    lines.append(f"| ExplorationSelector | `{config_info.get('exploration_selector')}` |")
    lines.append(f"| InferFKs | {'ON' if config_info.get('infer_fks') else 'OFF'} |")
    lines.append(f"| WithGraphs | {'ON' if config_info.get('with_graphs') else 'OFF'} |")
    lines.append(f"| UseEvidence | {'ON' if config_info.get('use_evidence') else 'OFF'} |")
    lines.append("")

    # Summary section
    total_q = summary.get("total_questions", summary.get("total_perguntas", len(all_rows)))
    ex_count = summary.get("ex_final_count", summary.get("perguntas_aprovadas", 0))
    ex_rate = summary.get("ex_final_rate", summary.get("taxa_aprovacao", 0.0) * 100)
    pass1_count = summary.get("ex_pass_at_1_count", summary.get("perguntas_1a_tentativa", 0))
    pass1_rate = summary.get("ex_pass_at_1_rate", summary.get("taxa_1a_tentativa", 0.0) * 100)

    lines.append("## Executive Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Total Questions Evaluated | {total_q} |")
    lines.append(f"| Total Execution Attempts | {summary.get('total_tentativas', len(all_rows))} |")
    lines.append(f"| Average Attempts per Question | {summary.get('tentativas_media', 1.0):.2f} |")
    lines.append(f"| Execution Accuracy (Final EX) | **{ex_count}/{total_q} ({ex_rate:.2f}%)** |")
    lines.append(f"| Pass@1 EX Accuracy | **{pass1_count}/{total_q} ({pass1_rate:.2f}%)** |")
    lines.append(f"| Average Column Recall (Final) | {summary.get('avg_recall', 0.0):.4f} |")
    lines.append(f"| Average Column Precision (Final) | {summary.get('avg_precision', 0.0):.4f} |")
    lines.append(f"| Average Column F1 Score (Final) | {summary.get('avg_f1', 0.0):.4f} |")
    lines.append(f"| Average SQL Similarity | {summary.get('similarity_media', 0.0):.4f} |")
    lines.append(f"| Failures (Mismatches) | {len(failures)}/{total_q} |")
    lines.append(f"| Average Latency per Attempt | {summary.get('tempo_medio_ms', 0.0):.2f} ms |")
    lines.append("")

    # Node-Level Token Consumption Table
    node_tokens = summary.get("node_tokens")
    if node_tokens and isinstance(node_tokens, dict):
        lines.append("## Node-Level Token Consumption (Average per Question & Run Total)")
        lines.append("")
        lines.append(
            "| Node | Calls | Total Input | Total Output | Total Tokens | Avg/Q Total | % Total |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for node_name, stats in node_tokens.items():
            if isinstance(stats, dict):
                calls = stats.get("calls", 0)
                t_in = stats.get("total_input", 0)
                t_out = stats.get("total_output", 0)
                t_all = stats.get("total_all", 0)
                avg_q = stats.get("avg_total_per_question", 0.0)
                pct = stats.get("pct_of_total_tokens", 0.0)
                lines.append(
                    f"| `{node_name}` | {calls} | {t_in:,} | {t_out:,} | {t_all:,} | {avg_q:.1f} | {pct:.2f}% |"
                )
        lines.append("")

    # Cost & Latency Distribution Table
    input_tokens = [float(r.get("tokens_input", 0) or 0) for r in all_rows]
    output_tokens = [float(r.get("tokens_output", 0) or 0) for r in all_rows]
    total_tokens = [float(r.get("tokens_total", 0) or 0) for r in all_rows]
    latencies = [float(r.get("tempo_agente_ms", 0) or 0) for r in all_rows]

    stats_input = _calculate_statistics(input_tokens)
    stats_output = _calculate_statistics(output_tokens)
    stats_total = _calculate_statistics(total_tokens)
    stats_latency = _calculate_statistics(latencies)

    lines.append("## Cost and Latency Distributions")
    lines.append("")
    lines.append("| Metric | Mean | Max | Min | Median | P75 | P90 | P95 |")
    lines.append("|---|---|---|---|---|---|---|---|")

    def fmt_stats(label: str, stats: dict[str, float], is_latency: bool = False) -> str:
        fmt = ".2f" if is_latency else ".1f"
        suffix = " ms" if is_latency else ""
        return (
            f"| {label} "
            f"| {stats['mean']:{fmt}}{suffix} "
            f"| {stats['max']:{fmt}}{suffix} "
            f"| {stats['min']:{fmt}}{suffix} "
            f"| {stats['median']:{fmt}}{suffix} "
            f"| {stats['p75']:{fmt}}{suffix} "
            f"| {stats['p90']:{fmt}}{suffix} "
            f"| {stats['p95']:{fmt}}{suffix} |"
        )

    lines.append(fmt_stats("Input Tokens", stats_input))
    lines.append(fmt_stats("Output Tokens", stats_output))
    lines.append(fmt_stats("Total Tokens", stats_total))
    lines.append(fmt_stats("Latency", stats_latency, is_latency=True))
    lines.append("")

    # Results by question table
    lines.append("## Per-Instance Results")
    lines.append("")
    lines.append("| Instance ID | DB | Question | Match | F1 | Similarity | Latency (ms) | Tokens Total |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in all_rows:
        q_short = str(r.get("pergunta_usuario", "")).replace("\n", " ")[:50]
        match_icon = "✅" if r.get("resultado_exato_match") is True else (
            "❌" if r.get("resultado_exato_match") is False else "⚠️"
        )
        lines.append(
            f"| `{r.get('id_exemplo')}` "
            f"| `{r.get('db_id')}` "
            f"| {q_short}... "
            f"| {match_icon} "
            f"| {float(r.get('resultado_f1', 0.0) or 0.0):.2f} "
            f"| {float(r.get('similarity_score_sql', 0.0) or 0.0):.2f} "
            f"| {float(r.get('tempo_agente_ms', 0.0) or 0.0):.0f} "
            f"| {int(r.get('tokens_total', 0) or 0):,} |"
        )
    lines.append("")

    # Detailed failure cases
    if failures:
        lines.append("## Detailed Failure Cases")
        lines.append("")
        lines.append(f"Total: **{len(failures)}** instance(s) did not achieve exact match.")
        lines.append("")
        for i, f in enumerate(failures, 1):
            lines.append(f"### Failure {i} — Instance `{f['id']}` (`{f['db_id']}`)")
            lines.append("")
            lines.append(f"**Question:** {f.get('pergunta')}")
            lines.append("")
            lines.append(
                f"**Metrics:** F1: `{f.get('f1', 0.0):.4f}` | "
                f"Precision: `{f.get('precision', 0.0):.4f}` | "
                f"Recall: `{f.get('recall', 0.0):.4f}`"
            )
            lines.append("")
            if f.get("erro_execucao"):
                lines.append(f"❌ **Execution Error:** `{f['erro_execucao']}`")
                lines.append("")

            lines.append("**Gold SQL:**")
            lines.append(f"```sql\n{f.get('query_ouro') or '(evaluated via official CSVs)'}\n```")
            lines.append("")

            attempts = f.get("historico_tentativas") or []
            if attempts:
                lines.append("#### Attempts:")
                for att_idx, att in enumerate(attempts, 1):
                    lines.append(f"##### Attempt {att_idx}:")
                    prompt = att.get("prompt", "")
                    if prompt:
                        lines.append("<details><summary>Prompt</summary>")
                        lines.append(f"```\n{prompt}\n```</details>")
                    thought = att.get("raciocinio", "")
                    if thought:
                        lines.append("<details><summary>Reasoning / CoT</summary>")
                        lines.append(f"{thought}</details>")
                    lines.append(f"**Generated SQL:**\n```sql\n{att.get('sql', '(empty)')}\n```")
                    err = att.get("erro", "")
                    lines.append(f"**Status / Error:** {err if err else 'EXEC_OK'}")
                    lines.append("")
            lines.append("---")
            lines.append("")
    else:
        lines.append("## Detailed Failure Cases")
        lines.append("")
        lines.append("🎉 **Zero failures!** All evaluated instances matched expected results.")
        lines.append("")

    with open(report_path, "w", encoding="utf-8") as f_out:
        f_out.write("\n".join(lines))


def main() -> None:
    args = parse_arguments()

    print("\n" + "=" * 80)
    print("TEXTTOINSIGHT - SPIDER 2.0 BENCHMARK EVALUATION ORCHESTRATOR")
    print("=" * 80)

    # 1. Resolve model and provider
    provider, concrete_model = resolve_model_and_provider(args.model)
    print(f"Provider:           {provider}")
    print(f"Concrete Model:     {concrete_model}")

    # 2. Determine API Key with Secret-Leak Guardrail
    api_key_env_var = args.api_key_env
    if not api_key_env_var:
        if provider == "google":
            api_key_env_var = "GOOGLE_API_KEY"
        elif provider == "openai":
            api_key_env_var = "OPENAI_API_KEY"
        else:
            api_key_env_var = f"{provider.upper()}_API_KEY"

    api_key = ""
    if not args.dry_run:
        api_key = os.getenv(api_key_env_var, "")
        if not api_key:
            print(f"❌ Error: Environment variable '{api_key_env_var}' is not set or empty in environment/.env.")
            sys.exit(1)
    else:
        api_key = "dry-run-key"
        print("⚡ [DRY-RUN MODE ACTIVE] Simulating LLM agent responses. No API calls or costs incurred.")

    # 3. Model kwargs preparation (merging temperature & max-tokens shortcuts)
    model_kwargs = dict(args.model_kwargs or [])
    if args.temperature is not None:
        model_kwargs["temperature"] = args.temperature
    if args.max_tokens is not None:
        model_kwargs["max_tokens"] = args.max_tokens

    infer_fks = (args.infer_fks == "on")
    with_graphs = (args.with_graphs == "on")
    use_cot = (args.cot == "on")
    use_data_exploration = (args.data_exploration == "on")
    use_rag = (args.rag == "on")
    enrich_rag = (args.enrich_rag == "on")
    use_evidence = (args.use_evidence == "on")

    # 4. Resolve Output Directory Structure (reports/spider2/run_<timestamp>/)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    if args.output:
        out_path = Path(args.output)
        if out_path.suffix == ".csv":
            run_dir = out_path.parent
            csv_path = out_path
        else:
            run_dir = out_path
            csv_path = run_dir / f"spider2_eval_{timestamp}.csv"
    else:
        run_dir = Path("reports") / "spider2" / f"run_{timestamp}"
        csv_path = run_dir / f"spider2_eval_{timestamp}.csv"

    run_dir.mkdir(parents=True, exist_ok=True)
    report_md_path = run_dir / f"spider2_eval_{timestamp}.md"
    empirico_md_path = run_dir / f"spider2_eval_{timestamp}_empirico.md"
    empirico_dir = run_dir / "empirico"

    log_path = Path(args.log_file) if args.log_file else (run_dir / f"spider2_eval_{timestamp}.log")
    logger_enabled = (args.log == "on")

    # 5. Load and filter dataset instances
    print(f"\n📂 Loading Spider 2 dataset from '{args.data_dir}'...")
    try:
        all_examples = load_spider2_examples(args.data_dir)
        print(f"✓ Total instances loaded: {len(all_examples)}")
    except Exception as e:
        print(f"❌ Failed to load dataset: {e}")
        sys.exit(1)

    # Dialect filtering
    if args.dialect != "all":
        examples = [ex for ex in all_examples if get_instance_dialect(ex.get("instance_id", "")) == args.dialect]
        print(f"✓ Filtered by dialect '{args.dialect}': {len(examples)} instances")
    else:
        examples = list(all_examples)

    if args.db_filter:
        examples = [ex for ex in examples if ex.get("db") == args.db_filter]
        print(f"✓ Filtered by db_id='{args.db_filter}': {len(examples)} instances")

    if args.question_filter:
        qf_lower = args.question_filter.lower()
        examples = [ex for ex in examples if qf_lower in ex.get("question", "").lower()]
        print(f"✓ Filtered by question substring: {len(examples)} instances")

    if args.seed is not None:
        random.seed(args.seed)
    if args.sample_size is not None and args.sample_size < len(examples):
        examples = random.sample(examples, k=args.sample_size)
    print(f"✓ Selected {len(examples)} instances for evaluation.\n")

    # 6. Initialize Components
    sqlite_executor = Spider2QueryExecutor(database_dir=args.sqlite_dir, timeout_seconds=args.timeout)
    reporter = CSVReporter(csv_path)

    config_info = {
        "timestamp": timestamp,
        "provider": provider,
        "model": concrete_model,
        "model_kwargs": model_kwargs,
        "dialect": args.dialect,
        "sample_size": args.sample_size or len(examples),
        "seed": args.seed,
        "timeout": args.timeout,
        "use_rag": use_rag,
        "enrich_rag": enrich_rag,
        "use_cot": use_cot,
        "use_data_exploration": use_data_exploration,
        "exploration_selector": args.exploration_selector,
        "infer_fks": infer_fks,
        "with_graphs": with_graphs,
        "use_evidence": use_evidence,
        "db_filter": args.db_filter,
        "question_filter": args.question_filter,
    }

    start_run_time = time.time()
    all_rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    engine_cache: dict[str, InsightEngine] = {}
    db_engines: dict[str, Any] = {}

    with SpiderExecutionLogger(
        output_dir=run_dir,
        timestamp=timestamp,
        enabled=logger_enabled,
        log_file=log_path,
    ) as logger:
        logger.log_run_header(config_info)

        for idx, ex in enumerate(examples, 1):
            instance_id = ex.get("instance_id", "")
            db_id = ex.get("db", "")
            question = ex.get("question", "")
            instance_dialect = get_instance_dialect(instance_id)

            # Load external knowledge if enabled
            external_knowledge = ""
            external_file = ex.get("external_knowledge")
            if use_evidence and external_file:
                ek_path = Path(args.data_dir) / "resource" / "documents" / external_file
                if ek_path.exists():
                    external_knowledge = ek_path.read_text(encoding="utf-8").strip()
                    prompt_question = f"{question}\n\n<additional_context>\n{external_knowledge}\n</additional_context>"
                else:
                    prompt_question = question
            else:
                prompt_question = question

            # Retrieve gold query and results
            gold_sql = get_gold_sql(args.data_dir, instance_id)
            gold_results_list = get_gold_results(args.data_dir, instance_id)

            print(f"[{idx}/{len(examples)}] Instance: {instance_id} | DB: {db_id} | Dialect: {instance_dialect}")
            print(f"     Question: {question[:75]}...")

            # Run evaluation inside Tee stream
            captured_trace = ""
            start_agent_time = time.time()
            with logger.capture_trace() as get_trace:
                if args.dry_run:
                    agent_result = simulate_agent_run(example=ex, gold_sql=gold_sql)
                    tempo_total = (time.time() - start_agent_time) * 1000.0
                else:
                    # Resolve query executor and initialize engine
                    if instance_dialect == "sqlite":
                        try:
                            db_path = str(sqlite_executor.get_db_path(db_id))
                        except FileNotFoundError as e:
                            print(f"     ❌ Database file not found: {e}")
                            continue

                        if db_id not in engine_cache:
                            db_engine = create_engine(f"sqlite:///file:{Path(db_path).resolve()}?mode=ro&uri=true")
                            db_engines[db_id] = db_engine
                            engine_cache[db_id] = InsightEngine(
                                api_key=api_key,
                                model=concrete_model,
                                db_engine=db_engine,
                                hitl=False,
                                show_output=False,
                                enable_graphs=with_graphs,
                                use_cot=use_cot,
                                use_data_exploration=use_data_exploration,
                                use_exploration_selector=args.exploration_selector,
                                use_rag=use_rag,
                                enrich_rag=enrich_rag,
                                inferir_fks_virtuais=infer_fks,
                                model_kwargs=model_kwargs,
                            )
                        engine = engine_cache[db_id]
                    else:
                        print(f"     ⚠️ Non-sqlite dialect '{instance_dialect}' currently evaluated via dry-run/adapters.")
                        continue

                    try:
                        agent_result = engine.run(
                            thread_id=f"spider2_eval_{instance_id}",
                            query=prompt_question,
                        )
                    except Exception as e:
                        print(f"     ❌ Agent runtime error: {e}")
                        agent_result = {
                            "sql_gerada": "",
                            "status": "erro",
                            "erro_execucao": str(e),
                            "tentativas_loop": 1,
                            "tokens_input": 0,
                            "tokens_output": 0,
                            "tokens_total": 0,
                            "historico_tentativas": [],
                        }
                    tempo_total = (time.time() - start_agent_time) * 1000.0

                captured_trace = get_trace()

            # Process candidate SQL
            query_agente = str(agent_result.get("sql_gerada") or "").strip()
            erro_exec = str(agent_result.get("erro_execucao") or "").strip()
            tentativas = int(agent_result.get("tentativas_loop", 1) or 1)
            historico_tent = agent_result.get("historico_tentativas") or []

            tokens_input = int(agent_result.get("tokens_input", 0) or 0)
            tokens_output = int(agent_result.get("tokens_output", 0) or 0)
            tokens_total = int(agent_result.get("tokens_total", 0) or (tokens_input + tokens_output))
            tokens_by_node = agent_result.get("tokens_by_node")

            viz_acionado = "grafico_gerado" in agent_result
            viz_sucesso = bool(agent_result.get("grafico_gerado", False))

            query_1a_tentativa = historico_tent[0].get("sql", "") if historico_tent else query_agente

            # Execute agent candidate query against DBMS
            executor = get_spider_executor(instance_dialect, database_dir=args.sqlite_dir, timeout_seconds=args.timeout)
            agent_execution: dict[str, Any] = {"success": False, "results": [], "row_count": 0, "error": ""}

            if query_agente and not erro_exec:
                agent_execution = executor.execute_query(db_id, query_agente)
                if not agent_execution["success"]:
                    erro_exec = agent_execution["error"]

            # Evaluate execution match & F1 against gold results
            resultado_exato_match: bool | None = None
            similarity_score = 0.0
            f1_scores = {"f1": 0.0, "precision": 0.0, "recall": 0.0}

            if gold_sql and query_agente:
                similarity_score = sql_similarity_score(gold_sql, query_agente)

            if agent_execution["success"]:
                best_match = False
                best_f1 = {"f1": 0.0, "precision": 0.0, "recall": 0.0}

                # Evaluate against gold results list if available
                if gold_results_list:
                    for g_res in gold_results_list:
                        m = results_exact_match(g_res, agent_execution["results"])
                        f = results_f1_score(g_res, agent_execution["results"])
                        if m:
                            best_match = True
                        if f["f1"] > best_f1["f1"]:
                            best_f1 = f
                    resultado_exato_match = best_match
                    f1_scores = best_f1
                elif gold_sql:
                    gold_exec = executor.execute_query(db_id, gold_sql)
                    if gold_exec["success"]:
                        resultado_exato_match = results_exact_match(gold_exec["results"], agent_execution["results"])
                        f1_scores = results_f1_score(gold_exec["results"], agent_execution["results"])
                    else:
                        erro_exec = f"Gold execution failed: {gold_exec['error']}"

            match_success = (resultado_exato_match is True)
            print(
                f"     Result: EX={resultado_exato_match} | F1={f1_scores['f1']:.2f} | "
                f"Similarity={similarity_score:.2f} | Latency={tempo_total:.0f}ms"
            )

            # Record failure case if not matched
            if not match_success:
                failures.append({
                    "id": instance_id,
                    "db_id": db_id,
                    "pergunta": question,
                    "query_ouro": gold_sql,
                    "query_agente": query_agente,
                    "f1": f1_scores["f1"],
                    "precision": f1_scores["precision"],
                    "recall": f1_scores["recall"],
                    "erro_execucao": erro_exec,
                    "historico_tentativas": historico_tent,
                })

            # Build and append CSV row
            row = build_comparison_row(
                id_exemplo=instance_id,
                tentativa_numero=tentativas,
                db_id=db_id,
                pergunta=question,
                query_ouro=gold_sql,
                query_agente=query_agente,
                tempo_agente_ms=tempo_total,
                veredito_critico="aprovado" if match_success else ("reprovado" if query_agente else "erro"),
                feedback_critico="",
                erro_execucao=erro_exec,
                resultado_exato_match=resultado_exato_match,
                similarity_score=similarity_score,
                resultado_f1=f1_scores["f1"],
                resultado_precision=f1_scores["precision"],
                resultado_recall=f1_scores["recall"],
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                tokens_total=tokens_total,
                viz_acionado=viz_acionado,
                viz_sucesso=viz_sucesso,
                resultado_exato_match_1a_tentativa=None,
                resultado_f1_1a_tentativa=0.0,
                query_1a_tentativa=query_1a_tentativa,
                tokens_by_node=tokens_by_node,
            )
            reporter.append_row(row)
            all_rows.append(row)

            # Write query log block
            logger.log_query(
                instance_id=instance_id,
                db_id=db_id,
                dialect=instance_dialect,
                question=question,
                external_knowledge=external_knowledge,
                gold_sql=gold_sql,
                agent_result=agent_result,
                match_success=match_success,
                similarity_score=similarity_score,
                time_ms=tempo_total,
                gold_results=gold_results_list[0] if gold_results_list else None,
                agent_results=agent_execution["results"] if agent_execution["success"] else None,
                captured_trace=captured_trace,
                precision=f1_scores["precision"],
                recall=f1_scores["recall"],
                f1=f1_scores["f1"],
                tokens_by_node=tokens_by_node,
            )

        # Dispose database engines cleanly
        for engine_item in db_engines.values():
            try:
                engine_item.dispose()
            except Exception:
                pass

        total_elapsed = time.time() - start_run_time
        summary = reporter.generate_summary(all_rows)
        logger.log_run_footer(summary, total_elapsed)

    # 7. Generate Reports
    print("\n" + "=" * 80)
    print("📊 SPIDER 2.0 FINAL EVALUATION SUMMARY")
    print("=" * 80)
    print(f"Total Questions Evaluated:          {summary['total_perguntas']}")
    print(f"Execution Accuracy (Final EX):      {summary['perguntas_aprovadas']}/{summary['total_perguntas']} ({summary['taxa_aprovacao'] * 100:.2f}%)")
    print(f"Pass@1 EX Accuracy:                 {summary['perguntas_1a_tentativa']}/{summary['total_perguntas']} ({summary['taxa_1a_tentativa'] * 100:.2f}%)")
    print(f"Average Column F1 Score:            {summary['avg_f1']:.4f}")
    print(f"Average SQL Similarity:             {summary['similarity_media']:.4f}")
    print(f"Total Tokens Consumed:              {summary['tokens']['total_all']:,}")
    print(f"Total Elapsed Time:                 {total_elapsed:.1f}s")
    print(f"\n✅ CSV saved to:                     {csv_path}")

    # Generate Markdown report in English
    generate_markdown_report(
        report_path=report_md_path,
        summary=summary,
        all_rows=all_rows,
        failures=failures,
        config_info=config_info,
    )
    print(f"✅ Markdown report saved to:         {report_md_path}")
    if logger_enabled:
        print(f"✅ Log file saved to:                {log_path}")

    # Generate Empirical Analysis Report & Graphs
    try:
        gerar_relatorio_empirico_completo(
            report_path=str(empirico_md_path),
            dataset_label="Spider 2.0 Lite",
            all_rows=all_rows,
            output_dir=str(empirico_dir),
        )
        print(f"✅ Empirical report saved to:        {empirico_md_path}")
        print(f"   Charts and CSV tables saved to:  {empirico_dir}/")
    except Exception as e:
        print(f"⚠️  Empirical analysis report generation note: {e}")


if __name__ == "__main__":
    main()
