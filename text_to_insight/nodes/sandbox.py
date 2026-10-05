"""
Nó Executor (antigo Sandbox) do grafo de agentes Text-to-Insight.

Responsabilidade única: validar e executar SQL gerada contra o banco real,
retornando resultado estruturado.
"""

from typing import Any

from ..state import EstadoTextToInsight
from .code_agent.code_sql import executar_sql_sqlite, executar_sql_via_engine, executar_sql_via_url


def nos_nodo_sandbox(estado: EstadoTextToInsight, engine: Any = None) -> dict:
    """
    Nó Executor: executa a SQL gerada contra o banco real (SQLite, PostgreSQL ou MySQL).

    Se uma Engine SQLAlchemy foi injetada pelo chamador (`engine`), executa
    diretamente nela via `executar_sql_via_engine` — nada de db_path/db_config/
    db_dialeto/db_url precisa estar no estado (nem, portanto, no checkpoint do
    grafo). Agora, a lógica utiliza uma engine, se ela existir. Caso não exista, verifica 
    se existe url. Novamente, se não existir, roda SQlite por padrão com db_path.
    """
    sql = estado.get("sql_gerada", "").strip()

    if not sql:
        print("[EXECUTOR] Nenhuma SQL encontrada no estado.")
        return {
            "erro_execucao": "Nenhuma SQL gerada para executar.",
            "saida_terminal": "[EXECUTOR] Erro: sql_gerada vazia.",
            "status": "exec_erro",
        }

    if engine is not None:
        print(f"[EXECUTOR] Executando SQL contra banco via engine ({engine.dialect.name})...")
        resultado = executar_sql_via_engine(engine, sql)
    else:
        db_path = estado.get("db_path", "")
        db_url = estado.get("db_url", "").strip()

        alvo = "banco via db_url (dialeto auto-detectado)" if db_url else db_path
        print(f"[EXECUTOR] Executando SQL contra {alvo}...")

        if db_url:
            resultado = executar_sql_via_url(db_url, sql)
        else:
            resultado = executar_sql_sqlite(db_path=db_path, sql=sql)

    if resultado["ok"]:
        print(f"[EXECUTOR] SQL executada com sucesso — {resultado['total_linhas_resultado']} linhas.")
        attempt_info = {
            "sql": sql,
            "erro": "",
            "prompt": estado.get("ultimo_prompt", ""),
            "contexto": estado.get("contexto_prompt_agente", ""),
            "raciocinio": estado.get("raciocinio_agente", ""),
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
