"""
src/spider/metrics.py

Metrics and evaluation utilities for SQL comparison against Spider 2.0.

Provides:
- SQL text normalization and SequenceMatcher similarity score.
- Column-level result comparison following official Spider 2.0 evaluation logic.
- Column-level Precision, Recall, and F1 score calculation.
- Comparison row builder for CSV reporting with per-node token tracking.
"""

from __future__ import annotations

import difflib
import json
import math
import re
from typing import Any

import pandas as pd

# Absolute tolerance for floating-point comparisons
_TOLERANCE = 1e-2


def normalize_sql(sql: str) -> str:
    """
    Normalizes a SQL query for robust string comparison.

    - Strips line comments (-- ...) and block comments (/* ... */)
    - Strips trailing semicolons and whitespace
    - Uppercases tokens
    - Collapses consecutive whitespace characters

    Parameters:
        sql: Raw SQL query string.

    Returns:
        Normalized SQL string.
    """
    if not sql:
        return ""
    # Remove line comments
    sql = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
    # Remove block comments
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    # Remove trailing semicolons and whitespace
    sql = sql.rstrip("; \n\t")
    # Uppercase
    sql = sql.upper()
    # Collapse multiple whitespace
    sql = re.sub(r"\s+", " ", sql).strip()
    return sql


def sql_similarity_score(sql1: str, sql2: str) -> float:
    """
    Computes text similarity score between two SQL queries using difflib.SequenceMatcher.

    Parameters:
        sql1: First SQL query.
        sql2: Second SQL query.

    Returns:
        Similarity score between 0.0 and 1.0 (1.0 = identical).
    """
    norm1 = normalize_sql(sql1)
    norm2 = normalize_sql(sql2)

    if not norm1 and not norm2:
        return 1.0
    if not norm1 or not norm2:
        return 0.0

    matcher = difflib.SequenceMatcher(None, norm1, norm2)
    return matcher.ratio()


def _vectors_match(v1: list[Any], v2: list[Any], tol: float = _TOLERANCE, ignore_order: bool = False) -> bool:
    """
    Compares two column vectors element-by-element following official Spider 2.0 rules:
    - Accepts absolute numeric tolerance tol for numbers.
    - Treats NaN / None values as equal.
    - Sorts vectors before comparison if ignore_order is True.
    """
    if ignore_order:
        v1 = sorted(v1, key=lambda x: (x is None, str(x), isinstance(x, (int, float))))
        v2 = sorted(v2, key=lambda x: (x is None, str(x), isinstance(x, (int, float))))

    if len(v1) != len(v2):
        return False

    for a, b in zip(v1, v2):
        if pd.isna(a) and pd.isna(b):
            continue
        elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if not math.isclose(float(a), float(b), abs_tol=tol):
                return False
        elif a != b:
            return False
    return True


def _results_to_dataframe(results: list[dict[str, Any]]) -> pd.DataFrame:
    """Converts a list of dictionary rows into a pandas DataFrame."""
    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results)


def compare_pandas_table(
    pred: pd.DataFrame,
    gold: pd.DataFrame,
    condition_cols: list[int] | None = None,
    ignore_order: bool = False,
) -> int:
    """
    Compares predicted DataFrame against gold DataFrame following Spider 2.0 official logic.

    For each column in gold (optionally filtered by condition_cols), checks whether
    at least one column in pred matches within numeric tolerance.

    Parameters:
        pred: Predicted result DataFrame.
        gold: Gold expected DataFrame.
        condition_cols: Indices of gold columns to evaluate. If None, evaluates all.
        ignore_order: If True, sorts values before comparing.

    Returns:
        1 if all gold columns are matched in pred, otherwise 0.
    """
    if condition_cols:
        gold_cols = gold.iloc[:, condition_cols]
    else:
        gold_cols = gold

    t_gold_list = gold_cols.transpose().values.tolist()
    t_pred_list = pred.transpose().values.tolist()

    for gold_vec in t_gold_list:
        if not any(_vectors_match(gold_vec, pred_vec, ignore_order=ignore_order)
                   for pred_vec in t_pred_list):
            return 0
    return 1


