"""
tests/test_bird_eval.py

Comprehensive test suite for BIRD benchmark integration.
Zero external API dependencies, lightweight (< 2 seconds), fully isolated via tmp_path.

Covers:
1. data_loader: Loading, filtering, hints/evidence injection, deterministic sampling.
2. query_executor: Read-only enforcement, math functions, timeouts, memory ceilings.
3. metrics: Column-agnostic Execution Accuracy (EX), multiset Counter equality, float tolerance.
4. csv_reporter: CSV schema persistence, difficulty tier stratification, official predict_dev.json.
5. cli_orchestrator: scripts/test_bird_eval.py smoke test in dry-run mode.
"""

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

import pytest

from src.bird import (
    BirdCSVReporter,
    BirdExecutionLogger,
    BirdQueryExecutor,
    build_bird_comparison_row,
    compare_bird_results,
    format_result_rows_markdown_table,
    get_database_path,
    get_database_uri,
    get_difficulty_distribution,
    get_unique_db_ids,
    load_bird_dev_examples,
    normalize_sql,
    normalize_value,
    prepare_prompt_question,
    sample_examples,
    sql_similarity_score,
)


@pytest.fixture
def mock_bird_env(tmp_path: Path) -> Path:
    """
    Creates an isolated, synthetic BIRD environment with 2 databases and 4 questions:
    - tmp_path/dev.json
    - tmp_path/dev_databases/school/school.sqlite
    - tmp_path/dev_databases/store/store.sqlite
    """
    # 1. Setup dev.json
    examples = [
        {
            "question_id": 1,
            "db_id": "school",
            "question": "How many students are enrolled in grade 10?",
            "evidence": "grade 10 corresponds to grade_level = 10",
            "SQL": "SELECT count(*) FROM students WHERE grade_level = 10",
            "difficulty": "simple",
        },
        {
            "question_id": 2,
            "db_id": "school",
            "question": "What is the average age of students in the Math class?",
            "evidence": "Math class has name = 'Math'",
            "SQL": "SELECT avg(s.age) FROM students AS s JOIN classes AS c ON s.class_id = c.id WHERE c.name = 'Math'",
            "difficulty": "moderate",
        },
        {
            "question_id": 3,
            "db_id": "school",
            "question": "List all classes ordered by class name ascending",
            "evidence": "",
            "SQL": "SELECT name FROM classes ORDER BY name ASC",
            "difficulty": "challenging",
        },
        {
            "question_id": 4,
            "db_id": "store",
            "question": "What is the total value of all stock in store?",
            "evidence": "total stock value = sum(price * quantity)",
            "SQL": "SELECT sum(price * quantity) FROM items",
            "difficulty": "simple",
        },
    ]

    dev_json_path = tmp_path / "dev.json"
    dev_json_path.write_text(json.dumps(examples, indent=2), encoding="utf-8")

    # 2. Setup SQLite databases
    databases_dir = tmp_path / "dev_databases"

    # School DB
    school_dir = databases_dir / "school"
    school_dir.mkdir(parents=True, exist_ok=True)
    school_db = school_dir / "school.sqlite"

    with sqlite3.connect(school_db) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("""
            CREATE TABLE classes (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE students (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                age INTEGER NOT NULL,
                grade_level INTEGER NOT NULL,
                class_id INTEGER REFERENCES classes(id)
            );
        """)
        conn.execute("INSERT INTO classes (id, name) VALUES (1, 'Math'), (2, 'Physics');")
        conn.execute("""
            INSERT INTO students (id, name, age, grade_level, class_id) VALUES
            (1, 'Alice', 15, 10, 1),
            (2, 'Bob', 16, 10, 1),
            (3, 'Charlie', 17, 11, 2),
            (4, 'Diana', 15, 10, 2);
        """)
        conn.commit()

    # Store DB
    store_dir = databases_dir / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    store_db = store_dir / "store.sqlite"

    with sqlite3.connect(store_db) as conn:
        conn.execute("""
            CREATE TABLE items (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                quantity INTEGER NOT NULL
            );
        """)
        conn.execute("""
            INSERT INTO items (id, name, price, quantity) VALUES
            (1, 'Book', 12.50, 10),
            (2, 'Pen', 2.00, 50),
            (3, 'Notebook', 5.00, 20);
        """)
        conn.commit()

    return tmp_path


# ==============================================================================
# 1. Tests for src.bird.data_loader
# ==============================================================================

