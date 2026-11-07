"""
tests/test_spider2_eval.py

Comprehensive test suite for Spider 2.0 benchmark integration.
Zero external API dependencies, lightweight (< 2 seconds), fully isolated via tmp_path synthetic fixture.

Covers:
1. data_loader: Loading, manifest discovery, dialect filtering (sqlite, bigquery, snowflake, all),
   gold SQL and CSV result loading.
2. query_executor: Read-only enforcement, mutation blocking, safe math functions, timeouts,
   factory resolution, masking database credentials.
3. security: Guardrails blocking raw keys in --api-key-env and sensitive kwargs in --model-kwargs
   without leaking secrets to stdout/stderr or logs.
4. logger: SpiderExecutionLogger lifecycle, Tee stream trace capture, per-query formatting with
   --- [TOKENS BY NODE] ---, and run footer summaries.
5. csv_reporter: 24-column CSV schema persistence, tokens_by_node JSON serialization, and node_tokens
   summary aggregation.
6. cli_orchestrator: scripts/test_spider2_eval.py dry-run end-to-end smoke tests and security rejection.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any

import pytest

from src.spider.csv_reporter import CSVReporter
from src.spider.logger import SpiderExecutionLogger, _sanitize_kwargs
from src.spider.metrics import (
    build_comparison_row,
    results_exact_match,
    results_f1_score,
    sql_similarity_score,
)
from src.spider.query_executor import (
    BaseSpiderQueryExecutor,
    Spider2QueryExecutor,
    SpiderBigQueryExecutor,
    SpiderQueryExecutor,
    SpiderSQLiteExecutor,
    SpiderSnowflakeExecutor,
    SpiderSQLAlchemyExecutor,
    get_spider_executor,
    mask_db_url,
)
from scripts.test_spider2_eval import (
    _model_kwarg,
    _validate_api_key_env,
    get_gold_results,
    get_gold_sql,
    get_instance_dialect,
    load_spider2_examples,
    parse_arguments,
    resolve_model_and_provider,
    simulate_agent_run,
)


# ==============================================================================
# SYNTHETIC FIXTURE (Zero Dependency on spider2-lite/)
# ==============================================================================

@pytest.fixture
def mock_spider2_env(tmp_path: Path) -> Path:
    """
    Creates an isolated, synthetic Spider 2.0 environment on the fly:
    - tmp_path/spider2-lite.jsonl (4 synthetic instances covering sqlite, bq, sf)
    - tmp_path/resource/databases/spider2-localdb/test_db/test_db.sqlite
    - tmp_path/resource/databases/spider2-localdb/test_db.sqlite (direct alias)
    - tmp_path/evaluation_suite/gold/sql/local_001.sql
    - tmp_path/evaluation_suite/gold/sql/local_002.sql
    - tmp_path/evaluation_suite/gold/exec_result/local_001.csv
    - tmp_path/evaluation_suite/gold/exec_result/local_002.csv
    - tmp_path/resource/documents/doc_001.md
    """
    # 1. Setup spider2-lite.jsonl
    instances = [
        {
            "instance_id": "local_001",
            "db": "test_db",
            "question": "How many active users are registered in the system?",
            "external_knowledge": "doc_001.md",
        },
        {
            "instance_id": "local_002",
            "db": "test_db",
            "question": "What is the maximum order amount recorded?",
            "external_knowledge": "",
        },
        {
            "instance_id": "bq_001",
            "db": "bq_analytics",
            "question": "Count page views grouped by referrer domain in BigQuery",
            "external_knowledge": "",
        },
        {
            "instance_id": "sf_001",
            "db": "sf_warehouse",
            "question": "Calculate total monthly revenue from Snowflake orders table",
            "external_knowledge": "",
        },
    ]

    jsonl_path = tmp_path / "spider2-lite.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for inst in instances:
            f.write(json.dumps(inst) + "\n")

    # 2. Setup SQLite databases
    db_root = tmp_path / "resource" / "databases" / "spider2-localdb"
    db_dir = db_root / "test_db"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_file = db_dir / "test_db.sqlite"

    with sqlite3.connect(db_file) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                is_active INTEGER NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                user_id INTEGER REFERENCES users(id),
                total REAL NOT NULL
            );
        """)
        conn.execute("""
            INSERT INTO users (id, name, is_active) VALUES
            (1, 'Alice', 1),
            (2, 'Bob', 0),
            (3, 'Charlie', 1);
        """)
        conn.execute("""
            INSERT INTO orders (id, user_id, total) VALUES
            (1, 1, 150.0),
            (2, 3, 300.0),
            (3, 1, 50.0);
        """)
        conn.commit()

    # Also place alias at root of sqlite-dir for dual lookup compatibility
    root_db_file = db_root / "test_db.sqlite"
    root_db_file.write_bytes(db_file.read_bytes())

    # 3. Setup Gold Reference SQLs
    sql_dir = tmp_path / "evaluation_suite" / "gold" / "sql"
    sql_dir.mkdir(parents=True, exist_ok=True)
    (sql_dir / "local_001.sql").write_text(
        "SELECT count(*) AS count FROM users WHERE is_active = 1;",
        encoding="utf-8",
    )
    (sql_dir / "local_002.sql").write_text(
        "SELECT max(total) AS max_total FROM orders;",
        encoding="utf-8",
    )

    # 4. Setup Gold Reference CSV Results
    exec_dir = tmp_path / "evaluation_suite" / "gold" / "exec_result"
    exec_dir.mkdir(parents=True, exist_ok=True)
    (exec_dir / "local_001.csv").write_text("count\n2\n", encoding="utf-8")
    (exec_dir / "local_002.csv").write_text("max_total\n300.0\n", encoding="utf-8")

    # 5. Setup External Knowledge Document
    doc_dir = tmp_path / "resource" / "documents"
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "doc_001.md").write_text(
        "Active users are defined as records where is_active is equal to 1.",
        encoding="utf-8",
    )

    return tmp_path


