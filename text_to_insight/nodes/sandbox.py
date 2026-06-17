"""
Nó Executor (antigo Sandbox) do grafo de agentes Text-to-Insight.

Responsabilidade única: validar e executar SQL gerada contra o banco real,
retornando resultado estruturado.
"""

from typing import Any

from ..state import EstadoTextToInsight
from .code_agent.code_sql import executar_sql_conn, executar_sql_sqlite


def nos_nodo_sandbox(estado: EstadoTextToInsight, conn: Any | None = None) -> dict:
    """
    Nó Executor: executa a SQL gerada contra o banco real.

    Lê `sql_gerada` do estado. Se uma conexão (`conn`) for injetada, executa a
    SQL através dela (modo agnóstico ao banco); caso contrário, cai no modo
    legado e abre uma conexão a partir do `db_path` presente no estado.
    A validação de segurança da SQL acontece dentro do executor.
    """
    sql = estado.get("sql_gerada", "").strip()
    db_path = estado.get("db_path", "")

    alvo = "conexão fornecida" if conn is not None else db_path
    print(f"[EXECUTOR] Executando SQL contra {alvo}...")

    if not sql:
        print("[EXECUTOR] Nenhuma SQL encontrada no estado.")
        return {
            "erro_execucao": "Nenhuma SQL gerada para executar.",
            "saida_terminal": "[EXECUTOR] Erro: sql_gerada vazia.",
            "status": "exec_erro",
        }

    if conn is not None:
        resultado = executar_sql_conn(conn, sql)
    else:
        resultado = executar_sql_sqlite(db_path, sql)

    if resultado["ok"]:
        print(f"[EXECUTOR] SQL executada com sucesso — {resultado['total_linhas_resultado']} linhas.")
        return {
            "linhas_resultado_preview": resultado["linhas_resultado_preview"],
            "linhas_resultado_completo": resultado["linhas_resultado_completo"],
            "total_linhas_resultado": resultado["total_linhas_resultado"],
            "saida_terminal": resultado["saida_terminal"],
            "erro_execucao": "",
            "status": "exec_ok",
        }
    else:
        print(f"[EXECUTOR] Erro na execução: {resultado['erro_execucao']}")
        return {
            "linhas_resultado_preview": [],
            "linhas_resultado_completo": [],
            "total_linhas_resultado": 0,
            "saida_terminal": resultado["saida_terminal"],
            "erro_execucao": resultado["erro_execucao"],
            "status": "exec_erro",
            "historico_tentativas": [{"sql": sql, "erro": resultado["erro_execucao"]}],
        }
