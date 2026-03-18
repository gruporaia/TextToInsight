"""
Nó Crítico do grafo de agentes Text-to-Insight.

O nó crítico é responsável por:
- Avaliar a qualidade do código gerado
- Verificar se a saída atende à pergunta original
- Fornecerdback construtivo
- Decidir se necessita iteração ou conclusão
"""

from ..state import EstadoTextToInsight


def nos_nodo_critico(estado: EstadoTextToInsight) -> dict:
    """
    Nó Crítico: Avalia se o código atendeu à pergunta original.
    
    Simula o processo de revisão crítica:
    - Compara a saída com a pergunta original
    - Verifica completude da resposta
    - Fornece feedback de melhoria
    - Emite veredito (aprovado/reprovado)
    
    Args:
        estado (EstadoTextToInsight): Estado atual do grafo.
    
    Returns:
        dict: Dicionário com atualizações do estado.
              - feedback_critico: String com avaliação e sugestões
              - status: 'aprovado' ou 'reprovado'
    """
    
    pergunta = estado.get("pergunta_usuario", "")
    saida = estado.get("saida_terminal", "")
    codigo = estado.get("codigo_gerado", "")
    tentativas = estado.get("tentativas_loop", 0)
    status_anterior = estado.get("status", "")
    
    print("[CRÍTICO] Avaliando qualidade do código e saída...")
    
    # Simular avaliação crítica
    feedback = f"""
    === AVALIAÇÃO CRÍTICA ===
    
    Pergunta original:
    "{pergunta}"
    
    Análise do código:
    - Cobertura de casos: 85%
    - Tratamento de erros: Parcial
    - Clareza: Boa
    - Eficiência: Aceitável
    
    Análise da saída:
    - Responde à pergunta: Sim
    - Completa: Aproximadamente
    - Formato apropriado: Sim
    
    Tentativa número: {tentativas}
    Status anterior: {status_anterior}
    
    Sugestões de melhoria:
    - Adicionar tratamento de conexão perdida
    - Melhorar mensagens de erro
    - Otimizar consulta SQL
    """
    
    # Simular veredito (progressivamente mais aprovado com tentativas)
    if status_anterior == "codigo_ok" and tentativas >= 2:
        veredito = "aprovado"
        print("[CRÍTICO] Código APROVADO! Pronto para conclusão.")
    elif status_anterior == "codigo_ok" and tentativas == 1:
        veredito = "reprovado"
        print("[CRÍTICO] Código precisa melhorias. Retornando para revisão.")
    else:
        veredito = "reprovado"
        print("[CRÍTICO] Código não passou na avaliação. Necessário revisar.")
    
    return {
        "feedback_critico": feedback,
        "status": veredito,
    }
