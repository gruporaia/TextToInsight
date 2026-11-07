"""
src/spider/csv_reporter.py

CSV reporter and statistical summary generator for Spider 2.0 evaluation results.

Provides:
- CSV initialization and row persistence.
- Per-attempt and per-question metric tracking.
- Node-level token usage aggregation (tokens_by_node).
- Timestamped directory and file name generation.
"""

from __future__ import annotations

from collections import defaultdict
import csv
from datetime import datetime
import json
from pathlib import Path
from typing import Any


class CSVReporter:
    """Manages CSV file output and statistical aggregation for Spider evaluation runs."""

    HEADERS = [
        "id_exemplo",
        "tentativa_numero",
        "db_id",
        "pergunta_usuario",
        "query_ouro_spider",
        "query_agente_tentativa",
        "tempo_agente_ms",
        "veredito_critico",
        "feedback_critico_recebido",
        "erro_execucao",
        "resultado_exato_match",
        "similarity_score_sql",
        "resultado_f1",
        "resultado_precision",
        "resultado_recall",
        # Empirical analysis columns
        "tokens_input",
        "tokens_output",
        "tokens_total",
        "viz_acionado",
        "viz_sucesso",
        "resultado_exato_match_1a_tentativa",
        "resultado_f1_1a_tentativa",
        "query_1a_tentativa",
        "tokens_by_node",
    ]

    def __init__(self, filepath: str | Path) -> None:
        """
        Initializes the CSV reporter.

        Parameters:
            filepath: Destination path for the CSV report file.
        """
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)

        with open(self.filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.HEADERS)
            writer.writeheader()

    def append_row(self, row: dict[str, Any]) -> None:
        """
        Appends an evaluation row to the CSV file.

        Parameters:
            row: Dictionary matching HEADERS keys.

        Raises:
            ValueError: If required keys are missing.
        """
        missing = set(self.HEADERS) - set(row.keys())
        if missing:
            raise ValueError(f"Missing required CSV keys: {missing}")

        with open(self.filepath, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.HEADERS)
            writer.writerow(row)

    def generate_summary(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Generates an aggregated statistical summary of the evaluation results,
        including accuracy metrics and node-level token consumption breakdown.

        Parameters:
            rows: List of CSV evaluation rows.

        Returns:
            Dictionary containing summary statistics and node token metrics.
        """
        canonical_nodes = [
            "planner",
            "rag_enrichment",
            "exploration_selector",
            "code_agent",
            "graph_router",
            "graph_generator",
            "response",
        ]

        empty_node_tokens = {
            node: {
                "calls": 0,
                "total_input": 0,
                "total_output": 0,
                "total_all": 0,
                "avg_input_per_question": 0.0,
                "avg_output_per_question": 0.0,
                "avg_total_per_question": 0.0,
                "pct_of_total_tokens": 0.0,
            }
            for node in canonical_nodes
        }

        if not rows:
            return {
                "total_perguntas": 0,
                "total_questions": 0,
                "total_tentativas": 0,
                "total_attempts": 0,
                "perguntas_aprovadas": 0,
                "ex_final_count": 0,
                "taxa_aprovacao": 0.0,
                "ex_final_rate": 0.0,
                "perguntas_1a_tentativa": 0,
                "ex_pass_at_1_count": 0,
                "taxa_1a_tentativa": 0.0,
                "ex_pass_at_1_rate": 0.0,
                "tentativas_media": 0.0,
                "avg_attempts_per_question": 0.0,
                "similarity_media": 0.0,
                "tempo_medio_ms": 0.0,
                "avg_precision": 0.0,
                "avg_recall": 0.0,
                "avg_f1": 0.0,
                "tokens": {
                    "total_input": 0,
                    "total_output": 0,
                    "total_all": 0,
                    "avg_input_per_question": 0.0,
                    "avg_output_per_question": 0.0,
                    "avg_total_per_question": 0.0,
                },
                "node_tokens": empty_node_tokens,
            }

        # Group attempts by question ID (id_exemplo)
        by_exemplo: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            ex_id = row["id_exemplo"]
            by_exemplo[ex_id].append(row)

        total_perguntas = len(by_exemplo)
        perguntas_aprovadas = 0
        perguntas_1a_tentativa = 0
        total_tentativas = 0
        similarities: list[float] = []
        tempos: list[float] = []
        precisions: list[float] = []
        recalls: list[float] = []
        f1s: list[float] = []

        total_tokens_input = 0
        total_tokens_output = 0
        total_tokens_all = 0

        # Node token aggregation per node across all questions
        node_totals: dict[str, dict[str, int]] = defaultdict(
            lambda: {"calls": 0, "total_input": 0, "total_output": 0, "total_all": 0}
        )

        for ex_id, tentativas in by_exemplo.items():
            tentativas_sorted = sorted(
                tentativas,
                key=lambda t: int(t.get("tentativa_numero", 1) or 1)
            )
            primeira = tentativas_sorted[0]
            ultima = tentativas_sorted[-1]

            try:
                qtd_tentativas = int(ultima.get("tentativa_numero", len(tentativas_sorted)))
            except (ValueError, TypeError):
                qtd_tentativas = len(tentativas_sorted)

            total_tentativas += qtd_tentativas

            # Check execution match (either exact match True or critic approved)
            match_final = ultima.get("resultado_exato_match") is True or ultima.get("veredito_critico") == "aprovado"
            if match_final:
                perguntas_aprovadas += 1

            match_1a = primeira.get("resultado_exato_match_1a_tentativa") is True or (
                qtd_tentativas == 1 and match_final
            )
            if match_1a:
                perguntas_1a_tentativa += 1

            # Precision, recall, F1 on final attempt
            try:
                precisions.append(float(ultima.get("resultado_precision", 0.0) or 0.0))
                recalls.append(float(ultima.get("resultado_recall", 0.0) or 0.0))
                f1s.append(float(ultima.get("resultado_f1", 0.0) or 0.0))
            except (ValueError, TypeError):
                pass

            # Similarity & latency across attempts
            for tent in tentativas_sorted:
                sim_val = tent.get("similarity_score_sql")
                if sim_val not in (None, ""):
                    try:
                        similarities.append(float(sim_val))
                    except (ValueError, TypeError):
                        pass

                t_ms = tent.get("tempo_agente_ms")
                if t_ms not in (None, ""):
                    try:
                        tempos.append(float(t_ms))
                    except (ValueError, TypeError):
                        pass

            # Question token usage from final attempt (cumulative state)
            q_in = int(ultima.get("tokens_input", 0) or 0)
            q_out = int(ultima.get("tokens_output", 0) or 0)
            q_tot = int(ultima.get("tokens_total", 0) or (q_in + q_out))
            total_tokens_input += q_in
            total_tokens_output += q_out
            total_tokens_all += q_tot

            # Parse question node-level tokens
            q_node_dict: dict[str, dict[str, int]] = defaultdict(
                lambda: {"input": 0, "output": 0, "total": 0, "calls": 0}
            )

            last_tb_raw = ultima.get("tokens_by_node")
            if last_tb_raw:
                try:
                    parsed = json.loads(last_tb_raw) if isinstance(last_tb_raw, str) else last_tb_raw
                    if isinstance(parsed, dict) and any(
                        v.get("calls", 0) > 0 or v.get("total", 0) > 0
                        for v in parsed.values()
                        if isinstance(v, dict)
                    ):
                        for node, usage in parsed.items():
                            if isinstance(usage, dict):
                                q_node_dict[node]["input"] = usage.get("input", 0)
                                q_node_dict[node]["output"] = usage.get("output", 0)
                                q_node_dict[node]["total"] = usage.get("total", 0)
                                q_node_dict[node]["calls"] = usage.get("calls", 0)
                except Exception:
                    pass

            # If last attempt was empty, check all attempts
            if not any(v["calls"] > 0 or v["total"] > 0 for v in q_node_dict.values()):
                for t in tentativas_sorted:
                    tb_raw = t.get("tokens_by_node")
                    if tb_raw:
                        try:
                            parsed = json.loads(tb_raw) if isinstance(tb_raw, str) else tb_raw
                            if isinstance(parsed, dict):
                                for node, usage in parsed.items():
                                    if isinstance(usage, dict):
                                        q_node_dict[node]["input"] += usage.get("input", 0)
                                        q_node_dict[node]["output"] += usage.get("output", 0)
                                        q_node_dict[node]["total"] += usage.get("total", 0)
                                        q_node_dict[node]["calls"] += usage.get("calls", 0)
                        except Exception:
                            pass

            for node, usage in q_node_dict.items():
                node_totals[node]["calls"] += usage["calls"]
                node_totals[node]["total_input"] += usage["input"]
                node_totals[node]["total_output"] += usage["output"]
                node_totals[node]["total_all"] += usage["total"]

        # Calculate node token summary metrics
        grand_total_tokens = sum(n["total_all"] for n in node_totals.values()) or total_tokens_all
        node_tokens_summary: dict[str, dict[str, Any]] = {}
        all_node_names = list(canonical_nodes) + [n for n in node_totals if n not in canonical_nodes]

        for node in all_node_names:
            d = node_totals.get(node, {"calls": 0, "total_input": 0, "total_output": 0, "total_all": 0})
            calls = d["calls"]
            t_in = d["total_input"]
            t_out = d["total_output"]
            t_all = d["total_all"]
            avg_in = t_in / total_perguntas if total_perguntas else 0.0
            avg_out = t_out / total_perguntas if total_perguntas else 0.0
            avg_tot = t_all / total_perguntas if total_perguntas else 0.0
            pct = (t_all / grand_total_tokens * 100.0) if grand_total_tokens else 0.0

            node_tokens_summary[node] = {
                "calls": calls,
                "total_input": t_in,
                "total_output": t_out,
                "total_all": t_all,
                "avg_input_per_question": round(avg_in, 2),
                "avg_output_per_question": round(avg_out, 2),
                "avg_total_per_question": round(avg_tot, 2),
                "pct_of_total_tokens": round(pct, 2),
            }

        taxa_aprov = (perguntas_aprovadas / total_perguntas) if total_perguntas > 0 else 0.0
        taxa_1a = (perguntas_1a_tentativa / total_perguntas) if total_perguntas > 0 else 0.0

        return {
            "total_perguntas": total_perguntas,
            "total_questions": total_perguntas,
            "total_tentativas": total_tentativas,
            "total_attempts": total_tentativas,
            "perguntas_aprovadas": perguntas_aprovadas,
            "ex_final_count": perguntas_aprovadas,
            "taxa_aprovacao": taxa_aprov,
            "ex_final_rate": round(taxa_aprov * 100.0, 2),
            "perguntas_1a_tentativa": perguntas_1a_tentativa,
            "ex_pass_at_1_count": perguntas_1a_tentativa,
            "taxa_1a_tentativa": taxa_1a,
            "ex_pass_at_1_rate": round(taxa_1a * 100.0, 2),
            "tentativas_media": (total_tentativas / total_perguntas) if total_perguntas > 0 else 0.0,
            "avg_attempts_per_question": (total_tentativas / total_perguntas) if total_perguntas > 0 else 0.0,
            "similarity_media": (sum(similarities) / len(similarities)) if similarities else 0.0,
            "tempo_medio_ms": (sum(tempos) / len(tempos)) if tempos else 0.0,
            "avg_precision": (sum(precisions) / len(precisions)) if precisions else 0.0,
            "avg_recall": (sum(recalls) / len(recalls)) if recalls else 0.0,
            "avg_f1": (sum(f1s) / len(f1s)) if f1s else 0.0,
            "tokens": {
                "total_input": total_tokens_input,
                "total_output": total_tokens_output,
                "total_all": total_tokens_all,
                "avg_input_per_question": round(total_tokens_input / total_perguntas, 2) if total_perguntas else 0.0,
                "avg_output_per_question": round(total_tokens_output / total_perguntas, 2) if total_perguntas else 0.0,
                "avg_total_per_question": round(total_tokens_all / total_perguntas, 2) if total_perguntas else 0.0,
            },
            "node_tokens": node_tokens_summary,
        }

    @staticmethod
    def generate_timestamped_filename(prefix: str = "spider2_eval", extension: str = "csv") -> str:
        """
        Generates a standardized timestamped report filename.
        Example: spider2_eval_2026-11-03_14-30-00.csv
        """
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        ext = extension.lstrip(".")
        return f"{prefix}_{timestamp}.{ext}"

    @staticmethod
    def generate_timestamped_run_dir(prefix: str = "run") -> str:
        """
        Generates a standardized timestamped run folder name.
        Example: run_2026-11-03_14-30-00
        """
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return f"{prefix}_{timestamp}"
