#!/usr/bin/env python3
"""
scripts/test_bird_eval.py

CLI Orchestrator for BIRD Benchmark Evaluation.

Integrates:
- src.bird.data_loader: Dataset loading, filtering, and sampling.
- src.bird.query_executor: Safe SQLite execution with math functions and timeouts.
- src.bird.metrics: Execution Accuracy (EX), SQL similarity, canonical row formatting.
- src.bird.csv_reporter: CSV persistence, Markdown report generation, and BIRD predict_dev.json export.
- text_to_insight.InsightEngine: The TextToInsight multi-agent graph (Planner + Code Agent + Executor/Sandbox).

Usage:
    # Quick smoke test with 2 questions without API key (dry-run):
    python scripts/test_bird_eval.py --sample-size 2 --dry-run

    # Live evaluation of 20 questions with RAG on:
    python scripts/test_bird_eval.py --sample-size 20 --seed 42 --rag on

    # Evaluation on a specific database with difficulty filter:
    python scripts/test_bird_eval.py --db-filter financial --difficulty simple
"""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

# Ensure project root is available in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

# Import BIRD evaluation modules
from src.bird import (
    BirdCSVReporter,
    BirdQueryExecutor,
    build_bird_comparison_row,
    compare_bird_results,
    get_database_path,
    load_bird_dev_examples,
    prepare_prompt_question,
    sample_examples,
    sql_similarity_score,
)

load_dotenv()


def parse_arguments() -> argparse.Namespace:
    """Parses command-line arguments for BIRD evaluation."""
    parser = argparse.ArgumentParser(
        description="TextToInsight BIRD Benchmark Evaluation CLI Orchestrator."
    )

    # Dataset & Filtering arguments
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data/bird",
        help="Path to BIRD data directory (default: data/bird)",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=None,
        help="Number of questions to evaluate (default: None = all)",
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
        help="Filter evaluation to a single database ID (e.g., financial)",
    )
    parser.add_argument(
        "--difficulty",
        type=str,
        choices=["simple", "moderate", "challenging"],
        default=None,
        help="Filter evaluation by BIRD difficulty tier",
    )
    parser.add_argument(
        "--stratify",
        action="store_true",
        help="Stratify sample proportionally across difficulty tiers",
    )

    # Output paths
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Destination CSV report path or directory (default: reports/bird/run_{timestamp}/bird_eval_{timestamp}.csv)",
    )

    # Engine & Agent configuration
    parser.add_argument(
        "--model",
        type=str,
        default=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        help="LLM model name (default: from OPENAI_MODEL or gpt-4o-mini)",
    )
    parser.add_argument(
        "--use-evidence",
        choices=["on", "off"],
        default="on",
        help="Inject domain evidence/hints into prompt (default: on)",
    )
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
        help="Enable column enrichment in RAG (default: off)",
    )
    parser.add_argument(
        "--infer-fks",
        action="store_true",
        help="Infer virtual foreign keys heuristically (default: False)",
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
        help="Enable data exploration node (default: on)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="SQL execution timeout in seconds per query (default: 30)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate execution without calling live LLMs (zero API cost)",
    )

    return parser.parse_args()


