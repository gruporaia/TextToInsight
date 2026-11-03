"""
src/spider/query_executor.py

Multi-DBMS query execution architecture for the Spider 2.0 benchmark.

Provides:
- BaseSpiderQueryExecutor: Abstract base class defining the execution contract.
- SpiderSQLiteExecutor: Safe SQLite execution with mutation blocking, timeouts, and safe math functions.
- SpiderQueryExecutor / Spider2QueryExecutor: Backward-compatible aliases and subclasses.
- SpiderBigQueryExecutor: Google Cloud BigQuery execution adapter.
- SpiderSnowflakeExecutor: Snowflake execution adapter.
- SpiderSQLAlchemyExecutor: Generic SQLAlchemy execution adapter (PostgreSQL, MySQL, etc.).
- get_spider_executor: Factory function resolving the appropriate executor for a given dialect.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import math
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any


# Regular expression pattern to detect destructive SQL write/mutation statements.
# \b ensures word-boundary matching to prevent false positives (e.g. column named 'inserted_at').
MUTATION_PATTERN = re.compile(
    r"\b(DROP|INSERT|UPDATE|DELETE|ALTER|TRUNCATE|REPLACE|CREATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


def mask_db_url(url: str) -> str:
    """Masks credentials in a database URI string for secure logging and reporting."""
    if not url:
        return ""
    # Masks ://username:password@host into ://username:***@host
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)


# ==============================================================================
# SAFE MATH WRAPPERS
# Standard RDBMS engines (Postgres/MySQL) evaluate invalid math (e.g. SQRT(-1) or
# LOG(0)) to NULL. These safe wrappers replicate standard database semantics in SQLite.
# ==============================================================================

def _safe_sqrt(x: Any) -> float | None:
    """Computes square root safely; returns None (SQL NULL) for negative numbers or non-numerics."""
    if x is None:
        return None
    try:
        val = float(x)
        return math.sqrt(val) if val >= 0 else None
    except (ValueError, TypeError):
        return None


def _safe_pow(base: Any, exp: Any) -> float | None:
    """Computes base^exp safely; returns None on domain error or arithmetic overflow."""
    if base is None or exp is None:
        return None
    try:
        return math.pow(float(base), float(exp))
    except (ValueError, TypeError, OverflowError):
        return None


def _safe_log(x: Any) -> float | None:
    """Computes natural logarithm (ln); returns None if x <= 0 or invalid."""
    if x is None:
        return None
    try:
        val = float(x)
        return math.log(val) if val > 0 else None
    except (ValueError, TypeError):
        return None


def _safe_log10(x: Any) -> float | None:
    """Computes base-10 logarithm; returns None if x <= 0 or invalid."""
    if x is None:
        return None
    try:
        val = float(x)
        return math.log10(val) if val > 0 else None
    except (ValueError, TypeError):
        return None


def _safe_exp(x: Any) -> float | None:
    """Computes e^x safely; returns None on overflow."""
    if x is None:
        return None
    try:
        return math.exp(float(x))
    except (ValueError, TypeError, OverflowError):
        return None


def _safe_ceil(x: Any) -> int | None:
    """Computes ceiling safely; returns integer or None."""
    if x is None:
        return None
    try:
        return math.ceil(float(x))
    except (ValueError, TypeError):
        return None


def _safe_floor(x: Any) -> int | None:
    """Computes floor safely; returns integer or None."""
    if x is None:
        return None
    try:
        return math.floor(float(x))
    except (ValueError, TypeError):
        return None


def _safe_mod(a: Any, b: Any) -> float | None:
    """Computes modulus (a % b); returns None if b is 0."""
    if a is None or b is None:
        return None
    try:
        divisor = float(b)
        return float(a) % divisor if divisor != 0 else None
    except (ValueError, TypeError):
        return None


def _safe_round(val: Any, decimals: Any = 0) -> float | None:
    """Rounds val to decimals; handles both 1-arg and 2-arg invocations."""
    if val is None:
        return None
    try:
        dec = int(decimals) if decimals is not None else 0
        return round(float(val), dec)
    except (ValueError, TypeError):
        return None


def _safe_trig(fn: Any, x: Any) -> float | None:
    """Computes trigonometric function safely."""
    if x is None:
        return None
    try:
        val = float(x)
        return fn(val)
    except (ValueError, TypeError, OverflowError):
        return None


def _verify_read_only_intent(sql: str) -> None:
    """
    Inspects SQL query text to block destructive write statements before execution.

    Raises:
        PermissionError: If any mutation keyword (DROP, DELETE, UPDATE, INSERT, etc.) is found.
    """
    clean_sql = re.sub(r"'(?:''|[^'])*'", "", sql)
    clean_sql = re.sub(r'"(?:""|[^"])*"', "", clean_sql)
    clean_sql = re.sub(r"--.*", "", clean_sql)
    clean_sql = re.sub(r"/\*.*?\*/", "", clean_sql, flags=re.DOTALL)
    match = MUTATION_PATTERN.search(clean_sql)
    if match:
        forbidden_word = match.group(0).upper()
        raise PermissionError(
            f"Destructive operation '{forbidden_word}' is prohibited in evaluation read-only mode."
        )


class BaseSpiderQueryExecutor(ABC):
    """
    Abstract base class defining the contract for Spider query execution across DBMS backends.
    """

    @abstractmethod
    def execute_query(
        self,
        db_id: str,
        sql: str,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """
        Executes a SQL query against the specified database.

        Parameters:
            db_id: Database identifier.
            sql: SQL query string to execute.
            timeout: Optional execution timeout in seconds.

        Returns:
            Dictionary with keys:
            - success: bool
            - results: list[dict[str, Any]]
            - row_count: int
            - error: str (empty string on success)
            - time_ms: float
        """
        raise NotImplementedError


class SpiderSQLiteExecutor(BaseSpiderQueryExecutor):
    """
    SQLite query executor for local Spider databases with timeout control,
    mutation blocking, and comprehensive safe math functions.
    """

    def __init__(
        self,
        database_dir: str | Path = "spider2-lite/resource/databases/spider2-localdb",
        timeout_seconds: int = 30,
    ) -> None:
        self.database_dir = Path(database_dir)
        self.timeout_seconds = timeout_seconds

    def get_db_path(self, db_id: str) -> Path:
        """
        Resolves the path to a SQLite database file, supporting both Spider 2.0 Lite
        (root-level databases) and Spider 1.0 (subfolder per database).

        Parameters:
            db_id: Database identifier.

        Returns:
            Resolved Path to the SQLite file.

        Raises:
            FileNotFoundError: If the database file is not found.
        """
        candidates = [
            self.database_dir / f"{db_id}.sqlite",
            self.database_dir / db_id / f"{db_id}.sqlite",
            self.database_dir / f"{db_id}.db",
            self.database_dir / db_id / f"{db_id}.db",
        ]
        for path in candidates:
            if path.exists():
                return path

        raise FileNotFoundError(
            f"Database '{db_id}' not found in directory '{self.database_dir}' "
            f"(checked: {', '.join(str(c) for c in candidates)})"
        )

    def _inject_math_functions(self, conn: sqlite3.Connection) -> None:
        """Injects trigonometric and analytical math functions into an active SQLite connection."""
        conn.create_function("PI", 0, lambda: math.pi)
        conn.create_function("SIN", 1, lambda x: _safe_trig(math.sin, x))
        conn.create_function("COS", 1, lambda x: _safe_trig(math.cos, x))
        conn.create_function("TAN", 1, lambda x: _safe_trig(math.tan, x))
        conn.create_function(
            "ASIN",
            1,
            lambda x: _safe_trig(math.asin, x) if (x is not None and _safe_trig(lambda v: -1 <= v <= 1, x)) else None,
        )
        conn.create_function(
            "ACOS",
            1,
            lambda x: _safe_trig(math.acos, x) if (x is not None and _safe_trig(lambda v: -1 <= v <= 1, x)) else None,
        )
        conn.create_function("ATAN", 1, lambda x: _safe_trig(math.atan, x))
        conn.create_function("RADIANS", 1, lambda x: _safe_trig(math.radians, x))
        conn.create_function("DEGREES", 1, lambda x: _safe_trig(math.degrees, x))

        conn.create_function("SQRT", 1, _safe_sqrt)
        conn.create_function("POWER", 2, _safe_pow)
        conn.create_function("POW", 2, _safe_pow)
        conn.create_function("EXP", 1, _safe_exp)
        conn.create_function("LN", 1, _safe_log)
        conn.create_function("LOG", 1, _safe_log10)
        conn.create_function("LOG10", 1, _safe_log10)
        conn.create_function("CEIL", 1, _safe_ceil)
        conn.create_function("CEILING", 1, _safe_ceil)
        conn.create_function("FLOOR", 1, _safe_floor)
        conn.create_function("ROUND", 1, lambda x: _safe_round(x, 0))
        conn.create_function("ROUND", 2, _safe_round)
        conn.create_function("SIGN", 1, lambda x: 1 if float(x) > 0 else (-1 if float(x) < 0 else 0) if x is not None else None)
        conn.create_function("ABS", 1, lambda x: abs(float(x)) if x is not None else None)
        conn.create_function("MOD", 2, _safe_mod)

    def execute_query(
        self,
        db_id: str,
        sql: str,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """
        Executes a SQL query in read-only mode against the SQLite database.

        Parameters:
            db_id: Database identifier.
            sql: SQL query string.
            timeout: Execution timeout in seconds (defaults to self.timeout_seconds).

        Returns:
            Dictionary with results, row count, error message, and execution time.
        """
        effective_timeout = timeout if timeout is not None else self.timeout_seconds
        start_time = time.time()
        conn: sqlite3.Connection | None = None
        timeout_timer: threading.Timer | None = None

        try:
            # 1. Defend against destructive queries
            _verify_read_only_intent(sql)

            # 2. Locate database file
            db_path = self.get_db_path(db_id)

            # 3. Connect in read-only mode using SQLite URI protocol
            connection_string = f"file:{db_path.resolve()}?mode=ro&uri=true"
            conn = sqlite3.connect(connection_string, timeout=float(effective_timeout), uri=True)
            conn.row_factory = sqlite3.Row

            # 4. Inject math functions
            self._inject_math_functions(conn)

            # 5. Arm watchdog timer to interrupt stalled queries
            timeout_timer = threading.Timer(float(effective_timeout), conn.interrupt)
            timeout_timer.start()

            cursor = conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()

            # 6. Convert rows to list of dictionaries
            results = [dict(row) for row in rows]
            elapsed_ms = (time.time() - start_time) * 1000.0

            return {
                "success": True,
                "results": results,
                "row_count": len(results),
                "error": "",
                "time_ms": elapsed_ms,
            }

        except sqlite3.OperationalError as e:
            elapsed_ms = (time.time() - start_time) * 1000.0
            error_str = str(e)
            if "interrupted" in error_str.lower():
                return {
                    "success": False,
                    "results": [],
                    "row_count": 0,
                    "error": f"Query execution timed out after {effective_timeout} seconds.",
                    "time_ms": elapsed_ms,
                }
            return {
                "success": False,
                "results": [],
                "row_count": 0,
                "error": f"SQLite OperationalError: {error_str}",
                "time_ms": elapsed_ms,
            }
        except PermissionError as e:
            elapsed_ms = (time.time() - start_time) * 1000.0
            return {
                "success": False,
                "results": [],
                "row_count": 0,
                "error": f"Permission Error: {str(e)}",
                "time_ms": elapsed_ms,
            }
        except FileNotFoundError as e:
            elapsed_ms = (time.time() - start_time) * 1000.0
            return {
                "success": False,
                "results": [],
                "row_count": 0,
                "error": f"Database not found: {str(e)}",
                "time_ms": elapsed_ms,
            }
        except sqlite3.Error as e:
            elapsed_ms = (time.time() - start_time) * 1000.0
            return {
                "success": False,
                "results": [],
                "row_count": 0,
                "error": f"SQLite Error: {str(e)}",
                "time_ms": elapsed_ms,
            }
        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000.0
            return {
                "success": False,
                "results": [],
                "row_count": 0,
                "error": f"Unexpected error: {str(e)}",
                "time_ms": elapsed_ms,
            }
        finally:
            if timeout_timer is not None:
                timeout_timer.cancel()
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


class SpiderQueryExecutor(SpiderSQLiteExecutor):
    """Backward-compatible alias for SpiderSQLiteExecutor (Spider 1.0 default path)."""

    def __init__(
        self,
        database_dir: str | Path = "data/spider_data/spider_data/database",
        timeout_seconds: int = 30,
    ) -> None:
        super().__init__(database_dir=database_dir, timeout_seconds=timeout_seconds)


class Spider2QueryExecutor(SpiderSQLiteExecutor):
    """Backward-compatible alias for SpiderSQLiteExecutor (Spider 2.0 localdb default path)."""

    def __init__(
        self,
        database_dir: str | Path = "spider2-lite/resource/databases/spider2-localdb",
        timeout_seconds: int = 30,
    ) -> None:
        super().__init__(database_dir=database_dir, timeout_seconds=timeout_seconds)


class SpiderBigQueryExecutor(BaseSpiderQueryExecutor):
    """
    Google Cloud BigQuery query executor for Spider 2.0 instances.
    """

    def __init__(
        self,
        project_id: str | None = None,
        credentials_path: str | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.project_id = project_id
        self.credentials_path = credentials_path
        self.timeout_seconds = timeout_seconds
        self._client = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from google.cloud import bigquery
        except ImportError as exc:
            raise ImportError(
                "BigQuery backend requires 'google-cloud-bigquery'. "
                "Install it via 'pip install google-cloud-bigquery'."
            ) from exc

        if self.credentials_path:
            from google.oauth2 import service_account
            credentials = service_account.Credentials.from_service_account_file(self.credentials_path)
            self._client = bigquery.Client(project=self.project_id, credentials=credentials)
        else:
            self._client = bigquery.Client(project=self.project_id)
        return self._client

    def execute_query(
        self,
        db_id: str,
        sql: str,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Executes a query on BigQuery and returns records as list[dict]."""
        effective_timeout = timeout if timeout is not None else self.timeout_seconds
        start_time = time.time()
        try:
            _verify_read_only_intent(sql)
            client = self._get_client()

            from google.cloud import bigquery
            job_config = bigquery.QueryJobConfig(
                default_dataset=f"{self.project_id}.{db_id}" if self.project_id and db_id else None
            )
            query_job = client.query(sql, job_config=job_config, timeout=effective_timeout)
            rows = query_job.result(timeout=effective_timeout)
            results = [dict(row.items()) for row in rows]
            elapsed_ms = (time.time() - start_time) * 1000.0

            return {
                "success": True,
                "results": results,
                "row_count": len(results),
                "error": "",
                "time_ms": elapsed_ms,
            }
        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000.0
            return {
                "success": False,
                "results": [],
                "row_count": 0,
                "error": f"BigQuery Error: {str(e)}",
                "time_ms": elapsed_ms,
            }


