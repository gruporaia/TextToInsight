"""
Nó Executor (antigo Sandbox) do grafo de agentes Text-to-Insight.

Responsabilidade única: validar e executar SQL gerada contra o banco real,
retornando resultado estruturado.
"""

from typing import Any

from ..state import EstadoTextToInsight
from .code_agent.code_sql import executar_sql_via_engine


def nos_nodo_sandbox(estado: EstadoTextToInsight, engine: Any) -> dict:
    """
    Nó Executor: executa a SQL gerada na Engine SQLAlchemy injetada pelo
    chamador (SQLite, PostgreSQL, MySQL...). A engine nunca entra no estado,
    então nada sobre a conexão vai para o checkpoint do grafo.
    """
    sql = estado.get("sql_gerada", "").strip()

    if not sql:
        print("[EXECUTOR] Nenhuma SQL encontrada no estado.")
        return {
            "erro_execucao": "Nenhuma SQL gerada para executar.",
            "saida_terminal": "[EXECUTOR] Erro: sql_gerada vazia.",
            "status": "exec_erro",
        }

    print(f"[EXECUTOR] Executando SQL contra banco via engine ({engine.dialect.name})...")
    resultado = executar_sql_via_engine(engine, sql)

    attempt_tokens = estado.get("last_attempt_tokens") or {}

    if resultado["ok"]:
        print(f"[EXECUTOR] SQL executada com sucesso — {resultado['total_linhas_resultado']} linhas.")
        attempt_info = {
            "sql": sql,
            "erro": "",
            "prompt": estado.get("ultimo_prompt", ""),
            "contexto": estado.get("contexto_prompt_agente", ""),
            "raciocinio": estado.get("raciocinio_agente", ""),
            "tokens_input": int(attempt_tokens.get("input", 0)),
            "tokens_output": int(attempt_tokens.get("output", 0)),
            "tokens_total": int(attempt_tokens.get("total", 0)),
        }
        return {
            "linhas_resultado_preview": resultado["linhas_resultado_preview"],
            "linhas_resultado_completo": resultado["linhas_resultado_completo"],
            "total_linhas_resultado": resultado["total_linhas_resultado"],
            "saida_terminal": resultado["saida_terminal"],
            "erro_execucao": "",
            "status": "exec_ok",
            "historico_tentativas": [attempt_info],
        }
    else:
        print(f"[EXECUTOR] Erro na execução: {resultado['erro_execucao']}")
        attempt_info = {
            "sql": sql,
            "erro": resultado["erro_execucao"],
            "prompt": estado.get("ultimo_prompt", ""),
            "contexto": estado.get("contexto_prompt_agente", ""),
            "raciocinio": estado.get("raciocinio_agente", ""),
            "tokens_input": int(attempt_tokens.get("input", 0)),
            "tokens_output": int(attempt_tokens.get("output", 0)),
            "tokens_total": int(attempt_tokens.get("total", 0)),
        }
        return {
            "linhas_resultado_preview": [],
            "linhas_resultado_completo": [],
            "total_linhas_resultado": 0,
            "saida_terminal": resultado["saida_terminal"],
            "erro_execucao": resultado["erro_execucao"],
            "status": "exec_erro",
            "historico_tentativas": [attempt_info],
        }
