"""
Nó Planejador do grafo de agentes Text-to-Insight.

O planejador é o cérebro do sistema, responsável por:
- Analisar a pergunta do usuário
- Decidir a estratégia de execução
- Orquestrar o fluxo entre os demais nós
"""

from ..state import EstadoTextToInsight


def nos_nodo_planejador(estado: EstadoTextToInsight) -> dict:
    """
    Nó Planejador: Orquestra a estratégia de execução.
    
    Analysa a pergunta do usuário e decide o plano de ação. Pode determinar:
    - Se precisa buscar contexto do banco de dados (schema)
    - Se pode proceder diretamente para geração de código
    - Se deve finalizar a execução
    
    Args:
        estado (EstadoTextToInsight): Estado atual do grafo contendo a pergunta do usuário.
    
    Returns:
        dict: Dicionário com atualizações do estado.
              - status: 'aguardando_schema', 'pronto_codificacao' ou 'finalizado'
              - contexto_schema: atualizado se necessário (pode estar vazio inicialmente)
    """
    
    # Simular análise da pergunta do usuário
    pergunta = estado.get("pergunta_usuario", "")
    
    # Lógica simulada: se o contexto_schema está vazio, precisamos buscar
    if not estado.get("contexto_schema", ""):
        status_planejador = "aguardando_schema"
        print(f"[PLANEJADOR] Pergunta recebida: {pergunta[:50]}...")
        print(f"[PLANEJADOR] Schema não encontrado. Buscando contexto...")
    else:
        # Se já temos schema e nenhum feedback crítico, podemos gerar código
        if not estado.get("feedback_critico", ""):
            status_planejador = "pronto_codificacao"
            print(f"[PLANEJADOR] Schema obtido. Pronto para gerar código.")
        else:
            # Se há feedback crítico anterior, revisitamos o plano
            status_planejador = "revisando_estrategia"
            print(f"[PLANEJADOR] Feedback anterior: {estado.get('feedback_critico', '')[:50]}...")
    
    return {
        "status": status_planejador,
        "tentativas_loop": estado.get("tentativas_loop", 0),
    }
