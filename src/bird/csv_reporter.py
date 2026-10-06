"""
src/bird/csv_reporter.py

CSV reporter, statistical summary generator, and official BIRD prediction exporter.

Responsibilities:
1. BirdCSVReporter:
   - Persists execution trial details row-by-row into a CSV file.
   - Aggregates multi-attempt metrics (Final EX, Pass@1, Self-Correction Recovery, Token usage, Latency).
   - Stratifies results across BIRD difficulty tiers (simple, moderate, challenging).
   - Renders a rich Markdown evaluation report.
   - Exports the canonical BIRD 'predict_dev.json' file formatted for official evaluation scripts.
"""

from collections import defaultdict
import csv
from datetime import datetime
import json
from pathlib import Path
from typing import Any


def format_result_rows_markdown_table(
    results: list[tuple[Any, ...]] | list[dict[str, Any]] | None,
    column_names: list[str] | None = None,
    max_rows: int = 10,
) -> str:
    """
    Renders up to max_rows of database result rows as a clean GitHub-flavored Markdown table.
    Supports tuples, lists, or dictionary rows. Appends an overflow note if len > max_rows.
    """
    if results is None:
        return "*(unavailable)*"
    if len(results) == 0:
        return "*(0 rows returned / empty set)*"

    sample = results[:max_rows]
    lines: list[str] = []

    # Case 1: rows are dictionaries
    if isinstance(sample[0], dict):
        cols = list(sample[0].keys())
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
        for r in sample:
            vals = [str(r.get(c, "")).replace("\n", " ").replace("|", "\\|") for c in cols]
            lines.append("| " + " | ".join(vals) + " |")
    # Case 2: rows are tuples or lists
    else:
        num_cols = len(sample[0]) if isinstance(sample[0], (tuple, list)) else 1
        cols = column_names if (column_names and len(column_names) == num_cols) else [f"col_{i+1}" for i in range(num_cols)]
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
        for r in sample:
            if isinstance(r, (tuple, list)):
                vals = [str(v).replace("\n", " ").replace("|", "\\|") for v in r]
            else:
                vals = [str(r).replace("\n", " ").replace("|", "\\|")]
            lines.append("| " + " | ".join(vals) + " |")

    if len(results) > max_rows:
        lines.append(f"\n*... and {len(results) - max_rows} more row(s) (total: {len(results)})*")

    return "\n".join(lines)


