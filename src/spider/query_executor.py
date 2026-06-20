"""
Executor de queries contra bancos SQLite do Spider dataset.

Fornece funcionalidades para:
- Conectar dinamicamente a bancos por db_id
- Executar queries em modo read-only
- Capturar resultados e erros
"""

import sqlite3
import time
import math
from pathlib import Path
from typing import Any


class SpiderQueryExecutor:
    """Executor de queries em bancos Spider com controle de timeout e segurança."""

    def __init__(self, database_dir: str = "data/spider_data/spider_data/database"):
        """
        Inicializa executor.

        Args:
            database_dir: Diretório contendo subpastas com bancos SQLite
        """
        self.database_dir = Path(database_dir)

    def get_db_path(self, db_id: str) -> Path:
        """
        Retorna caminho para banco específico.

        Args:
            db_id: ID do banco (ex: concert_singer)

        Returns:
            Caminho para .sqlite

        Raises:
            FileNotFoundError: Se banco não existe
        """
        db_path = self.database_dir / db_id / f"{db_id}.sqlite"
        if not db_path.exists():
            raise FileNotFoundError(f"Banco não encontrado: {db_path}")
        return db_path

    def execute_query(
        self,
        db_id: str,
        sql: str,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """
        Executa query em modo read-only contra um banco.

        Args:
            db_id: ID do banco
            sql: SQL a executar
            timeout: Timeout em segundos

        Returns:
            Dict com chaves:
            - success: bool
            - results: list[dict] (se sucesso)
            - row_count: int (total de linhas, sem limit)
            - error: str (se erro)
            - time_ms: float (tempo de execução)
        """
        start_time = time.time()

        try:
            db_path = self.get_db_path(db_id)

            # Conectar em modo read-only
            connection_string = f"file:{db_path}?mode=ro&uri=true"
            conn = sqlite3.connect(connection_string, timeout=timeout, uri=True)
            conn.row_factory = sqlite3.Row  # Retornar dicts

            # ========================================================================
            # INJEÇÃO DE FUNÇÕES MATEMÁTICAS NO SQLITE
            # Como o SQLite não possui nativamente as funções trigonométricas clássicas
            # (que existem no Postgres, MySQL, etc), o LLM frequentemente gera SQLs 
            # válidos que quebram no SQLite com o erro "No such function". 
            # Aqui nós ensinamos o SQLite local a resolver essas operações
            # em tempo de execução usando a biblioteca math nativa do Python. 
            # Isso é vital para as queries geográficas e analíticas do Spider 2.
            # ========================================================================
            conn.create_function("SIN", 1, math.sin)
            conn.create_function("COS", 1, math.cos)
            conn.create_function("SQRT", 1, math.sqrt)
            conn.create_function("RADIANS", 1, math.radians)
            conn.create_function("ACOS", 1, math.acos)
            conn.create_function("ASIN", 1, math.asin)
            conn.create_function("TAN", 1, math.tan)
            conn.create_function("ATAN", 1, math.atan)
            conn.create_function("DEGREES", 1, math.degrees)
            conn.create_function("POWER", 2, math.pow)
            conn.create_function("PI", 0, lambda: math.pi)
            conn.create_function("EXP", 1, math.exp)
            conn.create_function("LN", 1, math.log)
            conn.create_function("LOG", 1, math.log10)
            conn.create_function("LOG10", 1, math.log10)
            conn.create_function("CEIL", 1, math.ceil)
            conn.create_function("CEILING", 1, math.ceil)
            conn.create_function("FLOOR", 1, math.floor)
            conn.create_function("SIGN", 1, lambda x: -1 if x < 0 else (1 if x > 0 else 0))

            cursor = conn.cursor()

            # Executar query
            cursor.execute(sql)
            rows = cursor.fetchall()

            # Converter para list[dict]
            results = [dict(row) for row in rows]

            conn.close()

            elapsed_ms = (time.time() - start_time) * 1000

            return {
                "success": True,
                "results": results,
                "row_count": len(results),
                "error": "",
                "time_ms": elapsed_ms,
            }

        except sqlite3.Error as e:
            elapsed_ms = (time.time() - start_time) * 1000
            return {
                "success": False,
                "results": [],
                "row_count": 0,
                "error": f"SQLite error: {str(e)}",
                "time_ms": elapsed_ms,
            }
        except FileNotFoundError as e:
            elapsed_ms = (time.time() - start_time) * 1000
            return {
                "success": False,
                "results": [],
                "row_count": 0,
                "error": f"Database not found: {str(e)}",
                "time_ms": elapsed_ms,
            }
        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000
            return {
                "success": False,
                "results": [],
                "row_count": 0,
                "error": f"Unexpected error: {str(e)}",
                "time_ms": elapsed_ms,
            }
