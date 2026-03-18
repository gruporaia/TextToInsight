"""
Funções de roteamento condicional para o grafo Text-to-Insight.

As arestas condicionais decidem para qual nó o grafo deve prosseguir
baseado nas condições do estado atual. Utilizadas com add_conditional_edges()
no GraphState.
"""

from typing import Literal
from ..state import EstadoTextToInsight


def roteador_sandbox(estado: EstadoTextToInsight) -> Literal["critico", "planejador"]:
    """
    Roteador após execução do Sandbox.
    
    Decide para onde ir depois que o código foi executado:
    - Se houve ERRO e tentativas < 3: volta para code_agent para revisar
    - Se sucesso: prossegue para critico
    - Se muitas tentativas: volta para planejador
    
    Args:
        estado (EstadoTextToInsight): Estado atual do grafo.
    
    Returns:
        Literal["critico", "planejador"]: Nome do próximo nó.
        
    Lógica:
        - status == "erro_codigo" e tentativas < 3 -> "planejador" (reconsiderar estratégia)
        - status == "codigo_ok" -> "critico" (avaliar resultado)
        - tentativas >= 3 -> "planejador" (reiniciar com novo plano)
    """
    
    status = estado.get("status", "")
    tentativas = estado.get("tentativas_loop", 0)
    
    print(f"[ROTEADOR_SANDBOX] Status: {status}, Tentativas: {tentativas}")
    
    # Se code rodou com erro mas temos tentativas restantes
    if status == "erro_codigo" and tentativas < 3:
        print("[ROTEADOR_SANDBOX] Erro detectado. Retornando ao planejador...")
        return "planejador"
    
    # Se code rodou com sucesso, segue para crítico avaliar
    elif status == "codigo_ok":
        print("[ROTEADOR_SANDBOX] Código bem-sucedido. Enviando para crítico...")
        return "critico"
    
    # Se muitas tentativas, reinicia estratégia
    else:
        print("[ROTEADOR_SANDBOX] Muitas tentativas. Reiniciando no planejador...")
        return "planejador"


def roteador_planejador(estado: EstadoTextToInsight) -> Literal["esquema", "agente_codigo", "critico", "fim"]:
    """
    Roteador após Planejador.
    
    Decide para onde ir después del planejamiento:
    - Se contexto_schema está vazio: vai para "esquema"
    - Se tem feedback_critico anterior: volta para "agente_codigo"
    - Se código foi gerado: vai para "sandbox"
    - Se tudo OK: finaliza ("fim")
    
    Args:
        estado (EstadoTextToInsight): Estado atual do grafo.
    
    Returns:
        Literal["esquema", "agente_codigo", "critico", "fim"]: Nome do próximo nó.
        
    Lógica:
        - contexto_schema vazio -> "esquema" (buscar contexto)
        - status == "revisando_estrategia" -> "agente_codigo" (repensar código)
        - status == "pronto_codificacao" -> "agente_codigo" (gerar código)
        - status == "aprovado" -> "fim" (finalizar)
    """
    
    contexto = estado.get("contexto_schema", "")
    status = estado.get("status", "")
    feedback = estado.get("feedback_critico", "")
    
    print(f"[ROTEADOR_PLANEJADOR] Status: {status}, Schema preenchido: {bool(contexto)}")
    
    # Se não temos contexto, busca ele primeiro
    if not contexto:
        print("[ROTEADOR_PLANEJADOR] Schema vazio. Buscando contexto...")
        return "esquema"
    
    # Se há feedback anterior (reprovado), volta para revisar código
    if feedback and status == "reprovado":
        print("[ROTEADOR_PLANEJADOR] Código reprovado. Buscando melhoria...")
        return "agente_codigo"
    
    # Se status é "pronto_codificacao" ou "revisando_estrategia", gera código
    if status in ["pronto_codificacao", "revisando_estrategia", "aguardando_schema"]:
        print("[ROTEADOR_PLANEJADOR] Gerando código...")
        return "agente_codigo"
    
    # Se foi aprovado, finaliza
    if status == "aprovado":
        print("[ROTEADOR_PLANEJADOR] Aprovado! Finalizando execução...")
        return "fim"
    
    # Default: volta para agente_codigo
    print("[ROTEADOR_PLANEJADOR] Padrão: enviando para agente_codigo...")
    return "agente_codigo"
