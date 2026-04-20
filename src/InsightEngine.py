import time
import os
from dotenv import load_dotenv
from langgraph.graph import StateGraph
from src.graph import Graph
from src.utils import salvar_metricas_csv

class InsightEngine:
    def __init__(self, api_key, model, db_path, hitl=False, show_output=False):
        self._hitl_ativado = hitl
        self._show_output = show_output
        print(f"[CONFIG] HITL: {'ATIVADO' if self._hitl_ativado else 'DESATIVADO'}")
        print(f"[CONFIG] SHOW_OUTPUT: {'ATIVADO' if self._show_output else 'DESATIVADO'}")

        self._model = model #gemini-2.5-flash/gpt-5-nano 
        self._db_path = db_path #"data/olist_relational.db"

        self._grafo = Graph(api_key, self._model, self._hitl_ativado)
    
    def _exibir_resultado(self, resultado: dict) -> None:
        """Exibe o resultado final da execução de forma formatada."""
        print("\n" + "=" * 70)
        print("EXECUCAO CONCLUIDA")
        print("=" * 70)

        print(f"\nStatus Final: {resultado.get('status', 'desconhecido').upper()}")
        print(f"Total de Tentativas: {resultado.get('tentativas_loop', 0)}")

        print("\n" + "-" * 70)
        print("SQL GERADA:")
        print("-" * 70)
        sql = resultado.get("sql_gerada", "").strip()
        print(sql if sql else "[Nenhuma SQL gerada]")

        print("\n" + "-" * 70)
        print("SAIDA DA EXECUCAO:")
        print("-" * 70)
        saida = resultado.get("saida_terminal", "").strip()
        print(saida if saida else "[Nenhuma saida]")

        print("\n" + "-" * 70)
        print("RESULTADO (preview):")
        print("-" * 70)
        preview = resultado.get("linhas_resultado_preview", [])
        total = resultado.get("total_linhas_resultado", 0)
        if preview:
            for row in preview[:10]:
                print(row)
            if total > 10:
                print(f"... ({total - 10} linhas omitidas)")
        else:
            print("[Nenhum resultado]")

        print("\n" + "-" * 70)
        print("FEEDBACK DO CRITICO:")
        print("-" * 70)
        feedback = resultado.get("feedback_critico", "").strip()
        print(feedback if feedback else "[Nenhum feedback]")

        # Se o nó de resposta final gerou uma resposta em linguagem natural, exibi-la
        resposta_natural = resultado.get("resposta_natural", "").strip()
        print("\n" + "-" * 70)
        print("RESPOSTA NATURAL AO USUARIO:")
        print("-" * 70)
        print(resposta_natural)

        print("\n" + "=" * 70 + "\n")

    def get_insight(self, thread_id, query=None, user_response=None):
        """Executa uma consulta através do grafo Text-to-Insight."""
        config = {"configurable": {"thread_id": thread_id}}

        snapshot = self._grafo.grafo_text_to_insight.get_state(config)
        
        if snapshot.next and user_response:
            historico = snapshot.values.get("historico_conversa", [])
            pergunta_ai = snapshot.values.get("pergunta_ao_usuario", "")
            historico.append((f"ai: {pergunta_ai}", f"user: {user_response}"))
            
            self._grafo.grafo_text_to_insight.update_state(
                config, {"historico_conversa": historico, "espera_humana": False}
            )
            estado_execucao = None
        else:
            estado_execucao = {
                "pergunta_usuario": query,
                "contexto_schema": "",
                "sql_gerada": "",
                "saida_terminal": "",
                "feedback_critico": "",
                "erro_execucao": "",
                "historico_conversa": [],
                "status": "iniciado",
                "tentativas_loop": 0,
                "db_path": self._db_path,
                "espera_humana": False,
            }

        pergunta_exibicao = query if query else snapshot.values.get("pergunta_usuario", "Retomando conversa...")

        print("=" * 70)
        print("INICIANDO TEXT-TO-INSIGHT")
        print("=" * 70)
        print(f"\nPergunta: {pergunta_exibicao}\n")
        print("=" * 70)

        # Intervalo de cálculo da latência da consulta no grafo. Optei por considerar somente
        # o tempo em que o grafo de fato está rodando, então não incluo o tempo que leva as linhas
        # anteriores na main ou antes desse trecho.
        lat_inicio = time.perf_counter()

        while True:
            for evento in self._grafo.grafo_text_to_insight.stream(estado_execucao, config, stream_mode="values"):
                pass

            snapshot = self._grafo.grafo_text_to_insight.get_state(config)

            if not snapshot.next:
                resultado_final = snapshot.values
                lat_fim = time.perf_counter()
                latencia_consulta = lat_fim - lat_inicio
                salvar_metricas_csv(resultado_final, latencia_consulta)
                if self._show_output:
                    self._exibir_resultado(resultado_final)
                return resultado_final

            if "espera_humana" in snapshot.next:
                pergunta_agente = snapshot.values.get("pergunta_ao_usuario", "Pode confirmar o prosseguimento?")
                historico_atual = snapshot.values.get("historico_conversa", [])
                
                #discutir com na próxima reunião qual a necessidade de manter esse bloco/mudar a estratégia de toggle de remover completamente ṕara bloquear o fluxo
                if not self._hitl_ativado:
                    print("\n[HITL] Intervenção humana solicitada, mas o modo HITL está DESATIVADO.")
                    print("[HITL] Encerrando execução com status de bloqueio.")

                    resultado_final = dict(snapshot.values)
                    resultado_final.update({
                        "status": "bloqueado_hitl",
                        "erro_execucao": (
                            "Fluxo bloqueado: o planejador solicitou intervenção humana, "
                            "mas o HITL está desativado (--hitl off)."
                        ),
                        "saida_terminal": "[HITL] Bloqueado: intervenção humana necessária com HITL off.",
                    })

                    lat_fim = time.perf_counter()
                    latencia_consulta = lat_fim - lat_inicio
                    salvar_metricas_csv(resultado_final, latencia_consulta)
                    return resultado_final
                
                return {
                "status": "AWAITING_USER",
                "message": pergunta_agente,
                "chat_history": historico_atual,
                "thread_id": thread_id
                }
                
                #Essa implementação anterior era mais simples, lembrar de avaliar complexidade de uso da lib
                # print(f"\n[HITL]: {pergunta_agente}")

                # resposta = input("[RESPOSTA USUARIO]: ")
                # historico_atual.append(("ai: "+pergunta_agente, "\nuser: "+resposta))

                # self._grafo.grafo_text_to_insight.update_state(config, {"historico_conversa": historico_atual, "espera_humana": False})

                # estado_inicial = None

if __name__ == "__main__":
    load_dotenv()
    API_KEY = os.getenv("OPENAI_API_KEY") #GOOGLE_API_KEY/OPENAI_API_KEY
    MODEL = "gpt-5-nano" #gemini-2.5-flash/gpt-5-nano 
    DB_PATH = "data/olist_relational.db"
    engine = InsightEngine(API_KEY, MODEL, DB_PATH, hitl=True, show_output=True)
    resultado = engine.get_insight("thread_1", "Liste os itens mais quentes do mercado") #Quantos pedidos existem no banco?
    if resultado.get("status") == "AWAITING_USER":
        print("\n[HITL] Aguardando resposta do usuário...")
        print(f"Pergunta do agente: {resultado.get('message')}")
        print(f"Histórico de conversa até agora: {resultado.get('chat_history')}")
        engine.get_insight("thread_1", user_response="Sim, pode prosseguir, faça todas as assunções necessárias.")