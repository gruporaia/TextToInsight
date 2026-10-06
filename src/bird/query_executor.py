"""
src/bird/query_executor.py

Query execution module for the BIRD benchmark dataset.
Responsible for:
- Connecting to SQLite database files in read-only mode (?mode=ro&uri=true).
- Defending against destructive write queries (DROP, INSERT, UPDATE, DELETE).
- Registering Safe Math functions (SQRT, POW, LOG, CEIL, etc.) that return NULL on invalid inputs.
- Enforcing real query timeouts using threading.Timer with conn.interrupt().
- Protecting process memory via configurable maximum row ceiling (max_rows).
- Providing an extensible Strategy/Adapter design ready for SQLAlchemy backend integration.
"""

import math
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from src.bird.data_loader import get_database_path, get_database_uri


# Regular expression pattern to detect destructive SQL write/mutation statements.
# \b ensures word-boundary matching to prevent false positives (e.g. column named 'inserted_at').
MUTATION_PATTERN = re.compile(
    r"\b(DROP|INSERT|UPDATE|DELETE|ALTER|TRUNCATE|REPLACE|CREATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


# ==============================================================================
# SAFE MATH WRAPPERS
# In SQLite, user-defined functions that raise Python exceptions cause the entire
# query to abort with sqlite3.OperationalError.
# Standard RDBMS engines (Postgres/MySQL) evaluate invalid math (e.g. SQRT(-1) or
# LOG(0)) to NULL. These safe wrappers replicate standard database semantics.
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
    """Computes modulus (a % b); returns None if b is 0 (prevents ZeroDivisionError)."""
    if a is None or b is None:
        return None
    try:
        divisor = float(b)
        return float(a) % divisor if divisor != 0 else None
    except (ValueError, TypeError):
        return None


def _safe_round(val: Any, decimals: Any = 0) -> float | None:
    """Rounds val to decimals; handles both 1-arg and 2-arg SQLite round invocations."""
    if val is None:
        return None
    try:
        dec = int(decimals) if decimals is not None else 0
        return round(float(val), dec)
    except (ValueError, TypeError):
        return None


class BirdQueryExecutor:
    """
    SQL Query Executor for BIRD benchmark databases.
    
    Provides isolated, read-only execution with:
    - Real timeout cancellation using thread interrupts.
    - Safe math extensions matching PostgreSQL / MySQL semantics.
    - Defense-in-depth security preventing accidental data corruption.
    - Adapter pattern supporting native 'sqlite' and pluggable 'sqlalchemy' engines.
    """

    def __init__(
        self,
        data_dir: str | Path = "data/bird",
        timeout_seconds: int = 30,
        backend: str = "sqlite",
    ):
        """
        Initializes the query executor.

        Parameters:
            data_dir: Root directory of the BIRD dataset (e.g. 'data/bird').
            timeout_seconds: Default execution timeout in seconds.
            backend: Engine backend identifier ('sqlite' or 'sqlalchemy').
        """
        self.data_dir = Path(data_dir)
        self.timeout_seconds = timeout_seconds
        self.backend = backend.lower().strip()

    def _inject_math_functions(self, conn: sqlite3.Connection) -> None:
        """
        Registers analytical and trigonometric functions onto an active SQLite connection.
        Enables SQLite to seamlessly evaluate complex formulas common in BIRD gold queries.
        """
        # Trigonometric and basic geometry functions
        conn.create_function("PI", 0, lambda: math.pi)
        conn.create_function("SIN", 1, lambda x: math.sin(float(x)) if x is not None else None)
        conn.create_function("COS", 1, lambda x: math.cos(float(x)) if x is not None else None)
        conn.create_function("TAN", 1, lambda x: math.tan(float(x)) if x is not None else None)
        conn.create_function("ASIN", 1, lambda x: math.asin(float(x)) if x is not None and -1 <= float(x) <= 1 else None)
        conn.create_function("ACOS", 1, lambda x: math.acos(float(x)) if x is not None and -1 <= float(x) <= 1 else None)
        conn.create_function("ATAN", 1, lambda x: math.atan(float(x)) if x is not None else None)
        conn.create_function("RADIANS", 1, lambda x: math.radians(float(x)) if x is not None else None)
        conn.create_function("DEGREES", 1, lambda x: math.degrees(float(x)) if x is not None else None)

        # Power, root, and exponential functions
        conn.create_function("SQRT", 1, _safe_sqrt)
        conn.create_function("POWER", 2, _safe_pow)
        conn.create_function("POW", 2, _safe_pow)
        conn.create_function("EXP", 1, _safe_exp)

        # Logarithmic functions
        conn.create_function("LN", 1, _safe_log)
        conn.create_function("LOG", 1, _safe_log10)
        conn.create_function("LOG10", 1, _safe_log10)

        # Rounding and ceiling/floor
        conn.create_function("CEIL", 1, _safe_ceil)
        conn.create_function("CEILING", 1, _safe_ceil)
        conn.create_function("FLOOR", 1, _safe_floor)
        conn.create_function("ROUND", 1, lambda x: _safe_round(x, 0))
        conn.create_function("ROUND", 2, _safe_round)

        # Arithmetic helpers
        conn.create_function("ABS", 1, lambda x: abs(float(x)) if x is not None else None)
        conn.create_function("MOD", 2, _safe_mod)
        conn.create_function("SIGN", 1, lambda x: (1 if float(x) > 0 else (-1 if float(x) < 0 else 0)) if x is not None else None)

    def _verify_read_only_intent(self, sql: str) -> None:
        """
        Inspects SQL query text to block destructive write statements before execution.

        Raises:
            PermissionError: If any mutation keyword (DROP, DELETE, UPDATE, INSERT) is found.
        """
        match = MUTATION_PATTERN.search(sql)
        if match:
            forbidden_word = match.group(0).upper()
            raise PermissionError(
                f"Destructive operation '{forbidden_word}' is prohibited in evaluation read-only mode."
            )

    def _execute_sqlite(
        self,
        db_id: str,
        sql: str,
        timeout: int,
        max_rows: int | None,
    ) -> dict[str, Any]:
        """
        Executes a SQL query using Python's native sqlite3 driver with thread interrupt timeout.
        """
        start_time = time.time()
        conn: sqlite3.Connection | None = None
        timeout_timer: threading.Timer | None = None

        try:
            # 1. Defense-in-depth: scan query text for write operations
            self._verify_read_only_intent(sql)

            # 2. Resolve database path on local filesystem
            db_path = get_database_path(self.data_dir, db_id)

            # 3. Connect strictly in read-only mode using SQLite URI protocol
            connection_uri = f"file:{db_path.resolve()}?mode=ro&uri=true"
            conn = sqlite3.connect(connection_uri, uri=True, timeout=5.0)

            # 4. Inject safe math functions into SQLite connection
            self._inject_math_functions(conn)

            # 5. Arm background watchdog timer to call conn.interrupt() if execution exceeds timeout
            timeout_timer = threading.Timer(timeout, conn.interrupt)
            timeout_timer.start()

            cursor = conn.cursor()
            cursor.execute(sql)

            # 6. Extract returned column names from cursor description
            column_names = [desc[0] for desc in cursor.description] if cursor.description else []

            # 7. Fetch rows with memory protection against unconstrained queries
            truncated = False
            if max_rows is not None and max_rows > 0:
                raw_rows = cursor.fetchmany(max_rows + 1)
                if len(raw_rows) > max_rows:
                    truncated = True
                    raw_rows = raw_rows[:max_rows]
            else:
                raw_rows = cursor.fetchall()

            # Normalize rows to immutable tuples for canonical benchmark comparison
            results = [tuple(row) for row in raw_rows]

            elapsed_ms = (time.time() - start_time) * 1000.0

            return {
                "success": True,
                "results": results,
                "column_names": column_names,
                "row_count": len(results),
                "error": None,
                "time_ms": elapsed_ms,
                "truncated": truncated,
            }

        except sqlite3.OperationalError as e:
            elapsed_ms = (time.time() - start_time) * 1000.0
            error_str = str(e)
            # Detect interruption triggered by the watchdog timer
            if "interrupted" in error_str.lower():
                return {
                    "success": False,
                    "results": [],
                    "column_names": [],
                    "row_count": 0,
                    "error": f"Query execution timed out after {timeout} seconds.",
                    "time_ms": elapsed_ms,
                    "truncated": False,
                }
            return {
                "success": False,
                "results": [],
                "column_names": [],
                "row_count": 0,
                "error": f"SQLite OperationalError: {error_str}",
                "time_ms": elapsed_ms,
                "truncated": False,
            }

        except sqlite3.Error as e:
            elapsed_ms = (time.time() - start_time) * 1000.0
            return {
                "success": False,
                "results": [],
                "column_names": [],
                "row_count": 0,
                "error": f"SQLite Error: {str(e)}",
                "time_ms": elapsed_ms,
                "truncated": False,
            }

        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000.0
            return {
                "success": False,
                "results": [],
                "column_names": [],
                "row_count": 0,
                "error": str(e),
                "time_ms": elapsed_ms,
                "truncated": False,
            }

        finally:
            # Always disarm watchdog timer to avoid thread leaks
            if timeout_timer is not None:
                timeout_timer.cancel()
            # Cleanly close database connection
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _execute_sqlalchemy(
        self,
        db_id: str,
        sql: str,
        timeout: int,
        max_rows: int | None,
    ) -> dict[str, Any]:
        """
        Executes a SQL query using SQLAlchemy (PR #15 future integration).
        """
        start_time = time.time()
        try:
            import sqlalchemy
            from sqlalchemy import create_engine, text
        except ImportError as exc:
            raise ImportError(
                "SQLAlchemy backend requested, but 'sqlalchemy' is not installed in the environment. "
                "Please run 'pip install sqlalchemy' or use backend='sqlite'."
            ) from exc

        try:
            self._verify_read_only_intent(sql)
            db_uri = get_database_uri(self.data_dir, db_id)

            engine = create_engine(
                db_uri,
                execution_options={"timeout": timeout},
                future=True,
            )

            truncated = False
            with engine.connect() as connection:
                result_proxy = connection.execute(text(sql))
                column_names = list(result_proxy.keys())

                if max_rows is not None and max_rows > 0:
                    raw_rows = result_proxy.fetchmany(max_rows + 1)
                    if len(raw_rows) > max_rows:
                        truncated = True
                        raw_rows = raw_rows[:max_rows]
                else:
                    raw_rows = result_proxy.fetchall()

                results = [tuple(row) for row in raw_rows]

            elapsed_ms = (time.time() - start_time) * 1000.0

            return {
                "success": True,
                "results": results,
                "column_names": column_names,
                "row_count": len(results),
                "error": None,
                "time_ms": elapsed_ms,
                "truncated": truncated,
            }

        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000.0
            return {
                "success": False,
                "results": [],
                "column_names": [],
                "row_count": 0,
                "error": f"SQLAlchemy Error: {str(e)}",
                "time_ms": elapsed_ms,
                "truncated": False,
            }

    def execute_query(
        self,
        db_id: str,
        sql: str,
        timeout: int | None = None,
        max_rows: int | None = 10000,
    ) -> dict[str, Any]:
        """
        Executes a SQL query against the target BIRD database.

        Parameters:
            db_id: Database identifier (e.g. 'financial', 'superhero').
            sql: SQL query statement to execute.
            timeout: Execution timeout in seconds (defaults to self.timeout_seconds).
            max_rows: Maximum rows to fetch (default: 10000; protects against memory exhaustion).

        Returns:
            Dictionary matching canonical evaluation result schema:
            {
                "success": bool,
                "results": list[tuple],
                "column_names": list[str],
                "row_count": int,
                "error": str | None,
                "time_ms": float,
                "truncated": bool,
            }
        """
        effective_timeout = timeout if timeout is not None else self.timeout_seconds

        if self.backend == "sqlite":
            return self._execute_sqlite(db_id, sql, timeout=effective_timeout, max_rows=max_rows)
        elif self.backend == "sqlalchemy":
            return self._execute_sqlalchemy(db_id, sql, timeout=effective_timeout, max_rows=max_rows)
        else:
            raise ValueError(
                f"Unsupported executor backend '{self.backend}'. Supported backends: 'sqlite', 'sqlalchemy'."
            )