# ==============================================================================
# 1. DATA LOADER & DIALECT FILTERING TESTS
# ==============================================================================

def test_data_loader_load_spider2_examples(mock_spider2_env: Path) -> None:
    """Verifies that load_spider2_examples reads all synthetic instances accurately."""
    examples = load_spider2_examples(mock_spider2_env)
    assert len(examples) == 4
    instance_ids = [ex["instance_id"] for ex in examples]
    assert instance_ids == ["local_001", "local_002", "bq_001", "sf_001"]

    first = examples[0]
    assert first["instance_id"] == "local_001"
    assert first["db"] == "test_db"
    assert "active users" in first["question"].lower()
    assert first["external_knowledge"] == "doc_001.md"


def test_data_loader_missing_manifest_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that load_spider2_examples raises FileNotFoundError when manifest is absent."""
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()
    # Isolate from repo root fallback so only empty_dir is checked
    monkeypatch.setattr("scripts.test_spider2_eval.PROJECT_ROOT", empty_dir)

    with pytest.raises(FileNotFoundError, match="Spider 2 dataset manifest not found"):
        load_spider2_examples(empty_dir)


def test_get_instance_dialect() -> None:
    """Verifies prefix-based dialect inference for Spider 2.0 instances."""
    assert get_instance_dialect("local_001") == "sqlite"
    assert get_instance_dialect("LOCAL_042") == "sqlite"
    assert get_instance_dialect("bq_010") == "bigquery"
    assert get_instance_dialect("ga_005") == "bigquery"
    assert get_instance_dialect("sf_002") == "snowflake"
    assert get_instance_dialect("unknown_instance") == "sqlite"


def test_data_loader_get_gold_sql_and_results(mock_spider2_env: Path) -> None:
    """Verifies retrieval of gold SQL and CSV execution results."""
    # Existing instance 1
    gold_sql_1 = get_gold_sql(mock_spider2_env, "local_001")
    assert "count(*) AS count FROM users WHERE is_active = 1" in gold_sql_1
    gold_res_1 = get_gold_results(mock_spider2_env, "local_001")
    assert len(gold_res_1) == 1
    assert gold_res_1[0] == [{"count": 2}]

    # Existing instance 2
    gold_sql_2 = get_gold_sql(mock_spider2_env, "local_002")
    assert "max(total)" in gold_sql_2
    gold_res_2 = get_gold_results(mock_spider2_env, "local_002")
    assert len(gold_res_2) == 1
    assert gold_res_2[0] == [{"max_total": 300.0}]

    # Non-existent instance returns empty
    assert get_gold_sql(mock_spider2_env, "non_existent") == ""
    assert get_gold_results(mock_spider2_env, "non_existent") == []


def test_dialect_and_criteria_filtering(mock_spider2_env: Path) -> None:
    """Verifies filtering examples by dialect, database ID, and question substring."""
    all_examples = load_spider2_examples(mock_spider2_env)

    # Dialect filtering
    sqlite_ex = [ex for ex in all_examples if get_instance_dialect(ex["instance_id"]) == "sqlite"]
    assert len(sqlite_ex) == 2
    bq_ex = [ex for ex in all_examples if get_instance_dialect(ex["instance_id"]) == "bigquery"]
    assert len(bq_ex) == 1
    assert bq_ex[0]["instance_id"] == "bq_001"
    sf_ex = [ex for ex in all_examples if get_instance_dialect(ex["instance_id"]) == "snowflake"]
    assert len(sf_ex) == 1
    assert sf_ex[0]["instance_id"] == "sf_001"

    # DB filtering
    test_db_ex = [ex for ex in all_examples if ex.get("db") == "test_db"]
    assert len(test_db_ex) == 2

    # Question substring filtering
    revenue_ex = [ex for ex in all_examples if "revenue" in ex.get("question", "").lower()]
    assert len(revenue_ex) == 1
    assert revenue_ex[0]["instance_id"] == "sf_001"


# ==============================================================================
# 2. CLI ARGUMENTS & SECURITY / SECRET-LEAK GUARDRAILS
# ==============================================================================

def test_cli_argument_parsing_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies default values for CLI flags."""
    monkeypatch.setattr("sys.argv", ["test_spider2_eval.py"])
    args = parse_arguments()

    assert args.infer_fks == "off"
    assert args.with_graphs == "off"
    assert args.rag == "on"
    assert args.enrich_rag == "off"
    assert args.cot == "on"
    assert args.data_exploration == "on"
    assert args.exploration_selector == "off"
    assert args.use_evidence == "on"
    assert args.dialect == "sqlite"
    assert args.timeout == 30
    assert args.dry_run is False
    assert args.log == "on"
    assert args.seed == 42