def test_data_loader_load_and_filter(mock_bird_env: Path) -> None:
    """Tests complete loading and filtering by db_id and difficulty."""
    # Load all
    all_examples = load_bird_dev_examples(mock_bird_env)
    assert len(all_examples) == 4
    assert get_unique_db_ids(all_examples) == ["school", "store"]

    dist = get_difficulty_distribution(all_examples)
    assert dist == {"simple": 2, "moderate": 1, "challenging": 1}

    # Filter by db_id
    school_examples = load_bird_dev_examples(mock_bird_env, db_id="school")
    assert len(school_examples) == 3
    assert all(e["db_id"] == "school" for e in school_examples)

    # Filter by difficulty
    simple_examples = load_bird_dev_examples(mock_bird_env, difficulty="simple")
    assert len(simple_examples) == 2

    # Database paths and URIs
    db_path = get_database_path(mock_bird_env, "school")
    assert db_path.exists()
    assert db_path.name == "school.sqlite"

    db_uri = get_database_uri(mock_bird_env, "school", dialect="sqlite")
    assert db_uri.startswith("sqlite:///")

    # Non-existent file raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        load_bird_dev_examples(mock_bird_env / "non_existent_folder")


def test_data_loader_prepare_prompt_question() -> None:
    """Validates prompt question formatting with and without business evidence."""
    example = {
        "question": "Who scored the highest?",
        "evidence": "highest score = max(points)",
    }

    # With evidence
    prompt_with = prepare_prompt_question(example, use_evidence=True)
    assert prompt_with == "Who scored the highest?\nHint: highest score = max(points)"

    # Without evidence
    prompt_without = prepare_prompt_question(example, use_evidence=False)
    assert prompt_without == "Who scored the highest?"

    # Empty evidence defaults to bare question
    assert prepare_prompt_question({"question": "Count items", "evidence": ""}, use_evidence=True) == "Count items"


def test_data_loader_sample_reproducibility(mock_bird_env: Path) -> None:
    """Verifies that sample_examples is 100% deterministic and supports stratification."""
    examples = load_bird_dev_examples(mock_bird_env)

    # Determinism across repeated calls with same seed
    sample_a = sample_examples(examples, sample_size=2, seed=42)
    sample_b = sample_examples(examples, sample_size=2, seed=42)
    assert [e["question_id"] for e in sample_a] == [e["question_id"] for e in sample_b]

    # Different seed yields different selection
    sample_c = sample_examples(examples, sample_size=2, seed=999)
    assert len(sample_c) == 2


# ==============================================================================
# 2. Tests for src.bird.query_executor
# ==============================================================================

def test_query_executor_execution_and_math(mock_bird_env: Path) -> None:
    """Verifies standard SELECT execution and safe registration of math functions."""
    executor = BirdQueryExecutor(data_dir=mock_bird_env, timeout_seconds=5)

    # Standard SELECT
    res = executor.execute_query("school", "SELECT count(*) FROM students")
    assert res["success"] is True
    assert res["row_count"] == 1
    assert res["results"] == [(4,)]
    assert res["column_names"] == ["count(*)"]
    assert res["time_ms"] >= 0

    # Math functions registered (SQRT, POW, ROUND, ABS)
    math_query = "SELECT SQRT(16), POW(2, 3), ROUND(10.556, 2), ABS(-42)"
    math_res = executor.execute_query("school", math_query)
    assert math_res["success"] is True
    assert math_res["results"] == [(4.0, 8.0, 10.56, 42)]

    # Safe domain error handling: SQRT(-1) and LOG(0) must return None (SQL NULL), never crash
    domain_query = "SELECT SQRT(-4), LOG(0)"
    domain_res = executor.execute_query("school", domain_query)
    assert domain_res["success"] is True
    assert domain_res["results"] == [(None, None)]

    # Safe handling of non-numeric data in trig and helper UDFs (evaluates to NULL, never aborts query)
    invalid_math_query = "SELECT SIN('not_a_number'), ABS('invalid'), SIGN('xyz')"
    invalid_math_res = executor.execute_query("school", invalid_math_query)
    assert invalid_math_res["success"] is True
    assert invalid_math_res["results"] == [(None, None, None)]


def test_query_executor_read_only(mock_bird_env: Path) -> None:
    """Guarantees that mutation commands are blocked by regex pre-check and SQLite ro mode."""
    executor = BirdQueryExecutor(data_dir=mock_bird_env, timeout_seconds=5)

    # Mutation blocked by regex pre-check
    drop_res = executor.execute_query("school", "DROP TABLE students")
    assert drop_res["success"] is False
    assert "Destructive operation 'DROP' is prohibited" in drop_res["error"]

    insert_res = executor.execute_query("school", "INSERT INTO students (id, name, age, grade_level) VALUES (99, 'Eve', 16, 10)")
    assert insert_res["success"] is False
    assert "Destructive operation 'INSERT' is prohibited" in insert_res["error"]

    # Legitimate SELECT with mutation keyword inside string literal must NOT be blocked
    literal_res = executor.execute_query("school", "SELECT name FROM students WHERE name = 'DELETE'")
    assert literal_res["success"] is True
    assert literal_res["error"] is None



