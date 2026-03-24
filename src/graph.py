"""
Grafo compilado do sistema Text-to-Insight.

Fluxo MVP:
1. Planejador: Decide estratégia (LLM)
2. Esquema: Obtém contexto do banco (SQLite introspection)
3. Agente de Código: Gera SQL (LLM)
4. Executor: Executa SQL no banco real
5. Crítico: Avalia qualidade (LLM)
6. Roteadores condicionais decidem continuação ou conclusão
"""

from langgraph.graph import StateGraph, START, END

from .state import EstadoTextToInsight
from .nodes import (
    nos_nodo_planejador,
    nos_nodo_esquema,
    nos_nodo_agente_codigo,
    nos_nodo_sandbox,
    nos_nodo_critico,
)
from .routers import roteador_sandbox, roteador_planejador


def construir_grafo_text_to_insight() -> StateGraph:
    """
    Constrói e compila o grafo de agentes Text-to-Insight.
    """
    construtor_grafo = StateGraph(EstadoTextToInsight)

    # 1. ADICIONAR NÓS
    construtor_grafo.add_node("planejador", nos_nodo_planejador)
    construtor_grafo.add_node("esquema", nos_nodo_esquema)
    construtor_grafo.add_node("agente_codigo", nos_nodo_agente_codigo)
    construtor_grafo.add_node("sandbox", nos_nodo_sandbox)
    construtor_grafo.add_node("critico", nos_nodo_critico)

    # 2. ARESTAS FIXAS
    construtor_grafo.add_edge(START, "planejador")
    construtor_grafo.add_edge("esquema", "agente_codigo")
    construtor_grafo.add_edge("agente_codigo", "sandbox")

    # 3. ARESTAS CONDICIONAIS
    construtor_grafo.add_conditional_edges(
        "sandbox",
        roteador_sandbox,
        {
            "critico": "critico",
            "planejador": "planejador",
        }
    )

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

    def roteador_critico(estado: EstadoTextToInsight) -> str:
        status = estado.get("status", "")
        if status == "aprovado":
            return "fim"
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
    construtor = construir_grafo_text_to_insight()
    grafo_compilado = construtor.compile()
    print("[GRAFO] Grafo Text-to-Insight compilado com sucesso!")
    return grafo_compilado


grafo_text_to_insight = compilar_grafo()