def test_cli_argument_parsing_unified_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that unified configuration flags are correctly accepted and mapped."""
    monkeypatch.setattr(
        "sys.argv",
        [
            "test_spider2_eval.py",
            "--temperature", "0.2",
            "--max-tokens", "1024",
            "--infer-fks",
            "--with-graphs",
            "--use-evidence", "off",
            "--dialect", "all",
            "--timeout", "45",
        ],
    )
    args = parse_arguments()

    assert args.temperature == 0.2
    assert args.max_tokens == 1024
    assert args.infer_fks == "on"
    assert args.with_graphs == "on"
    assert args.use_evidence == "off"
    assert args.dialect == "all"
    assert args.timeout == 45


def test_cli_argument_parsing_dialect_options(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies parsing of dialect options: sqlite, bigquery, snowflake, all."""
    for dialect_val in ["sqlite", "bigquery", "snowflake", "all"]:
        monkeypatch.setattr("sys.argv", ["test_spider2_eval.py", "--dialect", dialect_val])
        args = parse_arguments()
        assert args.dialect == dialect_val


def test_cli_argument_parsing_rejeita_schemacrawler(monkeypatch: pytest.MonkeyPatch) -> None:
    """SchemaCrawler foi removido da biblioteca: a flag antiga não existe mais."""
    monkeypatch.setattr("sys.argv", ["test_spider2_eval.py", "--schemacrawler", "on"])
    with pytest.raises(SystemExit):
        parse_arguments()


def test_validate_api_key_env_valid() -> None:
    """Verifies valid environment variable names pass validation."""
    assert _validate_api_key_env("OPENAI_API_KEY") == "OPENAI_API_KEY"
    assert _validate_api_key_env("GOOGLE_API_KEY") == "GOOGLE_API_KEY"
    assert _validate_api_key_env("CUSTOM_LLM_KEY_2") == "CUSTOM_LLM_KEY_2"
    assert _validate_api_key_env(None) is None


def test_validate_api_key_env_rejects_raw_keys_without_leak() -> None:
    """
    SECURITY: Verifies that passing actual raw secret tokens to --api-key-env is rejected
    and that the raw secret value is NEVER leaked into the exception error message.
    """
    raw_secrets = [
        "sk-proj-1234567890abcdef1234567890",
        "AIzaSyDUMMY_GOOGLE_API_KEY_SECRET",
        "ghp_SecretGitHubPersonalAccessToken",
        "bearer super_secret_token_value",
        "lowercase_not_allowed",
    ]

    for secret in raw_secrets:
        with pytest.raises(argparse.ArgumentTypeError) as exc_info:
            _validate_api_key_env(secret)

        err_msg = str(exc_info.value)
        # Secret value must NEVER appear in the error message
        assert secret not in err_msg, f"Security leak detected: secret '{secret}' found in '{err_msg}'"
        assert "Invalid environment variable name for --api-key-env" in err_msg


def test_model_kwarg_valid() -> None:
    """Verifies parsing of standard key=value pairs into typed arguments."""
    k1, v1 = _model_kwarg("temperature=0.7")
    assert k1 == "temperature"
    assert v1 == 0.7

    k2, v2 = _model_kwarg("top_p=0.95")
    assert k2 == "top_p"
    assert v2 == 0.95

    k3, v3 = _model_kwarg("streaming=true")
    assert k3 == "streaming"
    assert v3 is True


def test_model_kwarg_rejects_sensitive_keys_without_leak() -> None:
    """
    SECURITY: Verifies that passing sensitive configuration keys via --model-kwargs
    is blocked to prevent leakage in process tables or CLI logs.
    """
    sensitive_items = [
        "api_key=sk-secret-123456",
        "access_token=ghp_secret987654",
        "secret=my_master_secret",
        "password=super_secret_db_pass",
        "auth_credential=secret_hash_value",
    ]

    for item in sensitive_items:
        with pytest.raises(argparse.ArgumentTypeError) as exc_info:
            _model_kwarg(item)

        err_msg = str(exc_info.value)
        # Value must not be leaked
        raw_val = item.split("=")[1]
        assert raw_val not in err_msg, f"Security leak: '{raw_val}' echoed in '{err_msg}'"
        assert "Sensitive configuration parameters" in err_msg


def test_mask_db_url() -> None:
    """Verifies that database connection strings have passwords redacted."""
    assert mask_db_url("") == ""
    assert (
        mask_db_url("postgresql://admin:super_secret_pass@db.example.com:5432/analytics")
        == "postgresql://admin:***@db.example.com:5432/analytics"
    )
    assert (
        mask_db_url("mysql+pymysql://root:pass123@127.0.0.1:3306/spider")
        == "mysql+pymysql://root:***@127.0.0.1:3306/spider"
    )
    # URL without password should remain unchanged
    assert (
        mask_db_url("sqlite:///path/to/db.sqlite")
        == "sqlite:///path/to/db.sqlite"
    )