def test_query_executor_timeout(mock_bird_env: Path) -> None:
    """Verifies that queries exceeding timeout are interrupted and return a clean error."""
    # Set a tiny 0.1-second timeout
    executor = BirdQueryExecutor(data_dir=mock_bird_env, timeout_seconds=0.1)

    # Long-running recursive CTE query that exceeds 0.1s
    runaway_sql = """
        WITH RECURSIVE cnt(x) AS (
            SELECT 1
            UNION ALL
            SELECT x+1 FROM cnt WHERE x < 50000000
        )
        SELECT sum(x) FROM cnt;
    """
    res = executor.execute_query("school", runaway_sql)
    assert res["success"] is False
    assert "timed out" in res["error"].lower()


# ==============================================================================
# 3. Tests for src.bird.metrics
# ==============================================================================

def test_metrics_normalization_and_similarity() -> None:
    """Tests SQL comment removal, whitespace collapsing, and textual similarity."""
    raw_sql = """
        /* Multi-line comment */
        SELECT  id,  name  -- trailing comment
        FROM   students;
    """
    normalized = normalize_sql(raw_sql)
    assert normalized == "SELECT ID, NAME FROM STUDENTS"


    score = sql_similarity_score("SELECT id FROM t", "SELECT id FROM t")
    assert score == 1.0


def test_metrics_execution_accuracy() -> None:
    """Tests column alias agnosticism, multiset Counter comparison, and float tolerance."""
    # 1. Alias agnosticism: different column keys with identical values MUST match
    gold_rows = [{"count(*)": 4}]
    agent_rows = [{"total_students": 4}]
    assert compare_bird_results(gold_rows, agent_rows) is True

    # 2. Float tolerance (rounded to 2 decimal places)
    gold_floats = [(10.500001,)]
    agent_floats = [(10.50,)]
    assert compare_bird_results(gold_floats, agent_floats) is True

    # 3. Unordered multiset matching: duplicate row count must match exactly
    # Two identical rows vs one row MUST return False
    assert compare_bird_results([(1,), (1,)], [(1,)], order_matters=False) is False
    # Same elements in different order MUST return True when order_matters=False
    assert compare_bird_results([(1,), (2,)], [(2,), (1,)], order_matters=False) is True

    # 4. Order sensitivity: auto-detected via ORDER BY in gold_sql
    gold_ordered_sql = "SELECT name FROM students ORDER BY name ASC"
    assert compare_bird_results([("Alice",), ("Bob",)], [("Bob",), ("Alice",)], gold_sql=gold_ordered_sql) is False
    assert compare_bird_results([("Alice",), ("Bob",)], [("Alice",), ("Bob",)], gold_sql=gold_ordered_sql) is True


# ==============================================================================
# 4. Tests for src.bird.csv_reporter
# ==============================================================================

