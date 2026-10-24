"""
src/bird/logger.py

Execution logger for BIRD Benchmark evaluations.

Responsibilities:
1. BirdExecutionLogger:
   - Persists detailed, per-query evaluation traces into a human-readable .log file.
   - Provides a Tee-based context manager to intercept and capture runtime prints
     ([SCHEMA], [RETRIEVER], [PLANEJADOR], [DATA_EXPLORATION], [EXECUTOR], [CRITICO])
     while simultaneously printing to the terminal.
   - Records full prompt text, CoT thoughts (<thought>), generated SQL, sandbox errors,
     intermediate self-correction attempts, gold comparisons, latency, and tokens.
   - Saves the log in the exact same output directory as the run's CSV, Markdown, and JSON.
"""

from __future__ import annotations

import contextlib
from datetime import datetime
import io
from pathlib import Path
import sys
from typing import Any


class _TeeStream:
    """Helper stream that writes to both the original stdout and an in-memory buffer."""

    def __init__(self, original_stream: Any, capture_buffer: io.StringIO) -> None:
        self._original = original_stream
        self._buffer = capture_buffer

    def write(self, s: str) -> int:
        self._buffer.write(s)
        return self._original.write(s)

    def flush(self) -> None:
        self._buffer.flush()
        self._original.flush()

    def isatty(self) -> bool:
        return getattr(self._original, "isatty", lambda: False)()