def test_logger_sanitize_kwargs() -> None:
    """Verifies that _sanitize_kwargs redacts sensitive keys and masks URLs."""
    raw_kwargs = {
        "temperature": 0.0,
        "api_key": "sk-secret-key-to-redact",
        "user_token": "token-to-redact",
        "db_url": "postgresql://usr:secret_pwd@localhost:5432/test",
    }
    sanitized = _sanitize_kwargs(raw_kwargs)

    assert sanitized["temperature"] == 0.0
    assert sanitized["api_key"] == "***REDACTED***"
    assert sanitized["user_token"] == "***REDACTED***"
    assert sanitized["db_url"] == "postgresql://usr:***@localhost:5432/test"


def test_resolve_model_and_provider() -> None:
    """Verifies provider and model name resolution."""
    p1, m1 = resolve_model_and_provider("openai/gpt-4o-mini")
    assert p1 == "openai"
    assert "gpt-4o-mini" in m1

    p2, m2 = resolve_model_and_provider("google/gemini-2.5-flash")
    assert p2 == "google"
    assert "gemini" in m2

    p3, m3 = resolve_model_and_provider("gemini-2.5-pro")
    assert p3 == "google"
    assert m3 == "gemini-2.5-pro"


# ==============================================================================
# 3. QUERY EXECUTORS (SQLite, SQLAlchemy, Multi-DBMS Factory)
# ==============================================================================

def test_spider_sqlite_executor_read_and_safe_math(mock_spider2_env: Path) -> None:
    """Verifies SQLite query execution and safe math function injection."""
    sqlite_dir = mock_spider2_env / "resource" / "databases" / "spider2-localdb"
    executor = SpiderSQLiteExecutor(database_dir=sqlite_dir, timeout_seconds=10)

    # 1. Standard read query
    res = executor.execute_query("test_db", "SELECT count(*) AS total_users FROM users;")
    assert res["success"] is True
    assert res["results"] == [{"total_users": 3}]
    assert res["error"] == ""
    assert res["row_count"] == 1
    assert res["time_ms"] > 0

    # 2. Safe math functions
    math_query = """
    SELECT
        SQRT(16) AS sqrt_val,
        SQRT(-4) AS sqrt_neg,
        POWER(2, 3) AS pow_val,
        LOG10(100) AS log10_val,
        LN(1) AS ln_val,
        LN(0) AS ln_zero,
        EXP(0) AS exp_val,
        CEIL(4.2) AS ceil_val,
        FLOOR(4.8) AS floor_val,
        ROUND(4.567, 2) AS round_val,
        SIGN(-42) AS sign_neg,
        ABS(-10) AS abs_val,
        PI() AS pi_val
    ;
    """
    math_res = executor.execute_query("test_db", math_query)
    assert math_res["success"] is True
    row = math_res["results"][0]

    assert row["sqrt_val"] == 4.0
    assert row["sqrt_neg"] is None  # Safe math returns NULL for invalid domain
    assert row["pow_val"] == 8.0
    assert row["log10_val"] == 2.0
    assert row["ln_val"] == 0.0
    assert row["ln_zero"] is None   # Safe math returns NULL for <= 0
    assert row["exp_val"] == 1.0
    assert row["ceil_val"] == 5
    assert row["floor_val"] == 4
    assert row["round_val"] == 4.57
    assert row["sign_neg"] == -1
    assert row["abs_val"] == 10
    assert round(row["pi_val"], 2) == 3.14


def test_spider_sqlite_executor_mutation_blocking(mock_spider2_env: Path) -> None:
    """
    SECURITY: Verifies that destructive mutation statements (DROP, INSERT, UPDATE,
    DELETE, ALTER, TRUNCATE, CREATE) are strictly blocked from execution.
    """
    sqlite_dir = mock_spider2_env / "resource" / "databases" / "spider2-localdb"
    executor = SpiderSQLiteExecutor(database_dir=sqlite_dir)

    mutations = [
        "DROP TABLE users;",
        "INSERT INTO users (id, name, is_active) VALUES (99, 'Hacker', 1);",
        "UPDATE users SET is_active = 0 WHERE id = 1;",
        "DELETE FROM users WHERE id = 2;",
        "ALTER TABLE users ADD COLUMN compromised TEXT;",
        "CREATE TABLE backdoor (id INT);",
    ]

    for sql in mutations:
        res = executor.execute_query("test_db", sql)
        assert res["success"] is False
        assert "Permission Error" in res["error"] or "blocked" in res["error"].lower()

    # Verify that database remained immutable
    verify_res = executor.execute_query("test_db", "SELECT count(*) AS cnt FROM users;")
    assert verify_res["success"] is True
    assert verify_res["results"] == [{"cnt": 3}]