class BirdCSVReporter:
    """
    Manages CSV persistence, summary metrics aggregation, and BIRD-compatible JSON exports.
    """

    # 16 standardized columns matching build_bird_comparison_row() in src/bird/metrics.py
    HEADERS: list[str] = [
        "question_id",
        "attempt_number",
        "db_id",
        "difficulty",
        "question",
        "evidence",
        "gold_sql",
        "agent_sql",
        "time_ms",
        "execution_status",
        "similarity_score",
        "execution_match",
        "error",
        "tokens_input",
        "tokens_output",
        "tokens_total",
    ]

    # Standard separator used by the official BIRD evaluation harness
    BIRD_PREDICTION_SEPARATOR: str = "\t----- ----- -----\t"

    def __init__(self, filepath: str | Path, predict_json_path: str | Path | None = None):
        """
        Initializes the CSV reporter.

        Args:
            filepath: Destination path for the CSV report file.
            predict_json_path: Optional destination path for official predict_dev.json.
                                Defaults to the same stem as filepath with a .json extension.
        """
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)

        if predict_json_path is not None:
            self.predict_json_path = Path(predict_json_path)
        else:
            self.predict_json_path = self.filepath.with_suffix(".json")

        self.predict_json_path.parent.mkdir(parents=True, exist_ok=True)

        # In-memory storage for rows processed during the active session
        self.rows: list[dict[str, Any]] = []
        self.failures: list[dict[str, Any]] = []
        self.self_corrections: list[dict[str, Any]] = []

        self._init_csv()

    def add_failure_case(self, case: dict[str, Any]) -> None:
        """Records a failure case for inclusion in the detailed markdown report."""
        self.failures.append(case)

    def add_self_correction_case(self, case: dict[str, Any]) -> None:
        """Records a self-correction case for inclusion in the detailed markdown report."""
        self.self_corrections.append(case)

    def rewrite_ordered_csv(self) -> None:
        """
        Re-writes the CSV file with all accumulated rows sorted by (question_id, attempt_number).
        Guarantees contiguous inspection ordering.
        """
        if not self.rows:
            return
        sorted_rows = sorted(
            self.rows,
            key=lambda r: (int(r.get("question_id", 0)), int(r.get("attempt_number", 1))),
        )
        self.rows = sorted_rows
        with open(self.filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.HEADERS)
            writer.writeheader()
            writer.writerows(sorted_rows)
            f.flush()

    def _init_csv(self) -> None:
        """
        Initializes the CSV file on disk.
        Writes header row if the file does not exist or is empty.
        """
        should_write_header = not self.filepath.exists() or self.filepath.stat().st_size == 0

        if should_write_header:
            with open(self.filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self.HEADERS)
                writer.writeheader()

    def append_row(self, row: dict[str, Any]) -> None:
        """
        Appends an evaluation attempt row to the in-memory log and writes it immediately to disk.

        Args:
            row: Dictionary containing evaluation columns.

        Raises:
            ValueError: If mandatory columns from HEADERS are missing.
        """
        missing_keys = set(self.HEADERS) - set(row.keys())
        if missing_keys:
            raise ValueError(
                f"Cannot append row to BirdCSVReporter. Missing mandatory keys: {sorted(missing_keys)}"
            )

        # Retain row in memory
        self.rows.append(row)

        # Append immediately to CSV file (flushing directly to ensure no data loss on interruption)
        with open(self.filepath, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.HEADERS)
            writer.writerow(row)
            f.flush()

    def save_predictions_json(
        self,
        predictions: dict[int | str, str],
        output_path: str | Path | None = None,
    ) -> Path:
        """
        Saves a dictionary of predictions directly to a JSON file.

        Args:
            predictions: Mapping of {question_id: sql_prediction_string}.
            output_path: Destination JSON path. Defaults to self.predict_json_path.

        Returns:
            Path object of the written JSON file.
        """
        target_path = Path(output_path) if output_path else self.predict_json_path
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Convert all keys to string for JSON standard compliance
        formatted_predictions = {str(k): str(v) for k, v in predictions.items()}

        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(formatted_predictions, f, indent=2, ensure_ascii=False)

        return target_path

    def export_predictions_from_rows(
        self,
        rows: list[dict[str, Any]] | None = None,
        output_path: str | Path | None = None,
        include_db_id: bool = True,
    ) -> Path:
        """
        Extracts final generated SQL queries from recorded rows and exports them
        in the canonical BIRD format:
            { "question_id": "SELECT ...\t----- ----- -----\t<db_id>" }

        Args:
            rows: List of evaluation rows. Defaults to self.rows if None.
            output_path: Destination path for predict_dev.json. Defaults to self.predict_json_path.
            include_db_id: If True, appends the BIRD tab delimiter and db_id.

        Returns:
            Path object of the exported JSON file.
        """
        target_rows = rows if rows is not None else self.rows
        if not target_rows:
            return self.save_predictions_json({}, output_path=output_path)

        # Group rows by question_id to pick the final attempt's generated query
        grouped_by_question: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for r in target_rows:
            grouped_by_question[r["question_id"]].append(r)

        predictions: dict[str, str] = {}
        for q_id, attempts in grouped_by_question.items():
            final_attempt = attempts[-1]
            sql = str(final_attempt.get("agent_sql") or "").strip()
            db_id = str(final_attempt.get("db_id") or "").strip()

            if include_db_id and db_id:
                predictions[str(q_id)] = f"{sql}{self.BIRD_PREDICTION_SEPARATOR}{db_id}"
            else:
                predictions[str(q_id)] = sql

        return self.save_predictions_json(predictions, output_path=output_path)

    def generate_summary(
        self, rows: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """
        Aggregates evaluation results across questions and difficulty tiers.

        Calculates:
        - Total unique questions & total attempts
        - Execution Accuracy (EX):
          * ex_final: % on final candidate SQL
          * ex_pass_at_1: % on first attempt (Pass@1)
          * self_correction_recovery_rate: % of questions initially failing fixed by sandbox error feedback
        - Sandbox execution success rate (%)
        - Token consumption (Total, input, output, averages per question)
        - Latency (Average ms per question and per attempt)
        - Stratification by difficulty ('simple', 'moderate', 'challenging')

        Args:
            rows: List of evaluation rows. Defaults to self.rows if None.

        Returns:
            Dictionary with aggregated metrics.
        """
        target_rows = rows if rows is not None else self.rows

        empty_summary: dict[str, Any] = {
            "total_questions": 0,
            "total_attempts": 0,
            "avg_attempts_per_question": 0.0,
            "ex_final_count": 0,
            "ex_final_rate": 0.0,
            "ex_pass_at_1_count": 0,
            "ex_pass_at_1_rate": 0.0,
            "sandbox_success_count": 0,
            "sandbox_success_rate": 0.0,
            "self_correction_count": 0,
            "self_correction_rate": 0.0,
            "tokens": {
                "total_input": 0,
                "total_output": 0,
                "total_all": 0,
                "avg_input_per_question": 0.0,
                "avg_output_per_question": 0.0,
                "avg_total_per_question": 0.0,
            },
            "latency": {
                "avg_time_ms_per_question": 0.0,
                "avg_time_ms_per_attempt": 0.0,
            },
            "sql_similarity": {
                "avg_similarity_score": 0.0,
            },
            "by_difficulty": {
                "simple": {"count": 0, "ex_count": 0, "ex_rate": 0.0, "sandbox_success_rate": 0.0, "avg_tokens": 0.0, "avg_time_ms": 0.0},
                "moderate": {"count": 0, "ex_count": 0, "ex_rate": 0.0, "sandbox_success_rate": 0.0, "avg_tokens": 0.0, "avg_time_ms": 0.0},
                "challenging": {"count": 0, "ex_count": 0, "ex_rate": 0.0, "sandbox_success_rate": 0.0, "avg_tokens": 0.0, "avg_time_ms": 0.0},
            },
        }

        if not target_rows:
            return empty_summary

        # Group attempts by question_id
        grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for r in target_rows:
            grouped[r["question_id"]].append(r)

        total_questions = len(grouped)
        total_attempts = len(target_rows)

        ex_final_count = 0
        ex_pass_at_1_count = 0
        sandbox_success_count = 0
        self_correction_count = 0
        initially_failed_count = 0

        # Totals for tokens and latency
        total_tokens_input = 0
        total_tokens_output = 0
        total_tokens_all = 0
        all_times_ms: list[float] = []
        all_similarities: list[float] = []

        # Data structure for tier aggregation
        tier_data: dict[str, dict[str, Any]] = {
            "simple": {"count": 0, "ex_count": 0, "sandbox_success": 0, "tokens": 0, "times": []},
            "moderate": {"count": 0, "ex_count": 0, "sandbox_success": 0, "tokens": 0, "times": []},
            "challenging": {"count": 0, "ex_count": 0, "sandbox_success": 0, "tokens": 0, "times": []},
        }

        for q_id, attempts in grouped.items():
            first_attempt = attempts[0]
            final_attempt = attempts[-1]

            # 1. Execution Accuracy checks
            first_match = bool(first_attempt.get("execution_match"))
            final_match = bool(final_attempt.get("execution_match"))

            if first_match:
                ex_pass_at_1_count += 1
            else:
                initially_failed_count += 1
                if final_match:
                    self_correction_count += 1

            if final_match:
                ex_final_count += 1

            # 2. Sandbox execution success check on final attempt
            exec_status = str(final_attempt.get("execution_status") or "").strip().lower()
            exec_error = str(final_attempt.get("error") or "").strip()
            # Deemed successful in sandbox if no execution error and status indicates completion
            sandbox_ok = (not exec_error) and (exec_status in ("exec_ok", "aprovado", "sql_gerada", "") or final_match)
            if sandbox_ok:
                sandbox_success_count += 1

            # 3. Difficulty tier assignment
            raw_diff = str(final_attempt.get("difficulty") or "simple").strip().lower()
            tier_key = raw_diff if raw_diff in tier_data else "simple"

            tier_data[tier_key]["count"] += 1
            if final_match:
                tier_data[tier_key]["ex_count"] += 1
            if sandbox_ok:
                tier_data[tier_key]["sandbox_success"] += 1

            # Sum tokens and times for this entire question across its attempts
            q_tokens_total = sum(int(a.get("tokens_total") or 0) for a in attempts)
            q_time_total = sum(float(a.get("time_ms") or 0.0) for a in attempts)

            tier_data[tier_key]["tokens"] += q_tokens_total
            tier_data[tier_key]["times"].append(q_time_total)

        # Global aggregations over all raw rows
        for r in target_rows:
            in_tok = int(r.get("tokens_input") or 0)
            out_tok = int(r.get("tokens_output") or 0)
            all_tok = int(r.get("tokens_total") or (in_tok + out_tok))

            total_tokens_input += in_tok
            total_tokens_output += out_tok
            total_tokens_all += all_tok

            t_ms = float(r.get("time_ms") or 0.0)
            all_times_ms.append(t_ms)

            sim = float(r.get("similarity_score") or 0.0)
            all_similarities.append(sim)

        # Rates calculation
        ex_final_rate = round((ex_final_count / total_questions) * 100, 2) if total_questions else 0.0
        ex_pass_at_1_rate = round((ex_pass_at_1_count / total_questions) * 100, 2) if total_questions else 0.0
        sandbox_success_rate = round((sandbox_success_count / total_questions) * 100, 2) if total_questions else 0.0
        self_correction_rate = round((self_correction_count / initially_failed_count) * 100, 2) if initially_failed_count else 0.0

        # Tier breakdown formatting
        by_difficulty_summary: dict[str, dict[str, Any]] = {}
        for tier, d in tier_data.items():
            cnt = d["count"]
            t_ex = d["ex_count"]
            t_sb = d["sandbox_success"]
            t_tok = d["tokens"]
            t_times = d["times"]

            by_difficulty_summary[tier] = {
                "count": cnt,
                "ex_count": t_ex,
                "ex_rate": round((t_ex / cnt) * 100, 2) if cnt else 0.0,
                "sandbox_success_rate": round((t_sb / cnt) * 100, 2) if cnt else 0.0,
                "avg_tokens": round(t_tok / cnt, 1) if cnt else 0.0,
                "avg_time_ms": round(sum(t_times) / cnt, 1) if cnt else 0.0,
            }

        return {
            "total_questions": total_questions,
            "total_attempts": total_attempts,
            "avg_attempts_per_question": round(total_attempts / total_questions, 2) if total_questions else 0.0,
            "ex_final_count": ex_final_count,
            "ex_final_rate": ex_final_rate,
            "ex_pass_at_1_count": ex_pass_at_1_count,
            "ex_pass_at_1_rate": ex_pass_at_1_rate,
            "sandbox_success_count": sandbox_success_count,
            "sandbox_success_rate": sandbox_success_rate,
            "self_correction_count": self_correction_count,
            "self_correction_rate": self_correction_rate,
            "tokens": {
                "total_input": total_tokens_input,
                "total_output": total_tokens_output,
                "total_all": total_tokens_all,
                "avg_input_per_question": round(total_tokens_input / total_questions, 1) if total_questions else 0.0,
                "avg_output_per_question": round(total_tokens_output / total_questions, 1) if total_questions else 0.0,
                "avg_total_per_question": round(total_tokens_all / total_questions, 1) if total_questions else 0.0,
            },
            "latency": {
                "avg_time_ms_per_question": round(sum(all_times_ms) / total_questions, 1) if total_questions else 0.0,
                "avg_time_ms_per_attempt": round(sum(all_times_ms) / total_attempts, 1) if total_attempts else 0.0,
            },
            "sql_similarity": {
                "avg_similarity_score": round(sum(all_similarities) / len(all_similarities), 4) if all_similarities else 0.0,
            },
            "by_difficulty": by_difficulty_summary,
        }

    def format_markdown_report(
        self,
        summary: dict[str, Any],
        config_info: dict[str, Any] | None = None,
        detailed_cases: dict[str, list[dict[str, Any]]] | None = None,
    ) -> str:
        """
        Formats a clean, publication-ready Markdown report of evaluation results.

        Args:
            summary: Dictionary returned by generate_summary().
            config_info: Optional dictionary containing evaluation run configuration
                         (e.g., model, dialect, rag_enabled, use_evidence, sample_size, seed).
            detailed_cases: Optional dictionary containing 'failures' and 'self_corrections'
                            cases with query attempts and data row samples.
                            Defaults to self.failures and self.self_corrections if None.

        Returns:
            Rendered Markdown text string.
        """
        config = config_info or {}
        timestamp = config.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        # Format configuration table
        config_rows = [
            f"| **Timestamp** | {timestamp} |",
            f"| **Model** | `{config.get('model', 'N/A')}` |",
            f"| **Dialect** | `{config.get('dialect', 'sqlite')}` |",
            f"| **Sample Size** | {config.get('sample_size', 'All')} |",
            f"| **Random Seed** | {config.get('seed', 'None')} |",
            f"| **Schema RAG** | `{'Enabled' if config.get('use_rag') else 'Disabled'}` |",
            f"| **Evidence / Hints** | `{'Enabled' if config.get('use_evidence', True) else 'Disabled'}` |",
        ]
        if config.get("db_filter"):
            config_rows.append(f"| **Database Filter** | `{config['db_filter']}` |")
        if config.get("difficulty_filter"):
            config_rows.append(f"| **Difficulty Filter** | `{config['difficulty_filter']}` |")

        config_table = "\n".join(config_rows)

        # Difficulty breakdown rows
        by_diff = summary.get("by_difficulty", {})
        diff_rows = []
        for tier in ["simple", "moderate", "challenging"]:
            d = by_diff.get(tier, {})
            cnt = d.get("count", 0)
            ex_cnt = d.get("ex_count", 0)
            ex_rate = d.get("ex_rate", 0.0)
            sb_rate = d.get("sandbox_success_rate", 0.0)
            avg_tok = d.get("avg_tokens", 0.0)
            avg_ms = d.get("avg_time_ms", 0.0)
            diff_rows.append(
                f"| **{tier.capitalize()}** | {cnt} | {ex_cnt} | **{ex_rate:.1f}%** | {sb_rate:.1f}% | {avg_tok:,.0f} | {avg_ms:,.0f} ms |"
            )
        diff_table = "\n".join(diff_rows)

        tok = summary.get("tokens", {})
        lat = summary.get("latency", {})

        report_sections = [
            f"""# BIRD Evaluation Report

## 1. Test Configuration
| Parameter | Value |
|---|---|
{config_table}

---

## 2. Executive Summary
| Metric | Value |
|---|---|
| **Total Questions Evaluated** | **{summary.get('total_questions', 0)}** |
| **Execution Accuracy (EX Final)** | **{summary.get('ex_final_rate', 0.0):.2f}%** ({summary.get('ex_final_count', 0)}/{summary.get('total_questions', 0)}) |
| **Pass@1 (First Attempt EX)** | **{summary.get('ex_pass_at_1_rate', 0.0):.2f}%** ({summary.get('ex_pass_at_1_count', 0)}/{summary.get('total_questions', 0)}) |
| **Sandbox Execution Success Rate** | **{summary.get('sandbox_success_rate', 0.0):.2f}%** ({summary.get('sandbox_success_count', 0)}/{summary.get('total_questions', 0)}) |
| **Self-Correction Recovery Rate** | **{summary.get('self_correction_rate', 0.0):.2f}%** ({summary.get('self_correction_count', 0)} corrected) |
| **Average Attempts per Question** | {summary.get('avg_attempts_per_question', 0.0):.2f} |
| **Average SQL Text Similarity** | {summary.get('sql_similarity', {}).get('avg_similarity_score', 0.0):.4f} |

---

## 3. Results Stratified by Difficulty
| Difficulty | Questions | EX Matches | EX Rate (%) | Sandbox Success (%) | Avg Tokens / Q | Avg Latency |
|---|---|---|---|---|---|---|
{diff_table}

---

## 4. Token & Latency Resource Consumption
| Resource Metric | Input | Output | Total |
|---|---|---|---|
| **Total Tokens** | {tok.get('total_input', 0):,} | {tok.get('total_output', 0):,} | **{tok.get('total_all', 0):,}** |
| **Average Tokens per Question** | {tok.get('avg_input_per_question', 0.0):,.1f} | {tok.get('avg_output_per_question', 0.0):,.1f} | **{tok.get('avg_total_per_question', 0.0):,.1f}** |
| **Average Latency per Question** | - | - | **{lat.get('avg_time_ms_per_question', 0.0):,.1f} ms** |
| **Average Latency per Attempt** | - | - | **{lat.get('avg_time_ms_per_attempt', 0.0):,.1f} ms** |"""
        ]

        # Resolve detailed inspection cases
        cases = detailed_cases if detailed_cases is not None else {}
        self_corrections = cases.get("self_corrections", self.self_corrections)
        failures = cases.get("failures", self.failures)

        # Section 5: Successful Self-Correction Cases
        if self_corrections:
            sc_blocks = [
                "---",
                "",
                "## 5. Successful Self-Correction Cases",
                "",
                "Questions that failed on the initial attempt (sandbox execution error or result mismatch) and were successfully recovered by the agent:",
                "",
            ]
            for sc in self_corrections:
                qid = sc.get("question_id", "N/A")
                db = sc.get("db_id", "N/A")
                diff = str(sc.get("difficulty", "N/A")).capitalize()
                question = sc.get("question", "")
                evidence = sc.get("evidence", "")
                gold_sql = sc.get("gold_sql", "")
                final_sql = sc.get("final_sql", sc.get("agent_sql", ""))
                attempts = sc.get("attempts", [])
                gold_results = sc.get("gold_results")
                gold_cols = sc.get("gold_columns")

                sc_blocks.append(f"### Question {qid} (`{db}` - {diff})")
                sc_blocks.append("")
                sc_blocks.append(f"**Question:** {question}")
                if evidence:
                    sc_blocks.append(f"> **Evidence / Hint:** {evidence}")
                sc_blocks.append("")
                sc_blocks.append("**Gold SQL:**")
                sc_blocks.append(f"```sql\n{gold_sql}\n```")
                sc_blocks.append("")
                sc_blocks.append("#### Attempt Progression:")
                for att_idx, att in enumerate(attempts, 1):
                    att_sql = att.get("sql", "").strip() or "(empty)"
                    att_err = att.get("erro", "").strip()
                    is_final = (att_idx == len(attempts))
                    status_badge = "✅ **Success (EX Match)**" if is_final else ("❌ **Error:** `" + att_err + "`" if att_err else "⚠️ **Subsequent iteration**")
                    sc_blocks.append(f"**Attempt {att_idx}** ({status_badge}):")
                    sc_blocks.append(f"```sql\n{att_sql}\n```")
                    sc_blocks.append("")

                sc_blocks.append("**Verified Result Sample (Gold/Final - top 10 rows):**")
                sc_blocks.append(format_result_rows_markdown_table(gold_results, column_names=gold_cols, max_rows=10))
                sc_blocks.append("")
                sc_blocks.append("---")
                sc_blocks.append("")
            report_sections.append("\n".join(sc_blocks))

        # Section 6: Detailed Failure Analysis (Execution Mismatches & Errors)
        if failures:
            fail_blocks = [
                "---",
                "",
                "## 6. Detailed Failure Analysis (Execution Mismatches & Errors)",
                "",
                "Detailed diagnostics for questions where the Text-to-Insight result differed from ground truth or encountered execution errors:",
                "",
            ]
            for f_case in failures:
                qid = f_case.get("question_id", "N/A")
                db = f_case.get("db_id", "N/A")
                diff = str(f_case.get("difficulty", "N/A")).capitalize()
                question = f_case.get("question", "")
                evidence = f_case.get("evidence", "")
                gold_sql = f_case.get("gold_sql", "")
                agent_sql = f_case.get("agent_sql", "")
                agent_err = f_case.get("error", "")
                attempts = f_case.get("attempts", [])
                gold_results = f_case.get("gold_results")
                gold_cols = f_case.get("gold_columns")
                agent_results = f_case.get("agent_results")
                agent_cols = f_case.get("agent_columns")

                fail_blocks.append(f"### Question {qid} (`{db}` - {diff})")
                fail_blocks.append("")
                fail_blocks.append(f"**Question:** {question}")
                if evidence:
                    fail_blocks.append(f"> **Evidence / Hint:** {evidence}")
                fail_blocks.append("")
                fail_blocks.append("**Gold SQL:**")
                fail_blocks.append(f"```sql\n{gold_sql}\n```")
                fail_blocks.append("")
                fail_blocks.append("**Gold Result** (top 10 rows):")
                fail_blocks.append(format_result_rows_markdown_table(gold_results, column_names=gold_cols, max_rows=10))
                fail_blocks.append("")

                if attempts and len(attempts) > 1:
                    fail_blocks.append("#### Attempt History:")
                    for att_idx, att in enumerate(attempts, 1):
                        att_sql = att.get("sql", "").strip() or "(empty)"
                        att_err = att.get("erro", "").strip()
                        badge = f"❌ Error: `{att_err}`" if att_err else "Executed"
                        fail_blocks.append(f"**Attempt {att_idx}** ({badge}):")
                        fail_blocks.append(f"```sql\n{att_sql}\n```")
                        fail_blocks.append("")

                fail_blocks.append("**Agent Candidate SQL (Final):**")
                fail_blocks.append(f"```sql\n{agent_sql or '(no query generated)'}\n```")
                fail_blocks.append("")

                if agent_err:
                    fail_blocks.append(f"❌ **SQLite Execution Error:** `{agent_err}`")
                else:
                    fail_blocks.append("**Agent Result (Final)** (top 10 rows):")
                    fail_blocks.append(format_result_rows_markdown_table(agent_results, column_names=agent_cols, max_rows=10))

                fail_blocks.append("")
                fail_blocks.append("---")
                fail_blocks.append("")
            report_sections.append("\n".join(fail_blocks))

        report_sections.append("---\n*Report generated automatically by `BirdCSVReporter` (TextToInsight).*")
        return "\n\n".join(report_sections)

    def save_markdown_report(
        self,
        summary: dict[str, Any],
        config_info: dict[str, Any] | None = None,
        filepath: str | Path | None = None,
        detailed_cases: dict[str, list[dict[str, Any]]] | None = None,
    ) -> Path:
        """
        Renders and writes the Markdown evaluation report to disk.

        Args:
            summary: Dictionary returned by generate_summary().
            config_info: Optional configuration metadata dictionary.
            filepath: Destination path for the Markdown report.
                      Defaults to self.filepath.with_suffix(".md").
            detailed_cases: Optional dictionary containing 'failures' and 'self_corrections'
                            cases with query attempts and data row samples.

        Returns:
            Path object of the written file.
        """
        target_path = Path(filepath) if filepath else self.filepath.with_suffix(".md")
        target_path.parent.mkdir(parents=True, exist_ok=True)

        content = self.format_markdown_report(
            summary,
            config_info=config_info,
            detailed_cases=detailed_cases,
        )
        target_path.write_text(content, encoding="utf-8")
        return target_path

    @staticmethod
    def generate_timestamped_filename(prefix: str = "bird_eval", extension: str = "csv") -> str:
        """
        Generates a standardized timestamped report filename.
        Example: bird_eval_2026-09-27_23-15-00.csv
        """
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        ext = extension.lstrip(".")
        return f"{prefix}_{timestamp}.{ext}"

    @staticmethod
    def generate_timestamped_run_dir(prefix: str = "run") -> str:
        """
        Generates a standardized timestamped run folder name.
        Example: run_2026-09-27_23-15-00
        """
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return f"{prefix}_{timestamp}"
