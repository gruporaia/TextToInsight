"""
src/bird/metrics.py

Metrics and evaluation module for the BIRD benchmark.
Responsible for:
- Normalizing SQL strings for comparison and similarity scoring.
- Calculating Execution Accuracy (EX), the primary academic benchmark metric.
- Type-normalizing row values (floats, strings, nulls, integers) to absorb driver variations.
- Performing multiset (Counter) and sequence-based execution comparisons.
- Constructing standardized evaluation result rows for CSV logging.
"""

import difflib
import math
import re
from collections import Counter
from typing import Any


# Regex pattern to check if a gold query explicitly requests ordered output
ORDER_BY_PATTERN = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)


def normalize_sql(sql: str) -> str:
    """
    Normalizes a SQL string for structural comparison and similarity analysis.

    Transformations:
    - Strips single-line comments (-- comment)
    - Strips multi-line block comments (/* comment */)
    - Strips trailing semicolons and whitespace
    - Normalizes consecutive whitespace characters to a single space
    - Converts text to uppercase

    Parameters:
        sql: Raw SQL query string.

    Returns:
        Cleaned, uppercase normalized SQL string.
    """
    if not sql:
        return ""

    # 1. Remove single line comments (-- ...)
    cleaned = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)

    # 2. Remove multi-line block comments (/* ... */)
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)

    # 3. Strip trailing semicolons, spaces, and newlines
    cleaned = cleaned.rstrip("; \n\t")

    # 4. Collapse whitespace sequences into single space and convert to uppercase
    cleaned = re.sub(r"\s+", " ", cleaned).strip().upper()

    return cleaned


def sql_similarity_score(sql1: str, sql2: str) -> float:
    """
    Calculates textual similarity ratio (between 0.0 and 1.0) between two SQL queries.
    Uses Python's difflib.SequenceMatcher on normalized SQL strings.

    Parameters:
        sql1: First SQL query.
        sql2: Second SQL query.

    Returns:
        Similarity score from 0.0 (completely distinct) to 1.0 (identical).
    """
    norm1 = normalize_sql(sql1)
    norm2 = normalize_sql(sql2)

    if not norm1 and not norm2:
        return 1.0
    if not norm1 or not norm2:
        return 0.0

    return difflib.SequenceMatcher(None, norm1, norm2).ratio()


def normalize_value(val: Any) -> Any:
    """
    Normalizes an individual cell value for robust cross-database comparison.

    Rules:
    - None / 'null' / 'NULL' / NaN -> None (SQL NULL standard)
    - bool -> preserved as bool
    - float -> rounded to 2 decimal places (absorbs IEEE 754 precision drift)
    - integer or whole float (e.g. 10.0) -> int
    - string -> stripped and lowercased

    Parameters:
        val: Raw cell value from execution results.

    Returns:
        Canonical normalized value.
    """
    # 1. Handle SQL NULL, None and NaN
    if val is None:
        return None

    if isinstance(val, float) and math.isnan(val):
        return None

    # 2. Preserve boolean values explicitly (since bool is a subclass of int in Python)
    if isinstance(val, bool):
        return val

    # 3. Handle integer values directly
    if isinstance(val, int):
        return val

    # 4. Handle float values: round to 2 decimal places to absorb floating-point drift
    if isinstance(val, float):
        # If float represents an exact integer (e.g. 5.0), convert to int for compatibility
        if val.is_integer():
            return int(val)
        return round(val, 2)

    # 5. Handle strings: check for null representations and numeric strings
    if isinstance(val, str):
        trimmed = val.strip()
        if trimmed.lower() in ("null", "none", ""):
            return None

        # Check if string represents an integer
        try:
            return int(trimmed)
        except ValueError:
            pass

        # Check if string represents a float
        try:
            float_val = float(trimmed)
            if float_val.is_integer():
                return int(float_val)
            return round(float_val, 2)
        except ValueError:
            pass

        # Default string normalization: trimmed lowercase
        return trimmed.lower()

    # Fallback for complex objects (dates, decimals)
    return str(val).strip().lower()


def _row_to_canonical_tuple(row: Any) -> tuple:
    """
    Converts a database row (tuple, list, sqlite3.Row, or dict) into a normalized tuple.
    Extracts values only, deliberately ignoring column names / aliases.
    """
    if isinstance(row, dict):
        return tuple(normalize_value(v) for v in row.values())
    if isinstance(row, (list, tuple)):
        return tuple(normalize_value(v) for v in row)
    return (normalize_value(row),)