def simulate_agent_run(
    example: dict[str, Any], attempt_number: int = 1
) -> dict[str, Any]:
    """
    Simulates an agent response for dry-run testing.
    Alternates between correct, self-correcting, and slightly varied queries to test metrics calculation.
    """
    gold_sql = example.get("SQL", "")
    qid = example.get("question_id", 0)

    # Simulate realistic token usage
    prompt_tokens = 600 + (qid % 200)
    completion_tokens = 60 + (qid % 30)

    # Simulate self-correction scenario (attempt 1 failed syntax, attempt 2 fixed)
    if qid % 4 == 0:
        return {
            "sql_gerada": gold_sql,
            "status": "aprovado",
            "erro_execucao": "",
            "tentativas_loop": 2,
            "tokens_input": prompt_tokens * 2,
            "tokens_output": completion_tokens * 2,
            "tokens_total": (prompt_tokens + completion_tokens) * 2,
            "historico_tentativas": [
                {
                    "sql": "SELECT * FROM non_existent_table WHERE id = 1",
                    "erro": "no such table: non_existent_table",
                    "prompt": "Simulated initial prompt",
                    "contexto": "Simulated schema",
                    "raciocinio": "Simulated initial reasoning",
                },
                {
                    "sql": gold_sql,
                    "erro": "",
                    "prompt": "Simulated retry prompt",
                    "contexto": "Simulated schema",
                    "raciocinio": "Simulated corrected reasoning",
                },
            ],
        }

    # Simulate failure in dry-run
    if qid % 5 == 0:
        simulated_sql = f"{gold_sql} LIMIT 1" if "LIMIT" not in gold_sql.upper() else gold_sql.replace("LIMIT 1", "")
        return {
            "sql_gerada": simulated_sql,
            "status": "exec_ok",
            "erro_execucao": "",
            "tentativas_loop": 1,
            "tokens_input": prompt_tokens,
            "tokens_output": completion_tokens,
            "tokens_total": prompt_tokens + completion_tokens,
            "historico_tentativas": [
                {
                    "sql": simulated_sql,
                    "erro": "",
                    "prompt": "Simulated prompt",
                    "contexto": "Simulated schema",
                    "raciocinio": "Simulated reasoning",
                }
            ],
        }

    # Default: 1 attempt success
    return {
        "sql_gerada": gold_sql,
        "status": "aprovado",
        "erro_execucao": "",
        "tentativas_loop": 1,
        "tokens_input": prompt_tokens,
        "tokens_output": completion_tokens,
        "tokens_total": prompt_tokens + completion_tokens,
        "historico_tentativas": [
            {
                "sql": gold_sql,
                "erro": "",
                "prompt": "Simulated prompt",
                "contexto": "Simulated schema",
                "raciocinio": "Simulated reasoning",
            }
        ],
    }



