"""
Grafo compilado do sistema Text-to-Insight.

Fluxo MVP:
1. Planejador: Decide estratégia (LLM)
2. Esquema: Obtém contexto do banco (SQLite introspection)
3. Agente de Código: Gera SQL (LLM)
4. Executor: Executa SQL no banco real
5. Salvar CSV: Exporta resultado para CSV
6. Roteador Gráfico: Decide se gera visualização (LLM)
7. Gerador Gráfico: Gera gráfico matplotlib (LLM + subprocess)
8. Resposta: Gera resposta em linguagem natural (LLM)
"""

from functools import partial

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from .state import EstadoTextToInsight
from .nodes import (
    nos_nodo_planejador,
    nos_nodo_esquema,
    nos_nodo_retriever,
    nos_nodo_data_exploration,
    nos_nodo_exploration_selector,
    nos_nodo_agente_codigo,
    nos_nodo_sandbox,
    nos_nodo_resposta,
    nos_nodo_salvar_csv,
    nos_nodo_gerador_grafico,
)
from .routers import roteador_sandbox, roteador_planejador, roteador_grafico
from .model_selection import get_model

def nos_nodo_espera_humana(estado: EstadoTextToInsight):
    """Nó estrutural: serve apenas como breakpoint para o HITL."""
    return estado

class Graph:
    def __init__(self, api_key: str, model: str, hitl: bool = True, enable_graphs: bool = True, use_cot: bool = True, use_data_exploration: bool = True, use_exploration_selector: bool = False, use_rag: bool = True):
        self.llm = get_model(model, api_key)
        self.memory = MemorySaver()
        self.enable_graphs = enable_graphs
        self.use_cot = use_cot
        self.use_data_exploration = use_data_exploration
        self.use_exploration_selector = use_exploration_selector
        self.use_rag = use_rag
        self.grafo_text_to_insight = self._compilar_grafo(hitl)

    def _construir_grafo_text_to_insight(self, hitl: bool) -> StateGraph:
        """
        Constrói e compila o grafo de agentes Text-to-Insight.
        """
        construtor_grafo = StateGraph(EstadoTextToInsight)

        # 1. ADICIONAR NÓS
        construtor_grafo.add_node("planejador", partial(nos_nodo_planejador, llm=self.llm, hitl=hitl))
        construtor_grafo.add_node("espera_humana", nos_nodo_espera_humana)
        construtor_grafo.add_node("esquema", nos_nodo_esquema)
        construtor_grafo.add_node("retriever", partial(nos_nodo_retriever, use_rag=self.use_rag))
        construtor_grafo.add_node("exploration_selector", partial(nos_nodo_exploration_selector, llm=self.llm, use_exploration_selector=self.use_exploration_selector))
        construtor_grafo.add_node("data_exploration", partial(nos_nodo_data_exploration, use_data_exploration=self.use_data_exploration))
        construtor_grafo.add_node("agente_codigo", partial(nos_nodo_agente_codigo, llm=self.llm, use_cot=self.use_cot))
        construtor_grafo.add_node("sandbox", nos_nodo_sandbox)
        construtor_grafo.add_node("salvar_csv", nos_nodo_salvar_csv)
        construtor_grafo.add_node("gerador_grafico", partial(nos_nodo_gerador_grafico, llm=self.llm))
        construtor_grafo.add_node("resposta", partial(nos_nodo_resposta, llm=self.llm))

        # 2. ARESTAS FIXAS
        construtor_grafo.add_edge(START, "planejador")
        construtor_grafo.add_edge("espera_humana", "planejador")
        construtor_grafo.add_edge("esquema", "retriever")
        construtor_grafo.add_edge("retriever", "exploration_selector")
        construtor_grafo.add_edge("exploration_selector", "data_exploration")
        construtor_grafo.add_edge("data_exploration", "planejador")
        construtor_grafo.add_edge("agente_codigo", "sandbox")

        # Gerador de gráfico sempre vai para resposta (sucesso ou falha)
        construtor_grafo.add_edge("gerador_grafico", "resposta")

        # 3. ARESTAS CONDICIONAIS
        construtor_grafo.add_conditional_edges(
            "sandbox",
            partial(roteador_sandbox, enable_graphs=self.enable_graphs),
            {
                "salvar_csv": "salvar_csv",
                "resposta": "resposta",
                "planejador": "planejador",
            }
        )

        construtor_grafo.add_conditional_edges(
            "planejador",
            roteador_planejador,
            {
                "espera_humana": "espera_humana",
                "esquema": "esquema",
                "agente_codigo": "agente_codigo",
                "planejador": "planejador",
                "fim": END,
            }
        )



        # Após salvar CSV, o roteador de gráfico decide se gera visualização
        construtor_grafo.add_conditional_edges(
            "salvar_csv",
            partial(roteador_grafico, llm=self.llm),
            {
                "gerador_grafico": "gerador_grafico",
                "resposta": "resposta",
            }
        )

        # Após gerar a resposta final, encerrar o grafo
        construtor_grafo.add_edge("resposta", END)

        return construtor_grafo

    def _compilar_grafo(self, hitl: bool) -> "CompiledStateGraph":
        construtor = self._construir_grafo_text_to_insight(hitl)
        grafo_compilado = construtor.compile(checkpointer=self.memory,
                                             interrupt_before=["espera_humana"])
        grafo_compilado.hitl_classifier_llm = self.llm
        print("[GRAFO] Grafo Text-to-Insight compilado com sucesso!")
        return grafo_compilado

    def app(self):
        return self.grafo_text_to_insight
    def invoke(self, estado: EstadoTextToInsight):
        return self.grafo_text_to_insight.invoke(estado)

    def stream(self, estado: EstadoTextToInsight, config: dict = None):
        """
        Executa o grafo em modo streaming, yieldando estado após cada nó.

        Args:
            estado: Estado inicial
            config: Configurações (ex: recursion_limit)

        Yields:
            Dicts com saída de cada nó
        """
        if config is None:
            config = {}
        return self.grafo_text_to_insight.stream(estado, config)

