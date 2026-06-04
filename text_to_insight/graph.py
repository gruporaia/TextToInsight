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
from langgraph.types import Send

from .state import EstadoTextToInsight, EstadoCandidato
from .nodes import (
    nos_nodo_planejador,
    nos_nodo_esquema,
    nos_nodo_retriever,
    nos_nodo_sandbox,
    nos_nodo_critico,
    nos_nodo_resposta,
    nos_nodo_salvar_csv,
    nos_nodo_gerador_grafico,
    nos_nodo_votacao,
    nos_nodo_explorador,
)
from .nodes.code_agent.code_agent import llm_gera_sql_candidato
from .nodes.sandbox import sandbox_validacao_candidato
from .routers import roteador_sandbox, roteador_planejador, roteador_grafico, roteador_fan_out, roteador_votacao
from .model_selection import get_model

def nos_nodo_espera_humana(estado: EstadoTextToInsight):
    """Nó estrutural: serve apenas como breakpoint para o HITL."""
    return estado

class Graph:
    def __init__(self, api_key: str, model: str, hitl: bool = True, enable_graphs: bool = True):
        self.llm = get_model(model, api_key)
        self.memory = MemorySaver()
        self.enable_graphs = enable_graphs
        self.grafo_text_to_insight = self._compilar_grafo(hitl)

    def _construir_subgrafo_gerador_candidato(self) -> StateGraph:
        """
        Constrói o sub-grafo para geração e refinement de um candidato SQL individual.
        
        Este sub-grafo é invocado 5 vezes em paralelo via Send (Map-Reduce).
        
        Estado de entrada: EstadoCandidato com:
        - indice: 0-4 (para prompt diversity)
        - pergunta: pergunta do usuário
        - schema: schema do banco
        - db_path: caminho para SQLite
        - historico_tentativas: tentativas anteriores (context-aware)
        - sql: "" (vazio inicialmente)
        - tentativas_refinamento: 0
        
        Estado de saída: EstadoCandidato com resultado_execucao + assinatura_resultado
        """
        subgrafo = StateGraph(EstadoCandidato)
        
        # Nó 1: Gerar SQL (Map)
        def nodo_llm_candidato(estado_candidato: EstadoCandidato) -> dict:
            """Gera SQL para o candidato usando LLM com diversidade térmica."""
            # Agora a função do code_agent puxa o que precisa direto do estado isolado
            atualizado = llm_gera_sql_candidato(
                estado_candidato=estado_candidato,
                llm=self.llm,
            )
            return atualizado


        # Nó 2: Validar/Executar (Reduce)
        def nodo_sandbox_candidato(estado_candidato: EstadoCandidato) -> dict:
            """Executa SQL no sandbox e calcula assinatura."""
            atualizado = sandbox_validacao_candidato(
                estado_candidato=estado_candidato
            )
            return atualizado
        

        # Nó 3: Decisão de retry
        def decisor_retry(estado_candidato: EstadoCandidato) -> str:
            """Avalia se deve retentar (erro retry-able e tentativas < 3)."""
            tentativas = estado_candidato.get("tentativas_refinamento", 0)
            erro = estado_candidato.get("erro", "")
            valido = estado_candidato.get("valido", False)
            temp = estado_candidato.get("temperatura", 0.0)
            
            if valido:
                print(f"[CANDIDATO Temp {temp}] ✅ Validado → fim")
                return "fim"
            
            if erro and tentativas < 5:
                is_syntax = "syntax" in erro.lower() or "near" in erro.lower()
                is_timeout = "timeout" in erro.lower()
                if is_syntax or is_timeout:
                    print(f"[CANDIDATO Temp {temp}] ⚠️ Retentar ({tentativas+1}/3)")
                    return "llm_candidato"
            
            print(f"[CANDIDATO Temp {temp}] ⏹️ Finalizar (falha permanente)")
            return "fim"

        
        # Adicionar nós
        subgrafo.add_node("llm_candidato", nodo_llm_candidato)
        subgrafo.add_node("sandbox_candidato", nodo_sandbox_candidato)
        
        # Adicionar arestas
        subgrafo.add_edge(START, "llm_candidato")
        subgrafo.add_edge("llm_candidato", "sandbox_candidato")
        
        # Condicional: retry ou fim
        subgrafo.add_conditional_edges(
            "sandbox_candidato",
            decisor_retry,
            {
                "llm_candidato": "llm_candidato",
                "fim": END,
            }
        )
        
        print("[GRAPH] Sub-grafo gerador_candidato construído.")
        return subgrafo

    def _construir_grafo_text_to_insight(self, hitl: bool) -> StateGraph:
        construtor_grafo = StateGraph(EstadoTextToInsight)

        # 1. ADICIONAR NÓS
        construtor_grafo.add_node("planejador", partial(nos_nodo_planejador, llm=self.llm, hitl=hitl))
        construtor_grafo.add_node("espera_humana", nos_nodo_espera_humana)
        construtor_grafo.add_node("esquema", nos_nodo_esquema)
        construtor_grafo.add_node("retriever", nos_nodo_retriever)
        
        # ✅ ReFoRCE: Sub-grafo para geração paralela de candidatos
        subgrafo_candidato = self._construir_subgrafo_gerador_candidato()
        subgrafo_compilado = subgrafo_candidato.compile()
        construtor_grafo.add_node("gerador_candidato", subgrafo_compilado)
        
        # ✅ ReFoRCE: Nó que paraleliza 5 candidatos
        def nodo_fan_out(estado: EstadoTextToInsight) -> dict:
            """Cria 5 Send objects, executa em paralelo agregando resultados."""
            sends = roteador_fan_out(estado)
            
            # Executar cada Send object através do sub-grafo compilado
            candidatos_saida = []
            for send_obj in sends:
                estado_candidato = send_obj.arg
                # Invocar sub-grafo com o estado do candidato
                resultado_candidato = subgrafo_compilado.invoke(estado_candidato)
                candidatos_saida.append(resultado_candidato)
            
            # Retornar estado atualizado com os candidatos agregados
            estado_atualizado = estado.copy() if isinstance(estado, dict) else dict(estado)
            estado_atualizado["candidatos"] = candidatos_saida
            
            return estado_atualizado
        
        construtor_grafo.add_node("fan_out", nodo_fan_out)
        
        # ✅ ReFoRCE: Nós de votação e exploração
        construtor_grafo.add_node("votacao", nos_nodo_votacao)
        construtor_grafo.add_node("explorador", partial(nos_nodo_explorador, llm=self.llm))
        
        # Nós antigos (mantidos para fallback se necessário)
        construtor_grafo.add_node("sandbox", nos_nodo_sandbox)
        construtor_grafo.add_node("critico", partial(nos_nodo_critico, llm=self.llm))
        construtor_grafo.add_node("salvar_csv", nos_nodo_salvar_csv)
        construtor_grafo.add_node("gerador_grafico", partial(nos_nodo_gerador_grafico, llm=self.llm))
        construtor_grafo.add_node("resposta", partial(nos_nodo_resposta, llm=self.llm))

        # 2. ARESTAS FIXAS
        construtor_grafo.add_edge(START, "planejador")
        construtor_grafo.add_edge("espera_humana", "planejador")
        construtor_grafo.add_edge("esquema", "retriever")
        construtor_grafo.add_edge("retriever", "planejador")
        
        # ✅ ReFoRCE: Após fan-out parallelizar 5 candidatos, agregar e votar
        construtor_grafo.add_edge("fan_out", "votacao")

        # Gerador de gráfico sempre vai para resposta (sucesso ou falha)
        construtor_grafo.add_edge("gerador_grafico", "resposta")
        
        # ✅ ReFoRCE: Após gerador_candidato, Fan-in automático + votação
        construtor_grafo.add_edge("gerador_candidato", "votacao")

        # 3. ARESTAS CONDICIONAIS
        construtor_grafo.add_conditional_edges(
            "sandbox",
            roteador_sandbox,
            {
                "critico": "critico",
                "planejador": "planejador",
            }
        )

        # ✅ ReFoRCE: Roteador planejador modificado para incluir transição Fan-out
        def roteador_planejador_reforcado(estado: EstadoTextToInsight) -> str:
            """
            Roteador planejador estendido para ReFoRCE.
            - Se pronto_codificacao → Fan-out para 5 candidatos
            """
            contexto = estado.get("contexto_schema", "")
            status = estado.get("status", "")
            esperar = estado.get("espera_humana", False)

            print(f"[ROTEADOR_PLANEJADOR_REFORCADO] Status: {status}")

            if esperar:
                return "espera_humana"

            if not contexto:
                return "esquema"

            # ✅ NOVO: Se pronto_codificacao, usar Fan-out para 5 candidatos (ReFoRCE)
            if status in ("pronto_codificacao", "revisando_estrategia"):
                print("[ROTEADOR_PLANEJADOR_REFORCADO] → gerador_candidato (ReFoRCE Map-Reduce)")
                return "gerador_candidato_fan_out"

            if status == "aprovado":
                return "fim"

            return "planejador"

        construtor_grafo.add_conditional_edges(
            "planejador",
            roteador_planejador_reforcado,
            {
                "espera_humana": "espera_humana",
                "esquema": "esquema",
                "gerador_candidato_fan_out": "fan_out",  # ✅ Nó que retorna 5 Send objects
                "critico": "critico",
                "planejador": "planejador",
                "fim": END,
            }
        )

        # ✅ ReFoRCE: Roteador após votação (Reduce + Consensus)
        construtor_grafo.add_conditional_edges(
            "votacao",
            roteador_votacao,
            {
                "salvar_csv": "salvar_csv",
                "nos_nodo_explorador": "explorador",  # Exploração de divergências
                "resposta": "resposta",  # Fallback
            }
        )

        # ✅ ReFoRCE: Se exploração detecta problema, volta ao Fan-out para nova rodada
        def roteador_explorador(estado: EstadoTextToInsight) -> str:
            """Após exploração, decide: nova rodada de 5 candidatos ou fallback."""
            rodadas = estado.get("rodadas_exploracao", 0)
            max_rodadas = 5
            
            if rodadas < max_rodadas:
                print(f"[ROTEADOR_EXPLORADOR] Rodada {rodadas + 1}/{max_rodadas} → fan_out (5 novos candidatos)")
                return "fan_out"
            else:
                print(f"[ROTEADOR_EXPLORADOR] Limite atingido ({rodadas}/{max_rodadas}) → resposta (fallback)")
                return "resposta"
        
        construtor_grafo.add_conditional_edges(
            "explorador",
            roteador_explorador,
            {
                "fan_out": "fan_out",         # ✅ CORRIGIDO: Voltar ao fan_out, não gerador_candidato
                "resposta": "resposta",
            }
        )

        MAX_TENTATIVAS_CRITICO = 5

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