def main() -> None:
    args = parse_arguments()

    print("\n" + "=" * 80)
    print("TEXTTOINSIGHT - BIRD BENCHMARK EVALUATION ORCHESTRATOR")
    print("=" * 80)

    # 1. Validate environment / API keys (if not in dry-run mode)
    api_key = ""
    if not args.dry_run:
        model_name = args.model.lower()
        if "gemini" in model_name:
            api_key = os.getenv("GOOGLE_API_KEY", "")
            if not api_key:
                print("❌ Error: GOOGLE_API_KEY is not set in environment or .env file.")
                sys.exit(1)
        else:
            api_key = os.getenv("OPENAI_API_KEY", "")
            if not api_key:
                print("❌ Error: OPENAI_API_KEY is not set in environment or .env file.")
                sys.exit(1)
    else:
        print("⚡ [DRY-RUN MODE ACTIVE] Simulating LLM agent responses. No API calls or costs incurred.")

    # 2. Load dataset examples
    data_dir = Path(args.data_dir)
    print(f"\n📂 Loading BIRD dev dataset from: {data_dir.resolve()}")
    try:
        examples = load_bird_dev_examples(
            data_dir=data_dir,
            db_id=args.db_filter,
            difficulty=args.difficulty,
            dialect="sqlite",
        )
        print(f"✓ Loaded {len(examples)} examples matching initial filters.")
    except Exception as e:
        print(f"❌ Failed to load BIRD dataset: {e}")
        sys.exit(1)

    # 3. Apply sample size and stratification
    if args.sample_size is not None and args.sample_size < len(examples):
        examples = sample_examples(
            examples=examples,
            sample_size=args.sample_size,
            seed=args.seed,
            stratify_by_difficulty=args.stratify,
        )
        print(f"✓ Sampled {len(examples)} examples (seed={args.seed}, stratify={args.stratify}).")

    if not examples:
        print("⚠️ No examples selected after applying filters. Exiting.")
        sys.exit(0)

    # 4. Initialize Query Executor & CSV Reporter
    executor = BirdQueryExecutor(data_dir=data_dir, timeout_seconds=args.timeout)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    if args.output:
        out_path = Path(args.output)
        if out_path.suffix.lower() == ".csv":
            csv_path = out_path
        else:
            csv_path = out_path / f"bird_eval_{timestamp}.csv"
    else:
        run_folder = BirdCSVReporter.generate_timestamped_run_dir(prefix="run")
        csv_filename = f"bird_eval_{timestamp}.csv"
        csv_path = PROJECT_ROOT / "reports" / "bird" / run_folder / csv_filename

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    reporter = BirdCSVReporter(filepath=csv_path)
    print(f"✓ CSV Reporter initialized at: {csv_path}")

    # 5. Engine cache by db_id
    engine_cache: dict[str, Any] = {}

    # Print Configuration Summary
    print("\n Execution Configuration:")
    print(f"   - Model: {args.model} {'(Dry-run)' if args.dry_run else ''}")
    print(f"   - Evidence (Hints): {args.use_evidence}")
    print(f"   - Schema RAG: {args.rag}")
    print(f"   - Chain of Thought: {args.cot}")
    print(f"   - Data Exploration: {args.data_exploration}")
    print(f"   - Total Questions to Run: {len(examples)}")
    print("-" * 80 + "\n")

    # 6. Evaluation Loop
    start_total_time = time.perf_counter()
    failures: list[dict[str, Any]] = []
    self_corrections: list[dict[str, Any]] = []

    for idx, ex in enumerate(examples, 1):
        q_id = ex["question_id"]
        db_id = ex["db_id"]
        difficulty = ex.get("difficulty", "simple")
        raw_question = ex["question"]
        evidence = ex.get("evidence", "")
        gold_sql = ex["SQL"]

        print(f"[{idx:03d}/{len(examples):03d}] QID {q_id} | DB: {db_id} | Diff: {difficulty}")
        print(f"     Prompt: {raw_question[:75]}...")

        # 6.1 Execute ground-truth gold SQL
        gold_exec = executor.execute_query(db_id=db_id, sql=gold_sql)
        if not gold_exec["success"]:
            print(f"     ⚠️ Gold query failed in database ({gold_exec['error']}). Skipping.")
            continue

        results_gold = gold_exec["results"]
        gold_columns = gold_exec.get("column_names") or []

        # 6.2 Prepare formatted prompt question
        prompt_query = prepare_prompt_question(
            example=ex,
            use_evidence=(args.use_evidence == "on"),
        )

        # 6.3 Run Agent or Simulate
        agent_exec_error = ""
        agent_start = time.perf_counter()

        if args.dry_run:
            agent_result = simulate_agent_run(ex, attempt_number=1)
            time.sleep(0.05)  # brief simulation delay
            agent_time_ms = (time.perf_counter() - agent_start) * 1000
        else:
            # Import InsightEngine lazily only when executing live runs
            from text_to_insight import InsightEngine

            # Resolve db path
            db_path_obj = get_database_path(data_dir=data_dir, db_id=db_id)
            if not db_path_obj.exists():
                print(f"     ❌ Database file not found: {db_path_obj}")
                continue

            # Cache InsightEngine per database ID
            if db_id not in engine_cache:
                try:
                    engine_cache[db_id] = InsightEngine(
                        api_key=api_key,
                        model=args.model,
                        db_path=str(db_path_obj),
                        hitl=False,
                        show_output=False,
                        enable_graphs=False,
                        use_cot=(args.cot == "on"),
                        use_data_exploration=(args.data_exploration == "on"),
                        use_rag=(args.rag == "on"),
                        enrich_rag=(args.enrich_rag == "on"),
                        inferir_fks_virtuais=args.infer_fks,
                    )
                except Exception as e:
                    print(f"     ❌ Failed to instantiate InsightEngine for db '{db_id}': {e}")
                    continue

            engine = engine_cache[db_id]

            try:
                agent_result = engine.run(
                    thread_id=f"bird_eval_{q_id}_{int(time.time())}",
                    query=prompt_query,
                )
            except Exception as e:
                agent_exec_error = str(e)
                agent_result = {
                    "sql_gerada": "",
                    "status": "exec_erro",
                    "erro_execucao": agent_exec_error,
                    "tokens_input": 0,
                    "tokens_output": 0,
                    "tokens_total": 0,
                    "historico_tentativas": [],
                }
            agent_time_ms = (time.perf_counter() - agent_start) * 1000

        # Extract agent execution values
        agent_sql = str(agent_result.get("sql_gerada") or "").strip()
        agent_status = str(agent_result.get("status") or "").strip().lower()
        if not agent_exec_error:
            agent_exec_error = str(agent_result.get("erro_execucao") or "").strip()
        historico_tent = agent_result.get("historico_tentativas") or []
        total_attempts = max(len(historico_tent), int(agent_result.get("tentativas_loop") or 1))

        tokens_in = int(agent_result.get("tokens_input") or 0)
        tokens_out = int(agent_result.get("tokens_output") or 0)
        tokens_tot = int(agent_result.get("tokens_total") or (tokens_in + tokens_out))

        # 6.4 Handle intermediate attempts (if agent iterated multiple times)
        if len(historico_tent) > 1:
            for att_idx, att in enumerate(historico_tent[:-1]):
                att_num = att_idx + 1
                inter_sql = str(att.get("sql") or "").strip()
                inter_err = str(att.get("erro") or "").strip()
                inter_match = False
                inter_status = "exec_erro" if inter_err else "exec_ok"

                # As decided: if intermediate attempt had no syntax error in sandbox, execute to check Pass@1 EX match
                if not inter_err and inter_sql:
                    inter_exec = executor.execute_query(db_id=db_id, sql=inter_sql)
                    if inter_exec["success"]:
                        inter_match = compare_bird_results(
                            results_gold=results_gold,
                            results_agent=inter_exec["results"],
                            gold_sql=gold_sql,
                        )
                    else:
                        inter_err = inter_exec["error"] or "Query execution error"
                        inter_status = "exec_erro"

                inter_sim = sql_similarity_score(gold_sql, inter_sql) if inter_sql else 0.0

                inter_row = build_bird_comparison_row(
                    question_id=q_id,
                    attempt_number=att_num,
                    db_id=db_id,
                    difficulty=difficulty,
                    question=raw_question,
                    evidence=evidence,
                    gold_sql=gold_sql,
                    agent_sql=inter_sql,
                    time_ms=0.0,
                    execution_status=inter_status,
                    similarity_score=inter_sim,
                    execution_match=inter_match,
                    error=inter_err,
                    tokens_input=0,
                    tokens_output=0,
                    tokens_total=0,
                )
                reporter.append_row(inter_row)

        # 6.5 Execute final candidate agent SQL on the database
        results_agent = None
        agent_columns = []
        match_success = False

        if agent_sql and not agent_exec_error:
            agent_db_exec = executor.execute_query(db_id=db_id, sql=agent_sql)
            if agent_db_exec["success"]:
                results_agent = agent_db_exec["results"]
                agent_columns = agent_db_exec.get("column_names") or []
                match_success = compare_bird_results(
                    results_gold=results_gold,
                    results_agent=results_agent,
                    gold_sql=gold_sql,
                )
            else:
                agent_exec_error = agent_db_exec["error"] or "Query execution error"

        # 6.6 Calculate SQL similarity ratio for final attempt
        sim_score = sql_similarity_score(gold_sql, agent_sql) if agent_sql else 0.0

        # 6.7 Append final attempt comparison row
        final_attempt_num = total_attempts
        final_row = build_bird_comparison_row(
            question_id=q_id,
            attempt_number=final_attempt_num,
            db_id=db_id,
            difficulty=difficulty,
            question=raw_question,
            evidence=evidence,
            gold_sql=gold_sql,
            agent_sql=agent_sql,
            time_ms=agent_time_ms,
            execution_status=agent_status,
            similarity_score=sim_score,
            execution_match=match_success,
            error=agent_exec_error,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            tokens_total=tokens_tot,
        )
        reporter.append_row(final_row)

        # 6.8 Collect detailed diagnostic cases for Markdown report
        if match_success and len(historico_tent) > 1:
            self_corrections.append({
                "question_id": q_id,
                "db_id": db_id,
                "difficulty": difficulty,
                "question": raw_question,
                "evidence": evidence,
                "gold_sql": gold_sql,
                "final_sql": agent_sql,
                "attempts": historico_tent,
                "gold_results": results_gold,
                "gold_columns": gold_columns,
                "agent_results": results_agent,
                "agent_columns": agent_columns,
            })
        elif not match_success:
            failures.append({
                "question_id": q_id,
                "db_id": db_id,
                "difficulty": difficulty,
                "question": raw_question,
                "evidence": evidence,
                "gold_sql": gold_sql,
                "agent_sql": agent_sql,
                "error": agent_exec_error,
                "attempts": historico_tent if historico_tent else [{"sql": agent_sql, "erro": agent_exec_error}],
                "gold_results": results_gold,
                "gold_columns": gold_columns,
                "agent_results": results_agent,
                "agent_columns": agent_columns,
            })

        # Print per-question feedback in console
        match_icon = "✅" if match_success else "❌"
        status_icon = "🟢" if agent_status in ("exec_ok", "aprovado") and not agent_exec_error else "🔴"
        attempts_badge = f"({total_attempts} att)" if total_attempts > 1 else ""
        print(
            f"     Result: {match_icon} EX Match {attempts_badge}| {status_icon} Sandbox: {agent_status.upper()} | "
            f"Sim: {sim_score:.2f} | Time: {agent_time_ms:.0f}ms | Tokens: {tokens_tot:,}"
        )
        if not match_success and agent_exec_error:
            print(f"     ⚠️ Error: {agent_exec_error[:80]}")


    total_elapsed = time.perf_counter() - start_total_time

    # 7. Summary & Reporting
    print("\n" + "=" * 80)
    print("EVALUATION SUMMARY & REPORT GENERATION")
    print("=" * 80)

    # Ensure CSV rows are cleanly sorted by (question_id, attempt_number)
    reporter.rewrite_ordered_csv()

    summary = reporter.generate_summary()

    # Export canonical BIRD predict_dev.json
    predict_json_path = reporter.export_predictions_from_rows()
    print(f"✓ Official BIRD predictions exported to: {predict_json_path}")

    # Generate and save Markdown report with failure & self-correction diagnostics
    detailed_cases = {
        "failures": failures,
        "self_corrections": self_corrections,
    }
    config_info = {
        "timestamp": timestamp,
        "model": args.model + (" (Dry-run)" if args.dry_run else ""),
        "dialect": "sqlite",
        "sample_size": len(examples),
        "seed": args.seed,
        "use_rag": (args.rag == "on"),
        "use_evidence": (args.use_evidence == "on"),
        "db_filter": args.db_filter,
        "difficulty_filter": args.difficulty,
    }
    md_path = reporter.save_markdown_report(
        summary,
        config_info=config_info,
        detailed_cases=detailed_cases,
    )
    print(f"✓ Markdown report saved to: {md_path}")
    print(f"✓ Detailed CSV log saved to: {csv_path}")

    # Display Executive Summary in Terminal
    print("\n" + reporter.format_markdown_report(summary, config_info=config_info, detailed_cases=detailed_cases))
    print(f"Total evaluation runtime: {total_elapsed:.1f} seconds.\n")


if __name__ == "__main__":
    main()