def compare_bird_results(
    results_gold: list[Any] | None,
    results_agent: list[Any] | None,
    order_matters: bool | None = None,
    gold_sql: str | None = None,
) -> bool:
    """
    Calculates Execution Accuracy (EX) following the canonical BIRD benchmark protocol.

    Comparison Logic:
    1. Returns False if either result set is None.
    2. Returns True if both result sets are empty (empty set matches empty set).
    3. Returns False if row counts differ.
    4. Converts every row into a normalized value tuple, ignoring column aliases.
    5. Determines order sensitivity:
       - If order_matters is None, inspects gold_sql for 'ORDER BY'.
       - If order matters: checks exact sequential row equality.
       - If order does not matter: compares multisets using Counter(tuples),
         strictly enforcing that duplicate row frequencies match.

    Parameters:
        results_gold: Result rows from executing the gold standard query.
        results_agent: Result rows from executing the agent-generated query.
        order_matters: Explicit boolean override for order sensitivity.
        gold_sql: Optional gold query SQL text used to auto-detect ORDER BY.

    Returns:
        True if execution results functionally match, False otherwise.
    """
    # 1. Null safety checks
    if results_gold is None or results_agent is None:
        return False

    # 2. Fast path: check row count equality
    if len(results_gold) != len(results_agent):
        return False

    # 3. Empty result match: both returned zero rows
    if len(results_gold) == 0 and len(results_agent) == 0:
        return True

    # 4. Transform all rows into canonical normalized tuples
    try:
        gold_tuples = [_row_to_canonical_tuple(r) for r in results_gold]
        agent_tuples = [_row_to_canonical_tuple(r) for r in results_agent]
    except Exception:
        return False

    # 5. Determine order sensitivity
    if order_matters is None:
        if gold_sql and ORDER_BY_PATTERN.search(gold_sql):
            order_matters = True
        else:
            order_matters = False

    # 6. Ordered comparison: exact sequence match
    if order_matters:
        return gold_tuples == agent_tuples

    # 7. Unordered comparison: multiset frequency match using collections.Counter
    # Strictly enforces that identical rows occur with the exact same count
    return Counter(gold_tuples) == Counter(agent_tuples)


def build_bird_comparison_row(
    question_id: int,
    attempt_number: int,
    db_id: str,
    difficulty: str,
    question: str,
    evidence: str,
    gold_sql: str,
    agent_sql: str,
    time_ms: float,
    execution_status: str = "exec_ok",
    similarity_score: float = 0.0,
    execution_match: bool = False,
    error: str = "",
    tokens_input: int = 0,
    tokens_output: int = 0,
    tokens_total: int | None = None,
    critic_verdict: str | None = None,
    critic_feedback: str | None = None,
) -> dict[str, Any]:
    """
    Constructs a standardized 16-field dictionary for CSV reporting and audit logging.

    Parameters:
        question_id: BIRD question identifier integer.
        attempt_number: Re-try iteration index (1-based).
        db_id: Database identifier name.
        difficulty: BIRD difficulty tier ('simple', 'moderate', 'challenging').
        question: User prompt question text.
        evidence: Business rule / formula hint text.
        gold_sql: Ground-truth reference SQL query.
        agent_sql: Candidate SQL query generated by the agent.
        time_ms: Total agent execution time in milliseconds.
        execution_status: Execution status ('exec_ok', 'exec_erro', 'aprovado', etc.).
        similarity_score: Textual SQL similarity ratio between gold and candidate (0.0 to 1.0).
        execution_match: Execution Accuracy boolean (True if results match gold).
        error: Error message string if SQL execution failed in the sandbox.
        tokens_input: Prompt input tokens consumed.
        tokens_output: Completion output tokens generated.
        tokens_total: Total tokens consumed (defaults to input + output).
        critic_verdict: Deprecated alias for execution_status.
        critic_feedback: Deprecated alias for error.

    Returns:
        Structured dictionary ready for csv.DictWriter.
    """
    calc_total_tokens = tokens_total if tokens_total is not None else (tokens_input + tokens_output)
    status_str = str(critic_verdict if critic_verdict is not None else execution_status).strip()
    error_str = str(error if error else (critic_feedback or "")).strip()

    return {
        "question_id": question_id,
        "attempt_number": attempt_number,
        "db_id": str(db_id).strip(),
        "difficulty": str(difficulty).lower().strip(),
        "question": str(question).strip(),
        "evidence": str(evidence).strip() if evidence else "",
        "gold_sql": str(gold_sql).strip(),
        "agent_sql": str(agent_sql).strip() if agent_sql else "",
        "time_ms": round(float(time_ms), 2),
        "execution_status": status_str,
        "similarity_score": round(float(similarity_score), 4),
        "execution_match": bool(execution_match),
        "error": error_str,
        "tokens_input": int(tokens_input),
        "tokens_output": int(tokens_output),
        "tokens_total": int(calc_total_tokens),
    }

