"""
Nó Executor (antigo Sandbox) do grafo de agentes Text-to-Insight.

Responsabilidade única: validar e executar SQL gerada contra o banco real,
retornando resultado estruturado.
"""

from ..state import EstadoTextToInsight, EstadoCandidato
from .code_agent.code_sql import executar_sql_sqlite
from .voting_node import calcular_assinatura_resultado


def nos_nodo_sandbox(estado: EstadoTextToInsight) -> dict:
    """
    Nó Executor: executa a SQL gerada contra o banco SQLite real.

    Lê `sql_gerada` e `db_path` do estado, delega para `executar_sql_sqlite`
    (que já faz validação de segurança) e retorna o resultado estruturado.
    """
    sql = estado.get("sql_gerada", "").strip()
    db_path = estado.get("db_path", "")

    print(f"[EXECUTOR] Executando SQL contra {db_path}...")

    if not sql:
        print("[EXECUTOR] Nenhuma SQL encontrada no estado.")
        return {
            "erro_execucao": "Nenhuma SQL gerada para executar.",
            "saida_terminal": "[EXECUTOR] Erro: sql_gerada vazia.",
            "status": "exec_erro",
        }

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


# ============================================================================
# ARQUITETURA ReFoRCE: Validação de Candidatos (Self-Refinement)
# ============================================================================

def sandbox_validacao_candidato(
    estado_candidato: EstadoCandidato,
) -> EstadoCandidato:
    """
    Executa SQL de um candidato individual e valida resultado.
    
    Responsabilidades:
    1. Executar SQL em sandbox seguro (já via executar_sql_sqlite)
    2. Se sucesso: calcular assinatura, marcar valido=True
    3. Se erro: registrar erro, avaliar se é retry-able (sintaxe + timeout)
    
    Args:
        estado_candidato: Estado do candidato com SQL preenchida
    
    Returns:
        EstadoCandidato atualizado com resultado_execucao, erro, valido, assinatura
    """
    
    sql = estado_candidato.get("sql", "").strip()
    tentativas = estado_candidato.get("tentativas_refinamento", 0)
    db_path = estado_candidato.get("db_path", "")
    temp = estado_candidato.get("temperatura", 0.0)
    
    print(f"[CANDIDATO Temp {temp}] Executando SQL (tentativa {tentativas})...")
    
    if not sql:
        estado_candidato["valido"] = False
        estado_candidato["erro"] = "SQL vazia"
        return estado_candidato
    
    resultado = executar_sql_sqlite(db_path, sql)
    
    if resultado["ok"]:
        # ✅ Sucesso: calcular assinatura e marcar válido
        linhas = resultado["linhas_resultado_completo"]
        assinatura = calcular_assinatura_resultado(linhas)
        
        estado_candidato["resultado_execucao"] = {
            "linhas": linhas,
            "total_linhas": resultado["total_linhas_resultado"],
            "preview": resultado["linhas_resultado_preview"],
        }
        estado_candidato["valido"] = True
        estado_candidato["assinatura_resultado"] = assinatura
        estado_candidato["erro"] = ""
        
        print(f"[CANDIDATO Temp {temp}] ✅ Sucesso: {resultado['total_linhas_resultado']} linhas | Hash: {assinatura[:16]}...")
        
    else:
        # ❌ Erro: avaliar se é retry-able
        erro_msg = resultado["erro_execucao"]
        
        # Detectar tipos de erro retry-able
        is_syntax_error = "syntax" in erro_msg.lower() or "near" in erro_msg.lower()
        is_timeout = "timeout" in erro_msg.lower()
        is_retry_able = is_syntax_error or is_timeout
        
        # Se retry-able e tentativas < 3: marcar para retry, não como falha final
        if is_retry_able and tentativas < 5:
            print(f"[CANDIDATO Temp {temp}] ⚠️ Erro retry-able: {erro_msg[:60]}...")
            estado_candidato["erro"] = erro_msg
            estado_candidato["tentativas_refinamento"] = tentativas + 1
            estado_candidato["valido"] = False
            # NÃO retornar ainda: o sub-grafo vai redirecionar para retry
        else:
            # Falha final: erro lógico ou limite de tentativas atingido
            print(f"[CANDIDATO Temp {temp}] ❌ Falha final: {erro_msg[:60]}...")
            estado_candidato["erro"] = erro_msg
            estado_candidato["valido"] = False
            estado_candidato["resultado_execucao"] = {
                "linhas": [],
                "total_linhas": 0,
                "preview": [],
            }
            estado_candidato["assinatura_resultado"] = "ERROR"
    
    return estado_candidato