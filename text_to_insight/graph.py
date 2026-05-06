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
from langgraph.checkpoint.memory import MemorySaver

from .state import EstadoTextToInsight
from .nodes import (
    nos_nodo_planejador,
    nos_nodo_esquema,
    nos_nodo_agente_codigo,
    nos_nodo_sandbox,
    nos_nodo_critico,
    nos_nodo_resposta,
)
from .routers import roteador_sandbox, roteador_planejador
from .model_selection import get_model

def nos_nodo_espera_humana(estado: EstadoTextToInsight):
    """Nó estrutural: serve apenas como breakpoint para o HITL."""
    return estado

class Graph:
    def __init__(self, api_key: str, model: str, hitl: bool = True):
        self.llm = get_model(model, api_key)
        self.memory = MemorySaver()
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
        construtor_grafo.add_node("agente_codigo", partial(nos_nodo_agente_codigo, llm=self.llm))
        construtor_grafo.add_node("sandbox", nos_nodo_sandbox)
        construtor_grafo.add_node("critico", partial(nos_nodo_critico, llm=self.llm))
        construtor_grafo.add_node("resposta", partial(nos_nodo_resposta, llm=self.llm))

        # 2. ARESTAS FIXAS
        construtor_grafo.add_edge(START, "planejador")
        construtor_grafo.add_edge("espera_humana", "planejador")
        construtor_grafo.add_edge("esquema", "planejador")
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
                "espera_humana": "espera_humana",
                "esquema": "esquema",
                "agente_codigo": "agente_codigo",
                "critico": "critico",
                "planejador": "planejador",
                "fim": END,
            }
        )

        MAX_TENTATIVAS_CRITICO = 3

        def roteador_critico(estado: EstadoTextToInsight) -> str:
            status = estado.get("status", "")
            tentativas = estado.get("tentativas_loop", 0)
            # Se aprovado, enviar para nó de resposta
            if status == "aprovado":
                return "resposta"
            # Se atingiu limite de tentativas, encerrar mesmo reprovado
            if tentativas >= MAX_TENTATIVAS_CRITICO:
                print(f"[ROTEADOR_CRITICO] Limite de {MAX_TENTATIVAS_CRITICO} tentativas atingido → resposta (forçado)")
                return "resposta"
            return "planejador"

        construtor_grafo.add_conditional_edges(
           "critico",
            roteador_critico,
            {
                "planejador": "planejador",
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