class BirdExecutionLogger:
    """Manages creation and writing of detailed BIRD benchmark execution logs."""

    def __init__(
        self,
        output_dir: Path | str,
        timestamp: str | None = None,
        enabled: bool = True,
        log_file: Path | str | None = None,
    ) -> None:
        self.enabled = enabled
        self.output_dir = Path(output_dir)
        self.timestamp = timestamp or datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        if log_file:
            self.log_path = Path(log_file)
        else:
            self.log_path = self.output_dir / f"bird_eval_{self.timestamp}.log"

        self._file_handle = None
        if self.enabled:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._file_handle = open(self.log_path, "w", encoding="utf-8")

    def __enter__(self) -> "BirdExecutionLogger":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def close(self) -> None:
        """Closes the underlying log file handle."""
        if self._file_handle and not self._file_handle.closed:
            self._file_handle.flush()
            self._file_handle.close()
            self._file_handle = None

    def _write(self, text: str) -> None:
        """Writes text directly to the log file and flushes."""
        if not self.enabled or not self._file_handle or self._file_handle.closed:
            return
        self._file_handle.write(text)
        self._file_handle.flush()

    @contextlib.contextmanager
    def capture_trace(self):
        """
        Context manager that intercepts sys.stdout using a Tee stream,
        recording all console output during the enclosed block into a buffer
        without suppressing live terminal display.
        """
        if not self.enabled:
            yield lambda: ""
            return

        buffer = io.StringIO()
        old_stdout = sys.stdout
        tee = _TeeStream(old_stdout, buffer)
        sys.stdout = tee  # type: ignore[assignment]
        try:
            yield lambda: buffer.getvalue()
        finally:
            sys.stdout = old_stdout

    def log_run_header(self, config_info: dict[str, Any]) -> None:
        """Writes the initial configuration header of the evaluation run."""
        if not self.enabled:
            return

        ts = config_info.get("timestamp", self.timestamp)
        model = config_info.get("model", "unknown")
        dialect = config_info.get("dialect", "sqlite")
        sample_size = config_info.get("sample_size", "all")
        seed = config_info.get("seed", 42)
        use_rag = "ON" if config_info.get("use_rag") else "OFF"
        enrich_rag = "ON" if config_info.get("enrich_rag") else "OFF"
        use_evidence = "ON" if config_info.get("use_evidence") else "OFF"
        use_cot = "ON" if config_info.get("use_cot") else "OFF"
        use_exploration = "ON" if config_info.get("use_data_exploration") else "OFF"
        exploration_selector = config_info.get("exploration_selector") or "off"
        schemacrawler = "ON" if config_info.get("schemacrawler") else "OFF"
        verificador = config_info.get("verificador") or "off"
        infer_fks = "ON" if config_info.get("infer_fks") else "OFF"
        model_kwargs = config_info.get("model_kwargs") or {}
        kwargs_str = f" | ModelKwargs: {model_kwargs}" if model_kwargs else ""
        db_filter = config_info.get("db_filter") or "None"
        diff_filter = config_info.get("difficulty_filter") or "None"

        sep = "=" * 80
        header_lines = [
            sep,
            "TEXTTOINSIGHT - BIRD BENCHMARK EXECUTION LOG",
            sep,
            f"Timestamp:          {ts}",
            f"Model:              {model}",
            f"Dialect:            {dialect}",
            f"Sample Size:        {sample_size}",
            f"Seed:               {seed}",
            f"Configuration:      RAG: {use_rag} | EnrichRAG: {enrich_rag} | Evidence: {use_evidence} | CoT: {use_cot} | "
            f"DataExploration: {use_exploration} (Selector: {exploration_selector}) | "
            f"SchemaCrawler: {schemacrawler} | InferFKs: {infer_fks} | Verificador: {verificador}{kwargs_str}",
            f"Filters:            DB: {db_filter} | Difficulty: {diff_filter}",
            f"Output Directory:   {self.output_dir}",
            f"Log File:           {self.log_path.name}",
            sep,
            "",
            "",
        ]
        self._write("\n".join(header_lines))

    def log_query(
        self,
        question_id: int | str,
        db_id: str,
        difficulty: str,
        question: str,
        evidence: str,
        gold_sql: str,
        agent_result: dict[str, Any],
        match_success: bool,
        similarity_score: float,
        time_ms: float,
        gold_results: list[Any] | None = None,
        agent_results: list[Any] | None = None,
        captured_trace: str = "",
        precision: float = 0.0,
        recall: float = 0.0,
        f1: float = 0.0,
    ) -> None:
        """Writes a detailed, structured execution block for a single benchmark query."""
        if not self.enabled:
            return

        lines: list[str] = []
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. Block Header
        lines.append(f"=== [START QID: {question_id} | DB: {db_id} | Difficulty: {difficulty}] ===")
        lines.append(f"[TIMESTAMP] {now_str}")
        lines.append(f"[QUESTION] {question}")
        lines.append(f"[EVIDENCE / HINT] {evidence or '(none)'}")
        lines.append("")

        # 2. Schema / RAG Context
        schema_rag = agent_result.get("contexto_rag_schema", "").strip()
        schema_full = agent_result.get("contexto_schema", "").strip()
        data_expl = agent_result.get("contexto_data_exploration", "").strip()

        lines.append("--- [SCHEMA / RAG CONTEXT] ---")
        if schema_rag:
            lines.append("Schema recuperado pelo RAG:")
            lines.append(schema_rag)
        elif schema_full:
            lines.append("Schema completo do banco:")
            lines.append(schema_full[:2000] + ("\n... (truncated schema)" if len(schema_full) > 2000 else ""))
        else:
            lines.append("(Nenhum schema registrado no estado)")

        if data_expl:
            lines.append("\nEstatísticas de Exploração de Dados:")
            lines.append(data_expl)
        lines.append("")

        # 3. Execution Trace (Tee captured terminal prints)
        if captured_trace and captured_trace.strip():
            lines.append("--- [EXECUTION TRACE / FLUXO DE NÓS (TEE)] ---")
            lines.append(captured_trace.strip())
            lines.append("")

        # 4. Attempts & Model Output
        history = agent_result.get("historico_tentativas") or []
        final_sql = str(agent_result.get("sql_gerada") or "").strip()
        final_error = str(agent_result.get("erro_execucao") or "").strip()
        final_thought = str(agent_result.get("raciocinio_agente") or "").strip()
        final_prompt = str(agent_result.get("ultimo_prompt") or "").strip()
        final_status = str(agent_result.get("status") or "").strip()

        if len(history) > 1:
            lines.append(f"--- [ATTEMPTS ({len(history)} tentativas)] ---")
            for att_idx, att in enumerate(history, 1):
                att_sql = str(att.get("sql") or "").strip()
                att_err = str(att.get("erro") or "").strip()
                att_prompt = str(att.get("prompt") or "").strip()
                att_thought = str(att.get("raciocinio") or "").strip()

                lines.append(f">> Tentativa #{att_idx}:")
                if att_prompt:
                    lines.append(f"  [Prompt Enviado]:\n{att_prompt}\n")
                if att_thought:
                    lines.append(f"  [Raciocínio CoT / Thought]:\n{att_thought}\n")
                lines.append(f"  [SQL Gerada]: {att_sql or '(vazio)'}")
                lines.append(f"  [Status / Erro]: {att_err or 'EXEC_OK'}")
                lines.append("")
        else:
            lines.append("--- [ATTEMPT 1/1] ---")
            if final_prompt:
                lines.append("[PROMPT COMPLETO ENVIADO AO LLM]:")
                lines.append(final_prompt)
                lines.append("")

            if final_thought:
                lines.append("[RACIOCÍNIO DO AGENTE (CoT / THOUGHT)]:")
                lines.append(final_thought)
                lines.append("")

            lines.append(f"[SQL GERADA]:\n{final_sql or '(nenhuma SQL gerada)'}")
            lines.append(f"[STATUS SANDBOX]: {final_status}")
            lines.append(f"[ERRO SANDBOX]: {final_error or '(nenhum)'}")

            preview = agent_result.get("linhas_resultado_preview") or []
            if preview:
                lines.append(f"[LINHAS RESULTADO PREVIEW ({len(preview)})]:")
                lines.append(str(preview[:5]))
            lines.append("")

        # 5. Evaluation & Gold Comparison
        lines.append("--- [EVALUATION & GOLD COMPARISON] ---")
        lines.append(f"[GOLD SQL]:\n{gold_sql.strip()}")
        gold_count = len(gold_results) if gold_results is not None else "N/A"
        agent_count = len(agent_results) if agent_results is not None else "N/A"
        lines.append(f"[EXECUTION ROWS]: Gold: {gold_count} | Agent: {agent_count}")

        match_icon = "✅ TRUE" if match_success else "❌ FALSE"
        lines.append(f"[EX MATCH (Execution Accuracy)]: {match_icon}")
        lines.append(
            f"[RESULT METRICS (Column-Level)]: Recall: {recall:.4f} | Precision: {precision:.4f} | F1: {f1:.4f}"
        )
        lines.append(f"[SQL SIMILARITY]: {similarity_score:.4f}")
        lines.append(f"[LATÊNCIA AGENTE]: {time_ms:.0f} ms")

        tokens_in = int(agent_result.get("tokens_input") or 0)
        tokens_out = int(agent_result.get("tokens_output") or 0)
        tokens_tot = int(agent_result.get("tokens_total") or (tokens_in + tokens_out))
        lines.append(f"[TOKENS]: Input: {tokens_in:,} | Output: {tokens_out:,} | Total: {tokens_tot:,}")
        alertas = agent_result.get("alertas_verificador") or []
        lines.append("[ALERTAS DO VERIFICADOR]: " + (" | ".join(a.get("mensagem", "") for a in alertas) if alertas else "(nenhum)"))
        verif = agent_result.get("verificacao") or {}
        if verif:
            probs = " / ".join(f"{k} {v:.0%}" for k, v in (verif.get("probs_jev") or {}).items())
            lines.append(f"[VERIFICADOR]: alerta {'sim' if verif.get('alerta') else 'não'} | nível: {verif.get('nivel') or '-'}"
                         f" | jev: {verif.get('nivel_jev') or 'sem Jev'}" + (f" ({probs})" if probs else "")
                         + f" | regras: {', '.join(verif.get('niveis_regras') or []) or '-'} | origem: {verif.get('origem') or '-'}")

        # 6. Block Footer
        lines.append(f"=== [END QID: {question_id}] ===\n\n")

        block_text = "\n".join(lines)
        self._write(block_text)

    def log_run_footer(self, summary: dict[str, Any], total_elapsed: float) -> None:
        """Writes the final summary section at the bottom of the log file."""
        if not self.enabled:
            return

        total_q = summary.get("total_questions", 0)
        final_ex = summary.get("ex_final_count", summary.get("execution_match_final", 0))
        final_pct = summary.get("ex_final_rate", summary.get("execution_match_final_pct", 0.0))
        pass1_ex = summary.get("ex_pass_at_1_count", summary.get("pass1_execution_match", 0))
        pass1_pct = summary.get("ex_pass_at_1_rate", summary.get("pass1_pct", 0.0))
        self_corr_pct = summary.get("self_correction_rate", summary.get("self_correction_success_pct", 0.0))

        avg_rec = summary.get("avg_recall", 0.0)
        avg_prec = summary.get("avg_precision", 0.0)
        avg_f1 = summary.get("avg_f1", 0.0)

        tokens_dict = summary.get("tokens") if isinstance(summary.get("tokens"), dict) else {}
        tok_tot = tokens_dict.get("total_all", summary.get("tokens_total", 0))
        tok_in = tokens_dict.get("total_input", summary.get("tokens_input", 0))
        tok_out = tokens_dict.get("total_output", summary.get("tokens_output", 0))

        sep = "=" * 80
        footer_lines = [
            sep,
            "EVALUATION RUN SUMMARY",
            sep,
            f"Total Questions Evaluated:          {total_q}",
            f"Execution Accuracy (Final EX):      {final_ex}/{total_q} ({final_pct:.2f}%)",
            f"Pass@1 EX Accuracy:                 {pass1_ex}/{total_q} ({pass1_pct:.2f}%)",
            f"Average Result Recall (Final):      {avg_rec * 100:.2f}% ({avg_rec:.4f})",
            f"Average Result Precision (Final):   {avg_prec * 100:.2f}% ({avg_prec:.4f})",
            f"Average Result F1 Score (Final):    {avg_f1 * 100:.2f}% ({avg_f1:.4f})",
            f"Self-Correction Recovery Rate:      {self_corr_pct:.2f}%",
            f"Total Tokens Consumed:              {tok_tot:,} (Input: {tok_in:,} | Output: {tok_out:,})",
            f"Total Elapsed Time:                 {total_elapsed:.1f} seconds",
            sep,
            "",
        ]
        self._write("\n".join(footer_lines))
