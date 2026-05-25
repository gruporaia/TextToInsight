from __future__ import annotations

from typing import Any, Callable

from .graph import Graph
from .runtime import construir_estado_inicial, executar_fluxo, exibir_resultado_console, registrar_resposta_humana
from .utils import salvar_metricas_csv


class InsightEngine:
    """
    Camada de alto nível para executar o fluxo Text-to-Insight.

    Em termos simples:
    - `run(...)` inicia uma nova consulta.
    - `resume(...)` continua uma consulta que ficou pausada em HITL.
    """

    def __init__(self, api_key: str, model: str, db_path: str, hitl: bool = False, show_output: bool = False, enable_graphs: bool = True, enrich_rag: bool = False):
        self._hitl_ativado = hitl
        # `show_output` controla se a engine imprime o resultado final no terminal.
        # Em cenários com CLI, normalmente deixamos False para evitar saída duplicada.
        self._show_output = show_output
        self._enable_graphs = enable_graphs
        self._enrich_rag = enrich_rag
        self._model = model
        self._db_path = db_path
        # O grafo compila os nós/roteadores e guarda memória por thread_id.
        self._grafo = Graph(api_key=api_key, model=self._model, hitl=self._hitl_ativado, enable_graphs=self._enable_graphs, enrich_rag=self._enrich_rag)

        print(f"[CONFIG] HITL: {'ATIVADO' if self._hitl_ativado else 'DESATIVADO'}")
        print(f"[CONFIG] SHOW_OUTPUT: {'ATIVADO' if self._show_output else 'DESATIVADO'}")
        print(f"[CONFIG] GRÁFICOS: {'ATIVADO' if self._enable_graphs else 'DESATIVADO'}")
        print(f"[CONFIG] ENRICH-RAG: {'ATIVADO' if self._enrich_rag else 'DESATIVADO'}")

    def _config(self, thread_id: str) -> dict[str, Any]:
        # O LangGraph usa esse bloco "configurable" para identificar a conversa.
        return {"configurable": {"thread_id": thread_id}}

    def _exibir_inicio(self, pergunta: str) -> None:
        # Banner simples para facilitar leitura no terminal.
        print("=" * 70)
        print("INICIANDO TEXT-TO-INSIGHT")
        print("=" * 70)
        print(f"\nPergunta: {pergunta}\n")
        print("=" * 70)

    def run(
        self,
        thread_id: str,
        query: str,
        on_human_prompt: Callable[[str], str] | None = None,
    ) -> dict[str, Any]:
        """Inicia uma consulta nova dentro de uma thread (sessão)."""
        # on_human_prompt = "função de callback" para HITL.
        # Ela vem de fora da engine (quem chama o método) e recebe a
        # pergunta do agente, retornando a resposta do usuário em texto.
        return self.get_insight(thread_id=thread_id, query=query, on_human_prompt=on_human_prompt)

    def resume(
        self,
        thread_id: str,
        user_response: str,
        on_human_prompt: Callable[[str], str] | None = None,
    ) -> dict[str, Any]:
        """Retoma uma thread pausada em HITL usando a resposta do usuário."""
        return self.get_insight(
            thread_id=thread_id,
            user_response=user_response,
            on_human_prompt=on_human_prompt,
        )

    def get_insight(
        self,
        thread_id: str,
        query: str | None = None,
        user_response: str | None = None,
        on_human_prompt: Callable[[str], str] | None = None,
    ) -> dict[str, Any]:
        """
        Executa ou retoma uma consulta via thread_id.

        Contrato estável:
        - Nova execução: informar `query`.
        - Retomada HITL: informar `user_response` na mesma `thread_id`.
        - Se houver pausa HITL e não houver callback/resposta, retorna status `AWAITING_USER`.

                Sobre `on_human_prompt`:
                - Não é definido dentro desta classe.
                - É passado por quem chama a engine.
                - Exemplo real neste projeto: `text_to_insight/cli.py` usa
                    `_coletar_resposta_humana` e envia essa função para `run(...)`.
        """
        config = self._config(thread_id)
        app = self._grafo.grafo_text_to_insight
        # `snapshot` é uma "foto" do estado atual salvo no checkpointer.
        snapshot = app.get_state(config)

        # Caso 1: a thread estava pausada e o usuário acabou de enviar resposta.
        if snapshot.next and user_response:
            registrar_resposta_humana(app, config, user_response)
            estado_execucao = None
            pergunta_exibicao = (
                snapshot.values.get("pergunta_atual")
                or snapshot.values.get("pergunta_original")
                or snapshot.values.get("pergunta_usuario")
                or "Retomando conversa..."
            )
        # Caso 2: chamada nova (primeira execução para essa pergunta).
        elif query:
            estado_execucao = construir_estado_inicial(query, self._db_path)
            pergunta_exibicao = query
        # Caso 3: a thread já está pausada, mas ainda sem resposta do usuário.
        elif snapshot.next:
            if not self._hitl_ativado:
                # Com HITL desligado, não podemos pedir input humano: bloqueia o fluxo.
                resultado_bloqueio = dict(snapshot.values)
                resultado_bloqueio.update(
                    {
                        "status": "bloqueado_hitl",
                        "erro_execucao": (
                            "Fluxo bloqueado: o planejador solicitou intervenção humana, "
                            "mas o HITL está desativado (--hitl off)."
                        ),
                        "saida_terminal": "[HITL] Bloqueado: intervenção humana necessária com HITL off.",
                    }
                )
                salvar_metricas_csv(resultado_bloqueio, 0.0)
                if self._show_output:
                    exibir_resultado_console(resultado_bloqueio)
                return resultado_bloqueio

            # Com HITL ligado, devolvemos um payload simples para o cliente/CLI
            # decidir como coletar a resposta humana.
            return {
                "status": "AWAITING_USER",
                "message": snapshot.values.get("pergunta_ao_usuario", "Pode confirmar o prosseguimento?"),
                "chat_history": snapshot.values.get("historico_conversa", []),
                "thread_id": thread_id,
            }
        else:
            raise ValueError("Informe `query` para nova execução ou `user_response` para retomada HITL.")

        # Só mostramos o banner quando a execução realmente vai rodar agora.
        self._exibir_inicio(str(pergunta_exibicao))

        # Loop principal de execução do grafo (até finalizar ou pausar em HITL).
        resultado = executar_fluxo(
            grafo_app=app,
            config=config,
            estado_execucao=estado_execucao,
            hitl_ativado=self._hitl_ativado,
            thread_id=thread_id,
            # Aqui a engine repassa o callback para o runtime.
            # Se vier None, o runtime não tenta ler input direto e
            # devolve status AWAITING_USER para o chamador tratar.
            on_human_prompt=on_human_prompt,
        )

        # Exibe saída final se configurado e se não ficou pendente de resposta humana.
        if self._show_output and resultado.get("status") != "AWAITING_USER":
            exibir_resultado_console(resultado)

        return resultado