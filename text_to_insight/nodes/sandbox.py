"""
Nó Executor (antigo Sandbox) do grafo de agentes Text-to-Insight.

Responsabilidade única: validar e executar SQL gerada contra o banco real,
retornando resultado estruturado.
"""

from ..state import EstadoTextToInsight
from .code_agent.code_sql import executar_sql, executar_sql_via_url


def nos_nodo_sandbox(estado: EstadoTextToInsight) -> dict:
    """
    Nó Executor: executa a SQL gerada contra o banco real (SQLite, PostgreSQL ou MySQL).

    Lê `sql_gerada`, `db_path`/`db_config` e `db_dialeto` do estado, delega para
    `executar_sql` (que já faz validação de segurança e roteia por dialeto) e
    retorna o resultado estruturado.
    """
    sql = estado.get("sql_gerada", "").strip()
    db_path = estado.get("db_path", "")
    db_config = estado.get("db_config") or {}
    dialeto = estado.get("db_dialeto", "sqlite")

    db_url = estado.get("db_url", "").strip()

    alvo = "banco via db_url (dialeto auto-detectado)" if db_url else (
        db_path if dialeto == "sqlite" else f"{dialeto}://{db_config.get('host', '?')}/{db_config.get('database', '?')}"
    )
    print(f"[EXECUTOR] Executando SQL contra {alvo}...")

    if not sql:
        print("[EXECUTOR] Nenhuma SQL encontrada no estado.")
        return {
            "erro_execucao": "Nenhuma SQL gerada para executar.",
            "saida_terminal": "[EXECUTOR] Erro: sql_gerada vazia.",
            "status": "exec_erro",
        }

    if db_url:
        resultado = executar_sql_via_url(db_url, sql)
    else:
        resultado = executar_sql(dialeto, sql, db_path=db_path, db_config=db_config)

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
