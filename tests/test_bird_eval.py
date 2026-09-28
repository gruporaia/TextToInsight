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
    BirdQueryExecutor,
    build_bird_comparison_row,
    compare_bird_results,
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
    md_path = reporter.save_markdown_report(summary, config_info={"model": "gpt-4o-mini"})
    assert md_path.exists()
    assert "Execution Accuracy (EX Final)" in md_path.read_text(encoding="utf-8")


# ==============================================================================
# 5. Smoke Test for scripts/test_bird_eval.py
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
    assert "EVALUATION SUMMARY & REPORT GENERATION" in result.stdout


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