def test_csv_reporter_and_predict_json(tmp_path: Path) -> None:
    """Validates 16-column CSV output, summary aggregation, and official BIRD predict_dev.json."""
    csv_file = tmp_path / "reports" / "eval.csv"
    reporter = BirdCSVReporter(filepath=csv_file)

    # Verify header
    assert len(reporter.HEADERS) == 16
    with open(csv_file, "r", encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        assert len(header) == 16
        assert "execution_status" in header
        assert "critic_verdict" not in header

    # Add sample rows:
    # Q1: 2 attempts, recovered by sandbox error feedback in attempt 2
    row_1_att1 = build_bird_comparison_row(
        question_id=1, attempt_number=1, db_id="school", difficulty="simple",
        question="Count students", evidence="", gold_sql="SELECT count(*) FROM students",
        agent_sql="SELECT count(id) FROM students", time_ms=500.0,
        execution_status="exec_erro", error="Syntax error near id", similarity_score=0.9,
        execution_match=False, tokens_input=300, tokens_output=30
    )
    row_1_att2 = build_bird_comparison_row(
        question_id=1, attempt_number=2, db_id="school", difficulty="simple",
        question="Count students", evidence="", gold_sql="SELECT count(*) FROM students",
        agent_sql="SELECT count(*) FROM students", time_ms=400.0,
        execution_status="exec_ok", error="", similarity_score=1.0,
        execution_match=True, tokens_input=400, tokens_output=25
    )
    reporter.append_row(row_1_att1)
    reporter.append_row(row_1_att2)

    # Summary metrics verification
    summary = reporter.generate_summary()
    assert summary["total_questions"] == 1
    assert summary["total_attempts"] == 2
    assert summary["ex_final_count"] == 1
    assert summary["ex_final_rate"] == 100.0
    assert summary["ex_pass_at_1_count"] == 0
    assert summary["self_correction_count"] == 1
    assert summary["self_correction_rate"] == 100.0
    assert summary["sandbox_success_count"] == 1
    assert summary["by_difficulty"]["simple"]["count"] == 1
    assert summary["by_difficulty"]["simple"]["ex_rate"] == 100.0


    # Official predict_dev.json export verification
    pred_path = reporter.export_predictions_from_rows()
    assert pred_path.exists()
    with open(pred_path, "r", encoding="utf-8") as f:
        preds = json.load(f)
    assert "1" in preds
    assert preds["1"] == "SELECT count(*) FROM students\t----- ----- -----\tschool"

    # Markdown report verification
    detailed_cases = {
        "self_corrections": [
            {
                "question_id": 1,
                "db_id": "school",
                "difficulty": "simple",
                "question": "Count students",
                "evidence": "",
                "gold_sql": "SELECT count(*) FROM students",
                "attempts": [
                    {"agent_sql": "SELECT count(id) FROM students", "error": "Syntax error"},
                    {"agent_sql": "SELECT count(*) FROM students", "error": ""},
                ],
                "gold_results": [(4,)],
                "gold_columns": ["count(*)"],
            }
        ]
    }
    md_path = reporter.save_markdown_report(
        summary, config_info={"model": "gpt-4o-mini"}, detailed_cases=detailed_cases
    )
    assert md_path.exists()
    md_content = md_path.read_text(encoding="utf-8")
    assert "Execution Accuracy (EX Final)" in md_content
    # Must correctly extract SQL and error even when keys are agent_sql / error (never '(empty)')
    assert "SELECT count(id) FROM students" in md_content
    assert "Syntax error" in md_content


def test_format_result_rows_markdown_table() -> None:
    """Verifies that result row datasets are formatted as markdown tables with proper truncation."""
    # 1. Empty and None cases
    assert "unavailable" in format_result_rows_markdown_table(None)
    assert "0 rows returned" in format_result_rows_markdown_table([])
    assert "0 columns returned" in format_result_rows_markdown_table([()])

    # 2. Tuple rows with column names
    rows = [("Alice", 15), ("Bob", 16)]
    table = format_result_rows_markdown_table(rows, column_names=["Name", "Age"], max_rows=10)
    assert "| Name | Age |" in table
    assert "| Alice | 15 |" in table
    assert "| Bob | 16 |" in table

    # 3. Truncation when exceeding max_rows
    large_rows = [(i, f"Val_{i}") for i in range(15)]
    trunc_table = format_result_rows_markdown_table(large_rows, max_rows=10)
    assert "| col_1 | col_2 |" in trunc_table
    assert "... and 5 more row(s) (total: 15)" in trunc_table

    # 4. Dict rows
    dict_rows = [{"city": "San Francisco", "count": 42}]
    dict_table = format_result_rows_markdown_table(dict_rows, max_rows=10)
    assert "| city | count |" in dict_table
    assert "| San Francisco | 42 |" in dict_table


def test_csv_reporter_rewrite_ordered_csv(tmp_path: Path) -> None:
    """Verifies that rewrite_ordered_csv sorts rows strictly by (question_id, attempt_number)."""
    csv_file = tmp_path / "ordered_eval.csv"
    reporter = BirdCSVReporter(filepath=csv_file)

    # Append rows in deliberately out-of-order sequence
    row_q2_att1 = build_bird_comparison_row(
        question_id=2, attempt_number=1, db_id="db", difficulty="simple",
        question="Q2", evidence="", gold_sql="SELECT 2", agent_sql="SELECT 2",
        time_ms=10.0,
    )
    row_q1_att2 = build_bird_comparison_row(
        question_id=1, attempt_number=2, db_id="db", difficulty="simple",
        question="Q1", evidence="", gold_sql="SELECT 1", agent_sql="SELECT 1",
        time_ms=10.0,
    )
    row_q1_att1 = build_bird_comparison_row(
        question_id=1, attempt_number=1, db_id="db", difficulty="simple",
        question="Q1", evidence="", gold_sql="SELECT 1", agent_sql="SELECT 0",
        time_ms=10.0,
    )

    reporter.append_row(row_q2_att1)
    reporter.append_row(row_q1_att2)
    reporter.append_row(row_q1_att1)

    # Sort and rewrite
    reporter.rewrite_ordered_csv()

    # Read back and verify strict order
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = list(f.readlines())
    # line 0 is header, 1 is (1,1), 2 is (1,2), 3 is (2,1)
    assert len(reader) == 4
    assert reader[1].startswith("1,1,")
    assert reader[2].startswith("1,2,")
    assert reader[3].startswith("2,1,")


def test_markdown_report_detailed_inspection(tmp_path: Path) -> None:
    """Verifies that detailed failure cases and self-corrections are rendered into the Markdown report."""
    csv_file = tmp_path / "report_detailed.csv"
    reporter = BirdCSVReporter(filepath=csv_file)
    summary = reporter.generate_summary()

    detailed_cases = {
        "self_corrections": [
            {
                "question_id": 10,
                "db_id": "school",
                "difficulty": "challenging",
                "question": "What is the highest score?",
                "evidence": "highest means max score",
                "gold_sql": "SELECT max(score) FROM tests",
                "final_sql": "SELECT max(score) FROM tests",
                "attempts": [
                    {"sql": "SELECT score FROM tests", "erro": "wrong aggregate"},
                    {"sql": "SELECT max(score) FROM tests", "erro": ""},
                ],
                "gold_results": [(100,)],
                "gold_columns": ["max_score"],
            }
        ],
        "failures": [
            {
                "question_id": 20,
                "db_id": "store",
                "difficulty": "simple",
                "question": "List all item names",
                "evidence": "",
                "gold_sql": "SELECT name FROM items",
                "agent_sql": "SELECT name FROM items LIMIT 1",
                "error": "",
                "attempts": [{"sql": "SELECT name FROM items LIMIT 1", "erro": ""}],
                "gold_results": [("Book",), ("Pen",), ("Notebook",)],
                "gold_columns": ["name"],
                "agent_results": [("Book",)],
                "agent_columns": ["name"],
            }
        ],
    }

    md_path = reporter.save_markdown_report(summary, detailed_cases=detailed_cases)
    content = md_path.read_text(encoding="utf-8")

    # Assert sections are present
    assert "## 5. Successful Self-Correction Cases" in content
    assert "Question 10 (`school` - Challenging)" in content
    assert "SELECT max(score) FROM tests" in content
    assert "| max_score |" in content

    assert "## 6. Detailed Failure Analysis (Execution Mismatches & Errors)" in content
    assert "Question 20 (`store` - Simple)" in content
    assert "| Book |" in content
    assert "| Pen |" in content
    assert "| Notebook |" in content


# ==============================================================================
# 5. Execution Logger Tests (BirdExecutionLogger)
# ==============================================================================

def test_bird_execution_logger_lifecycle_and_formatting(tmp_path: Path) -> None:
    """Verifies header, query block, tee capture, and footer formatting in .log file."""
    output_dir = tmp_path / "run_test"
    timestamp = "2026-10-14_17-45-00"

    logger = BirdExecutionLogger(
        output_dir=output_dir,
        timestamp=timestamp,
        enabled=True,
    )

    # 1. Header
    logger.log_run_header({
        "timestamp": timestamp,
        "model": "gpt-5-turbo",
        "dialect": "sqlite",
        "sample_size": 2,
        "seed": 42,
        "use_rag": True,
        "use_evidence": True,
        "use_cot": True,
        "use_data_exploration": False,
        "infer_fks": False,
        "db_filter": "school",
        "difficulty_filter": "simple",
    })

    # 2. Query 1: Single attempt with Tee capture
    with logger.capture_trace() as get_trace:
        print("[PLANEJADOR] Generating plan for QID 1")
        print("[EXECUTOR] Executing query on school DB")

    trace_output = get_trace()
    assert "[PLANEJADOR]" in trace_output
    assert "[EXECUTOR]" in trace_output

    logger.log_query(
        question_id=1,
        db_id="school",
        difficulty="simple",
        question="How many students are enrolled in grade 10?",
        evidence="grade 10 corresponds to grade_level = 10",
        gold_sql="SELECT count(*) FROM students WHERE grade_level = 10",
        agent_result={
            "sql_gerada": "SELECT count(*) FROM students WHERE grade_level = 10",
            "status": "aprovado",
            "erro_execucao": "",
            "raciocinio_agente": "Filter by grade_level = 10 and aggregate with count.",
            "ultimo_prompt": "You are a SQL expert...",
            "contexto_rag_schema": "TABLE students (id INT, grade_level INT)",
            "tokens_input": 500,
            "tokens_output": 50,
            "tokens_total": 550,
        },
        match_success=True,
        similarity_score=1.0,
        time_ms=125.0,
        gold_results=[(3,)],
        agent_results=[(3,)],
        captured_trace=trace_output,
    )

    # 3. Query 2: Multi-attempt self-correction query
    logger.log_query(
        question_id=2,
        db_id="school",
        difficulty="moderate",
        question="Average age of students in Math class?",
        evidence="Math class has name = 'Math'",
        gold_sql="SELECT avg(s.age) FROM students AS s JOIN classes AS c ON s.class_id = c.id WHERE c.name = 'Math'",
        agent_result={
            "sql_gerada": "SELECT avg(s.age) FROM students AS s JOIN classes AS c ON s.class_id = c.id WHERE c.name = 'Math'",
            "status": "aprovado",
            "erro_execucao": "",
            "tokens_input": 1200,
            "tokens_output": 120,
            "tokens_total": 1320,
            "historico_tentativas": [
                {
                    "sql": "SELECT avg(age) FROM students WHERE class = 'Math'",
                    "erro": "no such column: class",
                    "prompt": "Prompt attempt 1",
                    "raciocinio": "Initial attempt without join",
                },
                {
                    "sql": "SELECT avg(s.age) FROM students AS s JOIN classes AS c ON s.class_id = c.id WHERE c.name = 'Math'",
                    "erro": "",
                    "prompt": "Prompt attempt 2 with correction",
                    "raciocinio": "Correction: join with classes table on class_id",
                },
            ],
        },
        match_success=True,
        similarity_score=0.95,
        time_ms=450.0,
        gold_results=[(15.5,)],
        agent_results=[(15.5,)],
    )

    # 4. Footer
    logger.log_run_footer(
        summary={
            "total_questions": 2,
            "execution_match_final": 2,
            "execution_match_final_pct": 100.0,
            "pass1_execution_match": 1,
            "pass1_pct": 50.0,
            "self_correction_success_pct": 100.0,
            "tokens_total": 1870,
            "tokens_input": 1700,
            "tokens_output": 170,
        },
        total_elapsed=2.5,
    )
    logger.close()

    # Assert file existence and structure
    log_file = output_dir / f"bird_eval_{timestamp}.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")

    # Header assertions
    assert "TEXTTOINSIGHT - BIRD BENCHMARK EXECUTION LOG" in content
    assert "Timestamp:          2026-10-14_17-45-00" in content
    assert "Model:              gpt-5-turbo" in content
    assert "RAG: ON | Evidence: ON | CoT: ON | DataExploration: OFF | InferFKs: OFF" in content
    assert "Filters:            DB: school | Difficulty: simple" in content

    # QID 1 assertions
    assert "=== [START QID: 1 | DB: school | Difficulty: simple] ===" in content
    assert "[QUESTION] How many students are enrolled in grade 10?" in content
    assert "[EVIDENCE / HINT] grade 10 corresponds to grade_level = 10" in content
    assert "TABLE students (id INT, grade_level INT)" in content
    assert "--- [EXECUTION TRACE / FLUXO DE NÓS (TEE)] ---" in content
    assert "[PLANEJADOR] Generating plan for QID 1" in content
    assert "[EXECUTOR] Executing query on school DB" in content
    assert "[RACIOCÍNIO DO AGENTE (CoT / THOUGHT)]:" in content
    assert "[SQL GERADA]:\nSELECT count(*) FROM students WHERE grade_level = 10" in content
    assert "[STATUS SANDBOX]: aprovado" in content
    assert "[EX MATCH (Execution Accuracy)]: ✅ TRUE" in content
    assert "[SQL SIMILARITY]: 1.0000" in content
    assert "[LATÊNCIA AGENTE]: 125 ms" in content
    assert "[TOKENS]: Input: 500 | Output: 50 | Total: 550" in content
    assert "=== [END QID: 1] ===" in content

    # QID 2 (Self-correction attempts) assertions
    assert "=== [START QID: 2 | DB: school | Difficulty: moderate] ===" in content
    assert "--- [ATTEMPTS (2 tentativas)] ---" in content
    assert ">> Tentativa #1:" in content
    assert "[Prompt Enviado]:\nPrompt attempt 1" in content
    assert "[Raciocínio CoT / Thought]:\nInitial attempt without join" in content
    assert "[Status / Erro]: no such column: class" in content
    assert ">> Tentativa #2:" in content
    assert "Prompt attempt 2 with correction" in content
    assert "Correction: join with classes table on class_id" in content
    assert "=== [END QID: 2] ===" in content

    # Footer assertions
    assert "EVALUATION RUN SUMMARY" in content
    assert "Execution Accuracy (Final EX):      2/2 (100.00%)" in content
    assert "Pass@1 EX Accuracy:                 1/2 (50.00%)" in content
    assert "Self-Correction Recovery Rate:      100.00%" in content
    assert "Total Tokens Consumed:              1,870 (Input: 1,700 | Output: 170)" in content
    assert "Total Elapsed Time:                 2.5 seconds" in content