def compare_multi_pandas_table(
    pred: pd.DataFrame,
    multi_gold: list[pd.DataFrame],
    multi_condition_cols: list[Any] | None = None,
    multi_ignore_order: bool = False,
) -> int:
    """
    Compares predicted DataFrame against multiple valid gold answers.
    Returns 1 if pred matches at least one gold table.
    """
    if (
        multi_condition_cols is None
        or multi_condition_cols == []
        or multi_condition_cols == [[]]
        or multi_condition_cols == [None]
    ):
        multi_condition_cols = [[] for _ in range(len(multi_gold))]
    elif len(multi_gold) > 1 and not all(isinstance(s, list) for s in multi_condition_cols):
        multi_condition_cols = [multi_condition_cols for _ in range(len(multi_gold))]

    for i, gold in enumerate(multi_gold):
        if compare_pandas_table(pred, gold, multi_condition_cols[i], multi_ignore_order):
            return 1
    return 0


def results_exact_match(
    results_gold: list[dict[str, Any]],
    results_agent: list[dict[str, Any]],
    ignore_order: bool = True,
) -> bool:
    """
    Determines whether agent results match gold results according to official Spider 2.0 column matching.

    Parameters:
        results_gold: Gold query results as list[dict].
        results_agent: Agent query results as list[dict].
        ignore_order: Whether to ignore row ordering (default True).

    Returns:
        True if exact match, otherwise False.
    """
    if not results_gold and not results_agent:
        return True
    if not results_gold or not results_agent:
        return False

    gold_df = _results_to_dataframe(results_gold)
    pred_df = _results_to_dataframe(results_agent)

    if len(gold_df) != len(pred_df):
        return False

    return compare_pandas_table(pred_df, gold_df, ignore_order=ignore_order) == 1