def test_spider_sqlite_executor_timeout(mock_spider2_env: Path) -> None:
    """Verifies that an excessively slow query is interrupted by watchdog timer."""
    sqlite_dir = mock_spider2_env / "resource" / "databases" / "spider2-localdb"
    # Set short timeout of 1 second
    executor = SpiderSQLiteExecutor(database_dir=sqlite_dir, timeout_seconds=1)

    # Infinite recursive CTE to simulate runaway query
    runaway_sql = """
    WITH RECURSIVE runaway(x) AS (
        SELECT 1
        UNION ALL
        SELECT x + 1 FROM runaway
    )
    SELECT count(*) FROM runaway;
    """
    start_t = time.time()
    res = executor.execute_query("test_db", runaway_sql)
    elapsed = time.time() - start_t

    assert res["success"] is False
    assert "timed out" in res["error"].lower()
    assert elapsed < 5.0  # Must terminate promptly


def test_spider_sqlite_executor_database_not_found(tmp_path: Path) -> None:
    """Verifies clean error handling when the requested database does not exist."""
    executor = SpiderSQLiteExecutor(database_dir=tmp_path)
    res = executor.execute_query("missing_db", "SELECT 1;")
    assert res["success"] is False
    assert "Database not found" in res["error"]


def test_get_spider_executor_factory(mock_spider2_env: Path) -> None:
    """Verifies factory resolution for multi-DBMS executors and backward-compatibility aliases."""
    sqlite_dir = mock_spider2_env / "resource" / "databases" / "spider2-localdb"

    # SQLite resolution
    sq_exec = get_spider_executor("sqlite", database_dir=sqlite_dir)
    assert isinstance(sq_exec, BaseSpiderQueryExecutor)
    assert isinstance(sq_exec, SpiderSQLiteExecutor)

    # Aliases
    assert issubclass(SpiderQueryExecutor, BaseSpiderQueryExecutor)
    assert issubclass(Spider2QueryExecutor, SpiderSQLiteExecutor)

    # BigQuery resolution
    bq_exec = get_spider_executor("bigquery")
    assert isinstance(bq_exec, SpiderBigQueryExecutor)

    # Snowflake resolution
    sf_exec = get_spider_executor("snowflake")
    assert isinstance(sf_exec, SpiderSnowflakeExecutor)

    # SQLAlchemy generic resolution
    pg_exec = get_spider_executor("postgresql", db_url="postgresql://usr:pwd@localhost:5432/db")
    assert isinstance(pg_exec, SpiderSQLAlchemyExecutor)


def test_spider_sqlalchemy_executor_read_only_and_masking() -> None:
    """Verifies SpiderSQLAlchemyExecutor parameter validation, credential masking, and read-only enforcement."""
    raw_url = "postgresql://my_user:secret_pwd@localhost:5432/analytics"
    executor = SpiderSQLAlchemyExecutor(connection_url=raw_url)

    # Mutation query blocked before connection attempt
    res = executor.execute_query("any_db", "DROP TABLE critical_data;")
    assert res["success"] is False
    assert "destructive" in res["error"].lower() or "prohibited" in res["error"].lower() or "permission" in res["error"].lower()

    # Connection failure should mask or never expose the secret password in error output
    conn_fail_res = executor.execute_query("any_db", "SELECT 1;")
    assert conn_fail_res["success"] is False
    assert "secret_pwd" not in conn_fail_res["error"]


# ==============================================================================
# 4. SPIDER EXECUTION LOGGER LIFECYCLE & FORMATTING
# ==============================================================================

def test_spider_execution_logger_lifecycle(tmp_path: Path) -> None:
    """Verifies SpiderExecutionLogger creation, header writing, and clean closure."""
    log_file = tmp_path / "test_run.log"
    config_info = {
        "model": "gpt-4o-mini",
        "provider": "openai",
        "dialect": "sqlite",
        "sample_size": 2,
        "seed": 42,
        "timeout": 30,
        "use_rag": True,
        "use_cot": True,
        "with_graphs": False,
        "model_kwargs": {"temperature": 0.0, "api_key": "should_be_redacted"},
    }

    with SpiderExecutionLogger(output_dir=tmp_path, log_file=log_file, enabled=True) as logger:
        logger.log_run_header(config_info)

    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "TEXTTOINSIGHT - SPIDER 2.0 BENCHMARK EXECUTION LOG" in content
    assert "Model:              gpt-4o-mini" in content
    assert "Provider:           openai" in content
    assert "Dialect:            sqlite" in content
    assert "SchemaCrawler" not in content
    assert "should_be_redacted" not in content
    assert "***REDACTED***" in content


