"""
Executor de queries contra bancos SQLite do Spider dataset.

Fornece funcionalidades para:
- Conectar dinamicamente a bancos por db_id
- Executar queries em modo read-only
- Capturar resultados e erros
"""

import sqlite3
import time
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
