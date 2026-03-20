"""
Grafo compilado do sistema Text-to-Insight.

Este módulo instancia e compila o StateGraph que orquestra o fluxo
de agentes para transformar perguntas em código executável e insights.

O fluxo geral:
1. Planejador: Decide estratégia
2. Esquema: Obtém contexto do banco
3. Agente de Código: Gera código Python
4. Sandbox: Executa com segurança
5. Crítico: Avalia qualidade
6. Roteadores condicionais decidem continuação ou conclusão
"""

from langgraph.graph import StateGraph, START, END

from .state import EstadoTextToInsight
from .nodes import (
    nos_nodo_planejador,
    nos_nodo_esquema,
    nos_nodo_sandbox,
    nos_nodo_critico,
)

from .nodes.code_agent import (
    query_classify,
    easy_query,
    nested_complex_query,
    non_nested_complex_query,
    nos_nodo_agente_codigo #vou remover esse nó, deixei só pra testar a funcionalidade do grafo
)

from .routers import roteador_sandbox, roteador_planejador

def construir_grafo_text_to_insight() -> StateGraph:
    """
    Constrói e compila o grafo de agentes Text-to-Insight.
    
    Arquitetura:
        - Nós: planejador, esquema, agente_codigo, sandbox, critico
        - Arestas fixas: START -> planejador
        - Arestas condicionais: roteador_planejador, roteador_sandbox
    
    Returns:
        StateGraph: Grafo compilado pronto para invocar.
    """
    
    # Instanciar builder do grafo com o estado
    construtor_grafo = StateGraph(EstadoTextToInsight)
    
    # ============================================================
    # 1. ADICIONAR NÓS
    # ============================================================
    
    construtor_grafo.add_node("planejador", nos_nodo_planejador)
    construtor_grafo.add_node("esquema", nos_nodo_esquema)
    construtor_grafo.add_node("agente_codigo", nos_nodo_agente_codigo)
    construtor_grafo.add_node("sandbox", nos_nodo_sandbox)
    construtor_grafo.add_node("critico", nos_nodo_critico)
    
    # ============================================================
    # 2. ARESTAS FIXAS
    # ============================================================
    
    # START sempre vai para o planejador (ponto de entrada)
    construtor_grafo.add_edge(START, "planejador")
    
    # Após esquema sempre vai para agente_codigo
    construtor_grafo.add_edge("esquema", "agente_codigo")
    
    # Após agente_codigo sempre vai para sandbox
    construtor_grafo.add_edge("agente_codigo", "sandbox")
    
    # ============================================================
    # 3. ARESTAS CONDICIONAIS
    # ============================================================
    
    # Após sandbox, roteador decide: critic ou planejador
    construtor_grafo.add_conditional_edges(
        "sandbox",
        roteador_sandbox,
        {
            "critico": "critico",
            "planejador": "planejador",
        }
    )
    
    # Após planejador, roteador decide: esquema, agente_codigo, critico ou fim
    construtor_grafo.add_conditional_edges(
        "planejador",
        roteador_planejador,
        {
            "esquema": "esquema",
            "agente_codigo": "agente_codigo",
            "critico": "critico",
            "fim": END,
        }
    )
    
    # Após crítico:
    # - Se reprovado (status=='reprovado'): volta ao planejador
    # - Se aprovado (status=='aprovado'): vai ao fim
    def roteador_critico(estado: EstadoTextToInsight) -> str:
        """
        Roteador após Crítico:
        - reprovado -> planejador (refazer)
        - aprovado -> fim (conclusão)
        """
        status = estado.get("status", "")
        if status == "aprovado":
            return "fim"
        else:
            return "planejador"
    
    construtor_grafo.add_conditional_edges(
        "critico",
        roteador_critico,
        {
            "planejador": "planejador",
            "fim": END,
        }
    )
    
    return construtor_grafo


def compilar_grafo() -> "CompiledStateGraph":
    """
    Compila o grafo para execução.
    
    Returns:
        CompiledStateGraph: Grafo compilado pronto para invocar.
    """
    construtor = construir_grafo_text_to_insight()
    grafo_compilado = construtor.compile()
    
    print("[GRAFO] Grafo Text-to-Insight compilado com sucesso!")
    print("[GRAFO] Nós registrados:", list(grafo_compilado.nodes.keys()))
    
    return grafo_compilado


# Instância global do grafo compilado
grafo_text_to_insight = compilar_grafo()


if __name__ == "__main__":
    # Exemplo de uso do grafo
    print("\n=== TESTE DO GRAFO ===\n")
    
    # Estado inicial
    estado_inicial = {
        "pergunta_usuario": "Qual é o total de vendas por produto?",
        "contexto_schema": "",
        "codigo_gerado": "",
        "saida_terminal": "",
        "feedback_critico": "",
        "status": "iniciado",
        "tentativas_loop": 0,
    }
    
    print("Estado inicial:")
    print(estado_inicial)
    print("\nExecutando grafo...\n")
    
    # Invocar o grafo
    resultado = grafo_text_to_insight.invoke(estado_inicial)
    
    print("\nEstado final:")
    print(resultado)