def test_bird_execution_logger_disabled(tmp_path: Path) -> None:
    """Verifies that BirdExecutionLogger does not write when enabled=False."""
    output_dir = tmp_path / "disabled_run"
    logger = BirdExecutionLogger(
        output_dir=output_dir,
        enabled=False,
    )
    assert logger._file_handle is None

    # Operations should be no-ops without error
    logger.log_run_header({"model": "gpt-5"})
    with logger.capture_trace() as get_trace:
        print("This should not crash or buffer")
    assert get_trace() == ""

    logger.log_query(
        question_id=1,
        db_id="school",
        difficulty="simple",
        question="Q",
        evidence="",
        gold_sql="SELECT 1",
        agent_result={},
        match_success=True,
        similarity_score=1.0,
        time_ms=10.0,
    )
    logger.log_run_footer({"total_questions": 1}, total_elapsed=1.0)
    logger.close()

    assert not (output_dir / f"bird_eval_{logger.timestamp}.log").exists()


def test_bird_execution_logger_custom_log_path(tmp_path: Path) -> None:
    """Verifies explicit log_file parameter paths."""
    custom_log = tmp_path / "nested" / "my_custom_eval.log"
    with BirdExecutionLogger(output_dir=tmp_path, log_file=custom_log, enabled=True) as logger:
        logger.log_run_header({"model": "custom-model"})
        logger.log_run_footer({"total_questions": 0}, total_elapsed=0.5)

    assert custom_log.exists()
    content = custom_log.read_text(encoding="utf-8")
    assert "custom-model" in content


