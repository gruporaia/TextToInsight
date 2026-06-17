"""
Nó para execução de SQL em SQLite.

Este módulo NÃO gera Python dinamicamente
Ele expõe funções utilitárias para validar e executar SQL em modo seguro

"""
from __future__ import annotations

import re 
import sqlite3
from pathlib import Path
from typing import Any

# Expressão regular para detectar comandos SQL potencialmente perigosos.
BLOQUEIOS_REGEX = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|attach|detach|pragma|vacuum|reindex|replace)\b",
    re.IGNORECASE,
)

def validar_sql_segura(sql: str) -> tuple[bool, str]:
    """
    Valida SQL para uso seguro no executor.

    Regras:
    - permite apenas consultas iniciadas por SELECT ou WITH
    - bloqueia multiplas statements (mais de um ';')
    - bloqueia comandos de escrita/DDL/administracao

    Retorno:
    - (True, "") se a SQL for considerada segura
    - (False, "mensagem de erro") se a SQL violar as regras
    """

    if not sql or not sql.strip():
        return False, "SQL vazia."

    sql_limpa = sql.strip()

    # Remove ';' final opcional para facilitar regras.
    sql_sem_final = sql_limpa[:-1] if sql_limpa.endswith(";") else sql_limpa

    if ";" in sql_sem_final:
        return False, "Multiplas statements nao sao permitidas."

    inicio = sql_sem_final.lstrip().lower()
    if not (inicio.startswith("select") or inicio.startswith("with")):
        return False, "Apenas consultas SELECT/CTE (WITH) sao permitidas."

    if BLOQUEIOS_REGEX.search(sql_sem_final):
        return False, "SQL contem comando bloqueado por politica de seguranca."

    return True, ""

import time


def _linhas_como_dicts(cursor: Any, rows: list[Any]) -> list[dict[str, Any]]:
    """
    Converte as linhas retornadas por um cursor PEP 249 em dicionários.

    Usa `cursor.description` para obter os nomes das colunas (modo portátil,
    funciona com qualquer driver PEP 249) em vez de depender de
    `sqlite3.Row` — assim não precisamos mutar a conexão do usuário.
    """
    if cursor.description is None:
        return []
    colunas = [coluna[0] for coluna in cursor.description]
    return [dict(zip(colunas, linha)) for linha in rows]


def executar_sql_conn(
    conn: Any,
    sql: str,
    limite_preview: int = 5,
    timeout_segundos: float = 15.0,
) -> dict[str, Any]:
    """
    Executa SQL validada usando uma conexão PEP 249 já aberta e retorna
    resultado estruturado.

    A conexão é de responsabilidade de quem a abriu: aqui criamos apenas um
    cursor, executamos a query e descartamos o cursor — a conexão NÃO é
    fechada. A proteção contra escrita vem de `validar_sql_segura` (a garantia
    de somente-leitura via `mode=ro` só existe no caminho legado por `db_path`).

    O timeout por `set_progress_handler` é específico do SQLite; em conexões de
    outros bancos ele é silenciosamente ignorado.
    """
    ok, erro_validacao = validar_sql_segura(sql)
    if not ok:
        return {
            "ok": False,
            "erro_execucao": erro_validacao,
            "linhas_resultado_preview": [],
            "linhas_resultado_completo": [],
            "total_linhas_resultado": 0,
            "saida_terminal": f"[SANDBOX] SQL invalida: {erro_validacao}",
        }

    # Timeout de execução: disponível apenas em conexões SQLite.
    start_time = time.time()
    tem_progress_handler = hasattr(conn, "set_progress_handler")

    def _progress_handler():
        if time.time() - start_time > timeout_segundos:
            return 1  # abortar query
        return 0

    try:
        if tem_progress_handler:
            # Invoca a cada 1000 instruções da máquina virtual do SQLite.
            conn.set_progress_handler(_progress_handler, 1000)

        cur = conn.cursor()
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            total = len(rows)
            all_rows = _linhas_como_dicts(cur, rows)
            preview_rows = all_rows[:limite_preview]

            return {
                "ok": True,
                "erro_execucao": "",
                "linhas_resultado_preview": preview_rows,
                "linhas_resultado_completo": all_rows,
                "total_linhas_resultado": total,
                "saida_terminal": (
                    f"[SANDBOX] Execucao OK | linhas_total={total} "
                    f"| preview={min(total, limite_preview)}"
                ),
            }
        finally:
            cur.close()
    except sqlite3.OperationalError as e:
        erro_msg = str(e)
        if "interrupted" in erro_msg.lower():
            erro_msg = f"Query abortada por timeout (> {timeout_segundos}s)."
        return {
            "ok": False,
            "erro_execucao": f"Falha ao executar SQL: {erro_msg}",
            "linhas_resultado_preview": [],
            "linhas_resultado_completo": [],
            "total_linhas_resultado": 0,
            "saida_terminal": f"[SANDBOX] Erro de execucao: {erro_msg}",
        }
    except Exception as e:
        return {
            "ok": False,
            "erro_execucao": f"Falha ao executar SQL: {e}",
            "linhas_resultado_preview": [],
            "linhas_resultado_completo": [],
            "total_linhas_resultado": 0,
            "saida_terminal": f"[SANDBOX] Erro de execucao: {e}",
        }
    finally:
        # Remove o handler para não deixar efeito colateral na conexão.
        if tem_progress_handler:
            conn.set_progress_handler(None, 1000)


def executar_sql_sqlite(
    db_path: str,
    sql: str,
    limite_preview: int = 5,
    timeout_segundos: float = 15.0,
) -> dict[str, Any]:
    """
    Caminho legado: abre uma conexão SQLite somente-leitura a partir de
    `db_path` e delega para `executar_sql_conn`. Mantido para compatibilidade
    com chamadas que ainda passam o caminho do arquivo.
    """
    caminho = Path(db_path)
    if not caminho.exists():
        msg = f"Arquivo de banco nao encontrado: {db_path}"
        return {
            "ok": False,
            "erro_execucao": msg,
            "linhas_resultado_preview": [],
            "linhas_resultado_completo": [],
            "total_linhas_resultado": 0,
            "saida_terminal": f"[SANDBOX] {msg}",
        }

    conn = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
    try:
        return executar_sql_conn(conn, sql, limite_preview, timeout_segundos)
    finally:
        conn.close()