def test_spider_execution_logger_tee_stream(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Verifies that capture_trace captures output while simultaneously allowing terminal print."""
    with SpiderExecutionLogger(output_dir=tmp_path, enabled=True) as logger:
        with logger.capture_trace() as get_trace:
            print("Step 1: Planner node executed.")
            print("Step 2: Code Agent generated SQL.")
            trace_result = get_trace()

    # Captured trace buffer contains printed text
    assert "Step 1: Planner node executed." in trace_result
    assert "Step 2: Code Agent generated SQL." in trace_result

    # Standard stdout also received the prints
    captured = capsys.readouterr()
    assert "Step 1: Planner node executed." in captured.out
    assert "Step 2: Code Agent generated SQL." in captured.out


def test_spider_execution_logger_log_query_with_tokens_by_node(tmp_path: Path) -> None:
    """Verifies that log_query writes instance details, [TOKENS BY NODE], and comparison results."""
    log_file = tmp_path / "query_test.log"
    tokens_by_node = {
        "planner": {"input": 0, "output": 0, "total": 0, "calls": 0},
        "rag_enrichment": {"input": 500, "output": 100, "total": 600, "calls": 1},
        "exploration_selector": {"input": 200, "output": 30, "total": 230, "calls": 1},
        "code_agent": {"input": 1500, "output": 250, "total": 1750, "calls": 2},
        "graph_router": {"input": 0, "output": 0, "total": 0, "calls": 0},
        "graph_generator": {"input": 0, "output": 0, "total": 0, "calls": 0},
        "response": {"input": 300, "output": 50, "total": 350, "calls": 1},
    }

    agent_result = {
        "sql_gerada": "SELECT count(*) FROM users WHERE is_active = 1;",
        "raciocinio_agente": "Filtering active users where is_active = 1.",
        "ultimo_prompt": "Generate SQL for active users.",
        "tokens_input": 2500,
        "tokens_output": 430,
        "tokens_total": 2930,
        "tentativas_loop": 1,
        "historico_tentativas": [],
    }

    with SpiderExecutionLogger(output_dir=tmp_path, log_file=log_file, enabled=True) as logger:
        logger.log_query(
            instance_id="local_001",
            db_id="test_db",
            dialect="sqlite",
            question="How many active users are registered?",
            external_knowledge="Active users have is_active = 1",
            gold_sql="SELECT count(*) FROM users WHERE is_active = 1;",
            agent_result=agent_result,
            match_success=True,
            similarity_score=1.0,
            time_ms=125.0,
            gold_results=[{"count": 2}],
            agent_results=[{"count": 2}],
            captured_trace="Trace from node execution",
            precision=1.0,
            recall=1.0,
            f1=1.0,
            tokens_by_node=tokens_by_node,
        )

    content = log_file.read_text(encoding="utf-8")
    assert "=== [START INSTANCE: local_001 | DB: test_db | Dialect: sqlite] ===" in content
    assert "[QUESTION] How many active users are registered?" in content
    assert "--- [TOKENS BY NODE] ---" in content
    assert "code_agent" in content
    assert "rag_enrichment" in content
    assert "[EX MATCH (Execution Accuracy)]:" in content
    assert "TRUE" in content
    assert "F1: 1.0000" in content
    assert "=== [END INSTANCE: local_001] ===" in content


def test_spider_execution_logger_footer_and_disabled(tmp_path: Path) -> None:
    """Verifies summary footer writing and disabled logger behavior."""
    # 1. Summary footer
    log_file = tmp_path / "footer_test.log"
    summary = {
        "total_perguntas": 2,
        "perguntas_aprovadas": 2,
        "taxa_aprovacao": 1.0,
        "perguntas_1a_tentativa": 2,
        "taxa_1a_tentativa": 1.0,
        "avg_f1": 1.0,
        "similarity_media": 1.0,
        "tokens": {"total_all": 5860, "total_input": 5000, "total_output": 860},
        "node_tokens": {
            "code_agent": {"total_all": 3500, "avg_total_per_question": 1750.0, "calls": 4},
            "response": {"total_all": 700, "avg_total_per_question": 350.0, "calls": 2},
        },
    }

    with SpiderExecutionLogger(output_dir=tmp_path, log_file=log_file, enabled=True) as logger:
        logger.log_run_footer(summary, total_elapsed=4.5)

    content = log_file.read_text(encoding="utf-8")
    assert "SPIDER 2.0 EVALUATION RUN SUMMARY" in content
    assert "Execution Accuracy (Final EX):      2/2 (100.00%)" in content
    assert "NODE-LEVEL TOKEN CONSUMPTION SUMMARY" in content
    assert "code_agent" in content

    # 2. Disabled logger does not create file
    disabled_log = tmp_path / "disabled.log"
    with SpiderExecutionLogger(output_dir=tmp_path, log_file=disabled_log, enabled=False) as dis_logger:
        dis_logger.log_run_header({})
        dis_logger.log_run_footer(summary, total_elapsed=1.0)

    assert not disabled_log.exists()


# ==============================================================================
# 5. CSV REPORTER & NODE-LEVEL TOKEN AGGREGATION
# ==============================================================================

def test_csv_reporter_headers_and_row_persistence(tmp_path: Path) -> None:
    """Verifies that CSVReporter persists the full 24-column schema including tokens_by_node."""
    csv_file = tmp_path / "spider_eval.csv"
    reporter = CSVReporter(csv_file)

    assert len(reporter.HEADERS) == 24
    assert reporter.HEADERS[-1] == "tokens_by_node"

    tokens_by_node = {
        "code_agent": {"input": 1000, "output": 200, "total": 1200, "calls": 1},
        "response": {"input": 300, "output": 50, "total": 350, "calls": 1},
    }

    row = build_comparison_row(
        id_exemplo="local_001",
        tentativa_numero=1,
        db_id="test_db",
        pergunta="How many active users?",
        query_ouro="SELECT count(*) FROM users WHERE is_active = 1;",
        query_agente="SELECT count(*) FROM users WHERE is_active = 1;",
        tempo_agente_ms=120.0,
        veredito_critico="aprovado",
        feedback_critico="",
        erro_execucao="",
        resultado_exato_match=True,
        similarity_score=1.0,
        resultado_f1=1.0,
        resultado_precision=1.0,
        resultado_recall=1.0,
        tokens_input=1300,
        tokens_output=250,
        tokens_total=1550,
        viz_acionado=False,
        viz_sucesso=False,
        tokens_by_node=tokens_by_node,
    )

    reporter.append_row(row)

    # Read back and verify CSV content
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        saved_rows = list(reader)

    assert len(saved_rows) == 1
    saved = saved_rows[0]
    assert saved["id_exemplo"] == "local_001"
    assert saved["resultado_exato_match"] == "True"
    assert saved["tokens_total"] == "1550"

    # Verify JSON deserialization of tokens_by_node
    parsed_tbn = json.loads(saved["tokens_by_node"])
    assert parsed_tbn["code_agent"]["total"] == 1200
    assert parsed_tbn["response"]["calls"] == 1


def test_csv_reporter_append_row_missing_key_raises(tmp_path: Path) -> None:
    """Verifies that append_row validates row integrity against HEADERS."""
    csv_file = tmp_path / "bad.csv"
    reporter = CSVReporter(csv_file)
    with pytest.raises(ValueError, match="Missing required CSV keys"):
        reporter.append_row({"id_exemplo": "local_001"})


def test_csv_reporter_summary_node_tokens_aggregation(tmp_path: Path) -> None:
    """Verifies that generate_summary aggregates accuracy and per-node token averages."""
    csv_file = tmp_path / "summary_test.csv"
    reporter = CSVReporter(csv_file)

    tbn1 = {
        "exploration_selector": {"input": 100, "output": 20, "total": 120, "calls": 1},
        "code_agent": {"input": 1000, "output": 200, "total": 1200, "calls": 2},
        "response": {"input": 300, "output": 50, "total": 350, "calls": 1},
    }
    tbn2 = {
        "exploration_selector": {"input": 200, "output": 40, "total": 240, "calls": 1},
        "code_agent": {"input": 2000, "output": 300, "total": 2300, "calls": 2},
        "response": {"input": 400, "output": 70, "total": 470, "calls": 1},
    }

    rows = [
        build_comparison_row(
            id_exemplo="local_001",
            tentativa_numero=1,
            db_id="test_db",
            pergunta="Question 1",
            query_ouro="SELECT 1;",
            query_agente="SELECT 1;",
            tempo_agente_ms=100.0,
            veredito_critico="aprovado",
            feedback_critico="",
            erro_execucao="",
            resultado_exato_match=True,
            similarity_score=1.0,
            resultado_f1=1.0,
            resultado_precision=1.0,
            resultado_recall=1.0,
            tokens_input=1400,
            tokens_output=270,
            tokens_total=1670,
            tokens_by_node=tbn1,
        ),
        build_comparison_row(
            id_exemplo="local_002",
            tentativa_numero=1,
            db_id="test_db",
            pergunta="Question 2",
            query_ouro="SELECT 2;",
            query_agente="SELECT 2;",
            tempo_agente_ms=200.0,
            veredito_critico="aprovado",
            feedback_critico="",
            erro_execucao="",
            resultado_exato_match=True,
            similarity_score=1.0,
            resultado_f1=1.0,
            resultado_precision=1.0,
            resultado_recall=1.0,
            tokens_input=2600,
            tokens_output=410,
            tokens_total=3010,
            tokens_by_node=tbn2,
        ),
    ]

    summary = reporter.generate_summary(rows)

    assert summary["total_perguntas"] == 2
    assert summary["taxa_aprovacao"] == 1.0
    assert summary["taxa_1a_tentativa"] == 1.0

    node_tokens = summary["node_tokens"]
    # code_agent: 1200 + 2300 = 3500 total, avg = 1750.0, calls = 4
    assert node_tokens["code_agent"]["total_all"] == 3500
    assert node_tokens["code_agent"]["avg_total_per_question"] == 1750.0
    assert node_tokens["code_agent"]["calls"] == 4

    # exploration_selector: 120 + 240 = 360 total, avg = 180.0, calls = 2
    assert node_tokens["exploration_selector"]["total_all"] == 360
    assert node_tokens["exploration_selector"]["avg_total_per_question"] == 180.0
    assert node_tokens["exploration_selector"]["calls"] == 2


# ==============================================================================
# 6. END-TO-END CLI DRY-RUN SMOKE TESTS
# ==============================================================================

def test_cli_orchestrator_dry_run_full(mock_spider2_env: Path, tmp_path: Path) -> None:
    """
    Executes scripts/test_spider2_eval.py in --dry-run mode and verifies that all 5
    required artifact files/folders are generated:
    1. .csv (24 columns with tokens_by_node)
    2. .md (Executive report in English with Node-Level Token Consumption table)
    3. .log (Detailed execution trace with [TOKENS BY NODE] and Tee output)
    4. _empirico.md (Empirical analysis report)
    5. empirico/ (Folder containing empirical charts and tables)
    """
    run_dir = tmp_path / "spider2_run"
    sqlite_dir = mock_spider2_env / "resource" / "databases" / "spider2-localdb"

    cmd = [
        sys.executable,
        "scripts/test_spider2_eval.py",
        "--data-dir", str(mock_spider2_env),
        "--sqlite-dir", str(sqlite_dir),
        "--sample-size", "2",
        "--seed", "42",
        "--dry-run",
        "--output", str(run_dir),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"CLI orchestrator failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    # 1. Verify CSV artifact
    csv_files = list(run_dir.glob("spider2_eval_*.csv"))
    assert len(csv_files) == 1, f"Expected 1 CSV, found {len(csv_files)}"
    csv_path = csv_files[0]

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 2
    assert "tokens_by_node" in rows[0]
    first_tbn = json.loads(rows[0]["tokens_by_node"])
    assert "code_agent" in first_tbn
    assert first_tbn["code_agent"]["calls"] >= 1

    # 2. Verify Markdown report artifact
    md_files = [f for f in run_dir.glob("spider2_eval_*.md") if not f.name.endswith("_empirico.md")]
    assert len(md_files) == 1
    md_content = md_files[0].read_text(encoding="utf-8")
    assert "# Spider 2.0 Evaluation Report" in md_content
    assert "## Node-Level Token Consumption" in md_content
    assert "| `code_agent` |" in md_content

    # 3. Verify Log file artifact
    log_files = list(run_dir.glob("spider2_eval_*.log"))
    assert len(log_files) == 1
    log_content = log_files[0].read_text(encoding="utf-8")
    assert "TEXTTOINSIGHT - SPIDER 2.0 BENCHMARK EXECUTION LOG" in log_content
    assert "--- [TOKENS BY NODE] ---" in log_content
    assert "NODE-LEVEL TOKEN CONSUMPTION SUMMARY" in log_content

    # 4. Verify Empirical Markdown report
    empirico_md_files = list(run_dir.glob("*_empirico.md"))
    assert len(empirico_md_files) == 1

    # 5. Verify Empirical output directory
    empirico_dirs = [d for d in run_dir.iterdir() if d.is_dir() and d.name == "empirico"]
    assert len(empirico_dirs) == 1


def test_cli_orchestrator_filter_dry_run(mock_spider2_env: Path, tmp_path: Path) -> None:
    """Verifies that --question-filter active in dry-run filters instances accurately."""
    run_dir = tmp_path / "filter_run"
    sqlite_dir = mock_spider2_env / "resource" / "databases" / "spider2-localdb"

    cmd = [
        sys.executable,
        "scripts/test_spider2_eval.py",
        "--data-dir", str(mock_spider2_env),
        "--sqlite-dir", str(sqlite_dir),
        "--question-filter", "active",
        "--dry-run",
        "--output", str(run_dir),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"CLI orchestrator failed:\n{result.stderr}"
    assert "Filtered by question substring: 1 instances" in result.stdout


def test_cli_orchestrator_security_rejections_cli(mock_spider2_env: Path, tmp_path: Path) -> None:
    """
    SECURITY: Verifies that passing raw secrets via CLI flags triggers immediate rejection
    and that the secret values NEVER appear in process output or error messages.
    """
    sqlite_dir = mock_spider2_env / "resource" / "databases" / "spider2-localdb"
    secret_key = "sk-super-secret-key-1234567890"

    # 1. Raw secret in --api-key-env
    cmd1 = [
        sys.executable,
        "scripts/test_spider2_eval.py",
        "--data-dir", str(mock_spider2_env),
        "--sqlite-dir", str(sqlite_dir),
        "--api-key-env", secret_key,
        "--dry-run",
    ]
    res1 = subprocess.run(cmd1, capture_output=True, text=True)
    assert res1.returncode != 0
    # Must NOT echo the secret key
    assert secret_key not in res1.stdout
    assert secret_key not in res1.stderr
    assert "Invalid environment variable name for --api-key-env" in res1.stderr

    # 2. Sensitive keyword in --model-kwargs
    sensitive_val = "secret_password_token_999"
    cmd2 = [
        sys.executable,
        "scripts/test_spider2_eval.py",
        "--data-dir", str(mock_spider2_env),
        "--sqlite-dir", str(sqlite_dir),
        "--model-kwargs", f"password={sensitive_val}",
        "--dry-run",
    ]
    res2 = subprocess.run(cmd2, capture_output=True, text=True)
    assert res2.returncode != 0
    assert sensitive_val not in res2.stdout
    assert sensitive_val not in res2.stderr
    assert "Sensitive configuration parameters" in res2.stderr