def results_f1_score(
    results_gold: list[dict[str, Any]],
    results_agent: list[dict[str, Any]],
    ignore_order: bool = True,
) -> dict[str, float]:
    """
    Calculates column-level Precision, Recall, and F1 score following Spider 2.0 logic.

    Parameters:
        results_gold: Gold query results as list[dict].
        results_agent: Agent query results as list[dict].
        ignore_order: Whether to ignore row ordering (default True).

    Returns:
        Dict with keys: "precision", "recall", "f1" (floats between 0.0 and 1.0).
    """
    if not results_gold and not results_agent:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    if not results_gold:
        return {"precision": 0.0, "recall": 1.0, "f1": 0.0}
    if not results_agent:
        return {"precision": 1.0, "recall": 0.0, "f1": 0.0}

    gold_df = _results_to_dataframe(results_gold)
    pred_df = _results_to_dataframe(results_agent)

    max_rows = max(len(gold_df), len(pred_df))
    if len(gold_df) < max_rows:
        padding = pd.DataFrame(
            [[None] * gold_df.shape[1]] * (max_rows - len(gold_df)),
            columns=gold_df.columns,
        )
        gold_df = pd.concat([gold_df, padding], ignore_index=True)
    if len(pred_df) < max_rows:
        padding = pd.DataFrame(
            [[None] * pred_df.shape[1]] * (max_rows - len(pred_df)),
            columns=pred_df.columns,
        )
        pred_df = pd.concat([pred_df, padding], ignore_index=True)

    t_gold_list = gold_df.transpose().values.tolist()
    t_pred_list = pred_df.transpose().values.tolist()

    total_gold = len(t_gold_list)
    total_pred = len(t_pred_list)

    gold_matched = sum(
        1 for g in t_gold_list
        if any(_vectors_match(g, p, ignore_order=ignore_order) for p in t_pred_list)
    )
    pred_matched = sum(
        1 for p in t_pred_list
        if any(_vectors_match(p, g, ignore_order=ignore_order) for g in t_gold_list)
    )

    recall = gold_matched / total_gold if total_gold > 0 else 0.0
    precision = pred_matched / total_pred if total_pred > 0 else 0.0

    if precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * (precision * recall) / (precision + recall)

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def build_comparison_row(
    id_exemplo: int | str,
    tentativa_numero: int,
    db_id: str,
    pergunta: str,
    query_ouro: str,
    query_agente: str,
    tempo_agente_ms: float,
    veredito_critico: str,
    feedback_critico: str,
    erro_execucao: str,
    resultado_exato_match: bool | None,
    similarity_score: float,
    resultado_f1: float = 0.0,
    resultado_precision: float = 0.0,
    resultado_recall: float = 0.0,
    tokens_input: int = 0,
    tokens_output: int = 0,
    tokens_total: int = 0,
    viz_acionado: bool = False,
    viz_sucesso: bool = False,
    resultado_exato_match_1a_tentativa: bool | None = None,
    resultado_f1_1a_tentativa: float = 0.0,
    query_1a_tentativa: str = "",
    tokens_by_node: dict[str, Any] | str | None = None,
) -> dict[str, Any]:
    """
    Constructs a canonical evaluation row for CSV reporting.

    Parameters:
        id_exemplo: Instance or Question ID.
        tentativa_numero: Attempt index (1-based).
        db_id: Database ID.
        pergunta: Natural language user question.
        query_ouro: Gold reference SQL query.
        query_agente: Agent-generated SQL candidate.
        tempo_agente_ms: Execution latency in milliseconds.
        veredito_critico: Critic verdict.
        feedback_critico: Critic feedback text.
        erro_execucao: Execution error message, if any.
        resultado_exato_match: True/False for execution match, None if error.
        similarity_score: Sequence similarity score.
        resultado_f1: Column-level F1 score.
        resultado_precision: Column-level Precision.
        resultado_recall: Column-level Recall.
        tokens_input: Cumulative input tokens.
        tokens_output: Cumulative output tokens.
        tokens_total: Cumulative total tokens.
        viz_acionado: Whether visualization was requested.
        viz_sucesso: Whether visualization succeeded.
        resultado_exato_match_1a_tentativa: Pass@1 exact match indicator.
        resultado_f1_1a_tentativa: Pass@1 F1 score.
        query_1a_tentativa: SQL candidate from attempt 1.
        tokens_by_node: Node-level token usage dictionary or serialized JSON string.

    Returns:
        Dictionary conforming to CSVReporter.HEADERS.
    """
    if isinstance(tokens_by_node, dict):
        tokens_by_node_str = json.dumps(tokens_by_node, ensure_ascii=False, sort_keys=True)
    elif isinstance(tokens_by_node, str):
        tokens_by_node_str = tokens_by_node
    else:
        tokens_by_node_str = "{}"

    return {
        "id_exemplo": id_exemplo,
        "tentativa_numero": tentativa_numero,
        "db_id": db_id,
        "pergunta_usuario": pergunta,
        "query_ouro_spider": query_ouro,
        "query_agente_tentativa": query_agente,
        "tempo_agente_ms": round(tempo_agente_ms, 2),
        "veredito_critico": veredito_critico,
        "feedback_critico_recebido": feedback_critico,
        "erro_execucao": erro_execucao,
        "resultado_exato_match": resultado_exato_match if resultado_exato_match is not None else "",
        "similarity_score_sql": round(similarity_score, 4),
        "resultado_f1": resultado_f1,
        "resultado_precision": resultado_precision,
        "resultado_recall": resultado_recall,
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
        "tokens_total": tokens_total,
        "viz_acionado": viz_acionado,
        "viz_sucesso": viz_sucesso,
        "resultado_exato_match_1a_tentativa": resultado_exato_match_1a_tentativa if resultado_exato_match_1a_tentativa is not None else "",
        "resultado_f1_1a_tentativa": resultado_f1_1a_tentativa,
        "query_1a_tentativa": query_1a_tentativa,
        "tokens_by_node": tokens_by_node_str,
    }
