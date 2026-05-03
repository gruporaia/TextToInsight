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

def executar_sql_sqlite(
    db_path: str,
    sql: str,
    limite_preview: int = 5,
) -> dict[str, Any]:
    """
    Executa SQL validada em SQLite modo read-only e retorna resultado estruturado.
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

    try:
        conn = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
            total = len(rows)
            preview_rows = [dict(r) for r in rows[:limite_preview]]
            all_rows = [dict(r) for r in rows]

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
            conn.close()
    except Exception as e:
        return {
            "ok": False,
            "erro_execucao": f"Falha ao executar SQL: {e}",
            "linhas_resultado_preview": [],
            "linhas_resultado_completo": [],
            "total_linhas_resultado": 0,
            "saida_terminal": f"[SANDBOX] Erro de execucao: {e}",
        }