# ==============================================================================
# 6. Smoke Tests for scripts/test_bird_eval.py CLI Orchestrator
# ==============================================================================

def test_cli_orchestrator_dry_run(mock_bird_env: Path, tmp_path: Path) -> None:
    """Verifies that the CLI orchestrator executes successfully in dry-run mode."""
    out_csv = tmp_path / "cli_report.csv"
    cmd = [
        sys.executable,
        "scripts/test_bird_eval.py",
        "--data-dir", str(mock_bird_env),
        "--sample-size", "2",
        "--seed", "42",
        "--dry-run",
        "--output", str(out_csv),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"CLI command failed:\n{result.stderr}"

    # Verify generated outputs
    assert out_csv.exists()
    assert out_csv.with_suffix(".json").exists()
    assert out_csv.with_suffix(".md").exists()
    assert out_csv.with_suffix(".log").exists()
    assert "EVALUATION SUMMARY & REPORT GENERATION" in result.stdout

    # Verify log content
    log_content = out_csv.with_suffix(".log").read_text(encoding="utf-8")
    assert "TEXTTOINSIGHT - BIRD BENCHMARK EXECUTION LOG" in log_content
    assert "=== [START QID:" in log_content
    assert "EVALUATION RUN SUMMARY" in log_content


def test_cli_orchestrator_dry_run_with_dir_output(mock_bird_env: Path, tmp_path: Path) -> None:
    """Verifies that passing a directory path to --output creates timestamped artifacts inside it."""
    out_dir = tmp_path / "custom_run_folder"
    cmd = [
        sys.executable,
        "scripts/test_bird_eval.py",
        "--data-dir", str(mock_bird_env),
        "--sample-size", "2",
        "--seed", "42",
        "--dry-run",
        "--output", str(out_dir),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"CLI command failed:\n{result.stderr}"

    # Verify generated outputs inside custom directory
    generated_csvs = list(out_dir.glob("bird_eval_*.csv"))
    assert len(generated_csvs) == 1
    generated_csv = generated_csvs[0]
    assert generated_csv.with_suffix(".json").exists()
    assert generated_csv.with_suffix(".md").exists()
    assert generated_csv.with_suffix(".log").exists()


def test_cli_orchestrator_log_disabled(mock_bird_env: Path, tmp_path: Path) -> None:
    """Verifies that passing --log off suppresses .log file generation."""
    out_csv = tmp_path / "no_log_report.csv"
    cmd = [
        sys.executable,
        "scripts/test_bird_eval.py",
        "--data-dir", str(mock_bird_env),
        "--sample-size", "1",
        "--seed", "42",
        "--dry-run",
        "--output", str(out_csv),
        "--log", "off",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"CLI command failed:\n{result.stderr}"

    assert out_csv.exists()
    assert not out_csv.with_suffix(".log").exists()


def test_cli_orchestrator_custom_log_file(mock_bird_env: Path, tmp_path: Path) -> None:
    """Verifies that passing --log-file directs logging to an explicit path."""
    out_csv = tmp_path / "custom_log_report.csv"
    custom_log = tmp_path / "explicit_logs" / "custom_test.log"
    cmd = [
        sys.executable,
        "scripts/test_bird_eval.py",
        "--data-dir", str(mock_bird_env),
        "--sample-size", "1",
        "--seed", "42",
        "--dry-run",
        "--output", str(out_csv),
        "--log-file", str(custom_log),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"CLI command failed:\n{result.stderr}"

    assert out_csv.exists()
    assert custom_log.exists()
    assert "TEXTTOINSIGHT - BIRD BENCHMARK EXECUTION LOG" in custom_log.read_text(encoding="utf-8")


# ==============================================================================
# 6. Tests for Intermediate Folder Handling (dev_[date])
# ==============================================================================

def test_data_loader_dev_json_in_intermediate_folder(tmp_path: Path) -> None:
    """Verifies that load_bird_dev_examples finds dev.json inside any intermediate folder (e.g. dev_20240627)."""
    sub_dir = tmp_path / "dev_20240627"
    sub_dir.mkdir(parents=True)

    examples = [
        {
            "question_id": 101,
            "db_id": "test_db",
            "question": "Count rows",
            "evidence": "",
            "SQL": "SELECT count(*) FROM t",
            "difficulty": "simple",
        }
    ]
    dev_json_file = sub_dir / "dev.json"
    dev_json_file.write_text(json.dumps(examples), encoding="utf-8")

    # SQLite DB in intermediate folder
    db_file = sub_dir / "dev_databases" / "test_db" / "test_db.sqlite"
    db_file.parent.mkdir(parents=True)
    with sqlite3.connect(db_file) as conn:
        conn.execute("CREATE TABLE t (id INT);")
        conn.execute("INSERT INTO t VALUES (1);")

    # load_bird_dev_examples should find dev.json in sub_dir even when pointing to tmp_path root
    loaded = load_bird_dev_examples(tmp_path)
    assert len(loaded) == 1
    assert loaded[0]["question_id"] == 101

    # Should also have made dev.json visible in root and subfolder
    assert (tmp_path / "dev.json").exists()
    assert dev_json_file.exists()

    # get_database_path should find the nested sqlite file
    resolved_db = get_database_path(tmp_path, "test_db")
    assert resolved_db.exists()
    assert resolved_db.name == "test_db.sqlite"


def test_setup_bird_data_intermediate_folder(tmp_path: Path) -> None:
    """Verifies normalize_extracted_layout and verify_setup handle dev_[date] layout."""
    import zipfile
    from scripts.setup_bird_data import normalize_extracted_layout, verify_setup

    data_dir = tmp_path / "bird"
    sub_dir = data_dir / "dev_20240627"
    sub_dir.mkdir(parents=True)

    # 1. Place dev.json in intermediate folder
    examples = [
        {
            "question_id": 1,
            "db_id": "school",
            "question": "What is the count?",
            "SQL": "SELECT 1",
            "difficulty": "simple",
        }
    ]
    (sub_dir / "dev.json").write_text(json.dumps(examples), encoding="utf-8")

    # 2. Place dev_databases.zip in intermediate folder
    db_zip_path = sub_dir / "dev_databases.zip"
    inner_db_dir = tmp_path / "temp_db_build" / "dev_databases" / "school"
    inner_db_dir.mkdir(parents=True)
    dummy_db = inner_db_dir / "school.sqlite"
    with sqlite3.connect(dummy_db) as conn:
        conn.execute("CREATE TABLE t (x INT);")

    with zipfile.ZipFile(db_zip_path, "w") as z:
        z.write(dummy_db, arcname="dev_databases/school/school.sqlite")

    # Run normalize and verify
    assert verify_setup(data_dir) is True

    # dev.json should now be visible in root AND in intermediate folder
    assert (data_dir / "dev.json").exists()
    assert (sub_dir / "dev.json").exists()

    # Databases should be extracted
    assert (data_dir / "dev_databases" / "school" / "school.sqlite").exists()


def test_setup_bird_data_parse_arguments(monkeypatch) -> None:
    """Verifies CLI flag parsing for setup_bird_data.py including --mini and --full."""
    from scripts.setup_bird_data import parse_arguments

    monkeypatch.setattr("sys.argv", ["setup_bird_data.py", "--mini"])
    args = parse_arguments()
    assert args.mini is True
    assert args.full is False

    monkeypatch.setattr("sys.argv", ["setup_bird_data.py", "--full"])
    args = parse_arguments()
    assert args.full is True
    assert args.mini is False