class SpiderSnowflakeExecutor(BaseSpiderQueryExecutor):
    """
    Snowflake query executor for Spider 2.0 instances.
    """

    def __init__(
        self,
        user: str | None = None,
        password: str | None = None,
        account: str | None = None,
        warehouse: str | None = None,
        database: str | None = None,
        schema: str | None = None,
        timeout_seconds: int = 30,
        **kwargs: Any,
    ) -> None:
        self.user = user
        self.password = password
        self.account = account
        self.warehouse = warehouse
        self.database = database
        self.schema = schema
        self.timeout_seconds = timeout_seconds
        self.extra_kwargs = kwargs

    def execute_query(
        self,
        db_id: str,
        sql: str,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Executes a query on Snowflake and returns records as list[dict]."""
        effective_timeout = timeout if timeout is not None else self.timeout_seconds
        start_time = time.time()
        try:
            _verify_read_only_intent(sql)
            try:
                import snowflake.connector
                from snowflake.connector import DictCursor
            except ImportError as exc:
                raise ImportError(
                    "Snowflake backend requires 'snowflake-connector-python'. "
                    "Install it via 'pip install snowflake-connector-python'."
                ) from exc

            db_to_use = self.database or db_id
            conn = snowflake.connector.connect(
                user=self.user,
                password=self.password,
                account=self.account,
                warehouse=self.warehouse,
                database=db_to_use,
                schema=self.schema,
                network_timeout=effective_timeout,
                **self.extra_kwargs,
            )
            try:
                with conn.cursor(DictCursor) as cur:
                    cur.execute(sql)
                    rows = cur.fetchall()
                    results = [dict(row) for row in rows]
            finally:
                conn.close()

            elapsed_ms = (time.time() - start_time) * 1000.0
            return {
                "success": True,
                "results": results,
                "row_count": len(results),
                "error": "",
                "time_ms": elapsed_ms,
            }
        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000.0
            return {
                "success": False,
                "results": [],
                "row_count": 0,
                "error": f"Snowflake Error: {str(e)}",
                "time_ms": elapsed_ms,
            }


class SpiderSQLAlchemyExecutor(BaseSpiderQueryExecutor):
    """
    Generic SQLAlchemy query executor (PostgreSQL, MySQL, SQLite, etc.) for Spider instances.
    """

    def __init__(
        self,
        connection_url: str | None = None,
        db_uri_template: str | None = None,
        timeout_seconds: int = 30,
        **engine_kwargs: Any,
    ) -> None:
        self.connection_url = connection_url
        self.db_uri_template = db_uri_template
        self.timeout_seconds = timeout_seconds
        self.engine_kwargs = engine_kwargs

    def _resolve_url(self, db_id: str) -> str:
        if self.connection_url:
            return self.connection_url
        if self.db_uri_template:
            return self.db_uri_template.format(db_id=db_id)
        raise ValueError("Either connection_url or db_uri_template must be provided to SpiderSQLAlchemyExecutor.")

    def execute_query(
        self,
        db_id: str,
        sql: str,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Executes a query via SQLAlchemy and returns records as list[dict]."""
        effective_timeout = timeout if timeout is not None else self.timeout_seconds
        start_time = time.time()
        try:
            _verify_read_only_intent(sql)
            try:
                from sqlalchemy import create_engine, text
            except ImportError as exc:
                raise ImportError(
                    "SQLAlchemy backend requires 'sqlalchemy'. "
                    "Install it via 'pip install sqlalchemy'."
                ) from exc

            raw_url = self._resolve_url(db_id)
            engine = create_engine(
                raw_url,
                execution_options={"timeout": effective_timeout},
                future=True,
                **self.engine_kwargs,
            )
            try:
                with engine.connect() as conn:
                    result_proxy = conn.execute(text(sql))
                    results = [dict(row._mapping) for row in result_proxy]
            finally:
                engine.dispose()

            elapsed_ms = (time.time() - start_time) * 1000.0
            return {
                "success": True,
                "results": results,
                "row_count": len(results),
                "error": "",
                "time_ms": elapsed_ms,
            }
        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000.0
            error_msg = mask_db_url(str(e))
            return {
                "success": False,
                "results": [],
                "row_count": 0,
                "error": f"SQLAlchemy Error: {error_msg}",
                "time_ms": elapsed_ms,
            }


def get_spider_executor(
    dialect: str = "sqlite",
    **kwargs: Any,
) -> BaseSpiderQueryExecutor:
    """
    Factory function resolving and instantiating the appropriate Spider query executor.

    Parameters:
        dialect: Dialect name ('sqlite', 'bigquery', 'snowflake', 'postgresql', 'mysql', 'sqlalchemy').
        **kwargs: Arguments forwarded to the executor constructor.

    Returns:
        Instance of BaseSpiderQueryExecutor subclass.

    Raises:
        ValueError: If dialect is unsupported.
    """
    normalized = dialect.lower().strip()
    if normalized in ("sqlite", "sqlite3"):
        return SpiderSQLiteExecutor(**kwargs)
    elif normalized in ("bigquery", "bq"):
        return SpiderBigQueryExecutor(**kwargs)
    elif normalized in ("snowflake", "sf"):
        return SpiderSnowflakeExecutor(**kwargs)
    elif normalized in ("sqlalchemy", "postgres", "postgresql", "mysql"):
        return SpiderSQLAlchemyExecutor(**kwargs)
    else:
        raise ValueError(
            f"Unsupported dialect '{dialect}'. Supported dialects: sqlite, bigquery, snowflake, sqlalchemy, postgresql, mysql"
        )
