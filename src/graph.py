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

from functools import partial

from langgraph.graph import StateGraph, START, END
from langchain_google_genai import ChatGoogleGenerativeAI

from .state import EstadoTextToInsight
from .nodes import (
    nos_nodo_planejador,
    nos_nodo_esquema,
    nos_nodo_agente_codigo,
    nos_nodo_sandbox,
    nos_nodo_critico,
)
from .routers import roteador_sandbox, roteador_planejador

class Graph:
    def __init__(self, api_key): #Essa definição do grafo pode mudar pro caso de utilizarmos diferentes modelos

        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash", #Aqui coloquei manualmente o gemini 2.5, mas podemos deixar na definição do grafo
                                      #um campo pro usuário utilizar mais modelos no futuro
            google_api_key=api_key
            )
        self.grafo_text_to_insight = self._compilar_grafo()

    def _construir_grafo_text_to_insight(self) -> StateGraph:
        """
        Constrói e compila o grafo de agentes Text-to-Insight.
        """
        construtor_grafo = StateGraph(EstadoTextToInsight)

        # 1. ADICIONAR NÓS
        construtor_grafo.add_node("planejador", partial(nos_nodo_planejador, llm=self.llm))
        construtor_grafo.add_node("esquema", nos_nodo_esquema)
        construtor_grafo.add_node("agente_codigo", partial(nos_nodo_agente_codigo, llm=self.llm))
        construtor_grafo.add_node("sandbox", nos_nodo_sandbox)
        construtor_grafo.add_node("critico", partial(nos_nodo_critico, llm=self.llm))

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

    def _compilar_grafo(self) -> "CompiledStateGraph":
        construtor = self._construir_grafo_text_to_insight()
        grafo_compilado = construtor.compile()
        print("[GRAFO] Grafo Text-to-Insight compilado com sucesso!")
        return grafo_compilado

    def invoke(self, estado: EstadoTextToInsight):
        return self.grafo_text_to_insight.invoke(estado)
