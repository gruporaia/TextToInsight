"""
Grafo compilado do sistema Text-to-Insight.

Fluxo MVP:
1. Planejador: Decide estratégia (LLM)
2. Esquema: Obtém contexto do banco (SQLite introspection)
3. Agente de Código: Gera SQL (LLM)
4. Executor: Executa SQL no banco real
5. Crítico: Avalia qualidade (LLM)
6. Salvar CSV: Exporta resultado para CSV
7. Roteador Gráfico: Decide se gera visualização (LLM)
8. Gerador Gráfico: Gera gráfico matplotlib (LLM + subprocess)
9. Resposta: Gera resposta em linguagem natural (LLM)
"""

from functools import partial

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from .state import EstadoTextToInsight
from .nodes import (
    nos_nodo_planejador,
    nos_nodo_esquema,
    nos_nodo_retriever,
    nos_nodo_agente_codigo,
    nos_nodo_sandbox,
    nos_nodo_critico,
    nos_nodo_resposta,
    nos_nodo_salvar_csv,
    nos_nodo_gerador_grafico,
    nos_nodo_enrich,
)
from .routers import roteador_sandbox, roteador_planejador, roteador_grafico, roteador_schema
from .model_selection import get_model

def nos_nodo_espera_humana(estado: EstadoTextToInsight):
    """Nó estrutural: serve apenas como breakpoint para o HITL."""
    return estado

class Graph:
    def __init__(self, api_key: str, model: str, hitl: bool = True, enable_graphs: bool = True, enrich_rag: bool = False):
        self.llm = get_model(model, api_key)
        self.memory = MemorySaver()
        self.enable_graphs = enable_graphs
        self.grafo_text_to_insight = self._compilar_grafo(hitl, enrich_rag)

    def _construir_grafo_text_to_insight(self, hitl: bool, enrich_rag: bool) -> StateGraph:
        """
        Constrói e compila o grafo de agentes Text-to-Insight.
        """
        construtor_grafo = StateGraph(EstadoTextToInsight)

        # 1. ADICIONAR NÓS
        construtor_grafo.add_node("planejador", partial(nos_nodo_planejador, llm=self.llm, hitl=hitl))
        construtor_grafo.add_node("espera_humana", nos_nodo_espera_humana)
        construtor_grafo.add_node("esquema", nos_nodo_esquema)
        construtor_grafo.add_node("retriever", nos_nodo_retriever)
        construtor_grafo.add_node("agente_codigo", partial(nos_nodo_agente_codigo, llm=self.llm))
        construtor_grafo.add_node("sandbox", nos_nodo_sandbox)
        construtor_grafo.add_node("critico", partial(nos_nodo_critico, llm=self.llm))
        construtor_grafo.add_node("salvar_csv", nos_nodo_salvar_csv)
        construtor_grafo.add_node("gerador_grafico", partial(nos_nodo_gerador_grafico, llm=self.llm))
        construtor_grafo.add_node("resposta", partial(nos_nodo_resposta, llm=self.llm))

        # 2. ARESTAS FIXAS
        construtor_grafo.add_edge(START, "planejador")
        construtor_grafo.add_edge("espera_humana", "planejador")
        path = 'retriever'
        if enrich_rag:
            construtor_grafo.add_node("enriquecimento_rag", partial(nos_nodo_enrich, llm=self.llm))
            construtor_grafo.add_edge("enriquecimento_rag", "retriever")
            path = 'enriquecimento_rag'

        construtor_grafo.add_edge("retriever", "planejador")
        construtor_grafo.add_edge("agente_codigo", "sandbox")

        # Gerador de gráfico sempre vai para resposta (sucesso ou falha)
        construtor_grafo.add_edge("gerador_grafico", "resposta")

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
                "espera_humana": "espera_humana",
                "esquema": "esquema",
                "agente_codigo": "agente_codigo",
                "critico": "critico",
                "retriever": path,
                "fim": END,
            }
        )

        construtor_grafo.add_conditional_edges(
            "esquema",
            roteador_schema,
            {
                "retriever": "retriever",
                "enriquecimento_rag": path,
            }
        )


        MAX_TENTATIVAS_CRITICO = 3

        def roteador_critico(estado: EstadoTextToInsight) -> str:
            status = estado.get("status", "")
            tentativas = estado.get("tentativas_loop", 0)
            
            next_step = "salvar_csv" if self.enable_graphs else "resposta"
            
            # Se aprovado, enviar para proximo passo
            if status == "aprovado":
                return next_step
            # Se atingiu limite de tentativas, encerrar mesmo reprovado
            if tentativas >= MAX_TENTATIVAS_CRITICO:
                print(f"[ROTEADOR_CRITICO] Limite de {MAX_TENTATIVAS_CRITICO} tentativas atingido → {next_step} (forçado)")
                return next_step
            return "planejador"

        construtor_grafo.add_conditional_edges(
           "critico",
            roteador_critico,
            {
                "planejador": "planejador",
                "salvar_csv": "salvar_csv",
                "resposta": "resposta",
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

    def _compilar_grafo(self, hitl: bool, enrich_rag: bool) -> "CompiledStateGraph":
        construtor = self._construir_grafo_text_to_insight(hitl, enrich_rag)
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

