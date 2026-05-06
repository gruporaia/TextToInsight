from __future__ import annotations

import time
from typing import Any, Callable

from .utils import salvar_metricas_csv

HITL_AWAITING_STATUS = "AWAITING_USER"
HITL_BLOCKED_STATUS = "bloqueado_hitl"


def construir_estado_inicial(pergunta: str, db_path: str) -> dict[str, Any]:
    """Cria o estado inicial padrão para uma execução do grafo."""
    return {
        "pergunta_usuario": pergunta,
        "contexto_schema": "",
        "sql_gerada": "",
        "saida_terminal": "",
        "feedback_critico": "",
        "erro_execucao": "",
        "historico_conversa": [],
        "status": "iniciado",
        "tentativas_loop": 0,
        "db_path": db_path,
        "espera_humana": False,
        "historico_tentativas": [],
    }


def exibir_resultado_console(resultado: dict[str, Any]) -> None:
    """Exibe o resultado final de forma consistente entre CLI e engine."""
    print("\n" + "=" * 70)
    print("EXECUCAO CONCLUIDA")
    print("=" * 70)

    print(f"\nStatus Final: {resultado.get('status', 'desconhecido').upper()}")
    print(f"Total de Tentativas: {resultado.get('tentativas_loop', 0)}")

    print("\n" + "-" * 70)
    print("SQL GERADA:")
    print("-" * 70)
    sql = str(resultado.get("sql_gerada", "")).strip()
    print(sql if sql else "[Nenhuma SQL gerada]")

    print("\n" + "-" * 70)
    print("SAIDA DA EXECUCAO:")
    print("-" * 70)
    saida = str(resultado.get("saida_terminal", "")).strip()
    print(saida if saida else "[Nenhuma saida]")

    print("\n" + "-" * 70)
    print("RESULTADO (preview):")
    print("-" * 70)
    preview = resultado.get("linhas_resultado_preview", []) or []
    total = int(resultado.get("total_linhas_resultado", 0) or 0)
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
    feedback = str(resultado.get("feedback_critico", "")).strip()
    print(feedback if feedback else "[Nenhum feedback]")

    print("\n" + "-" * 70)
    print("RESPOSTA NATURAL AO USUARIO:")
    print("-" * 70)
    resposta_natural = str(resultado.get("resposta_natural", "")).strip()
    print(resposta_natural if resposta_natural else "[Nenhuma resposta natural]")

    print("\n" + "=" * 70 + "\n")


def _resultado_aguardando_usuario(snapshot_values: dict[str, Any], thread_id: str) -> dict[str, Any]:
    return {
        "status": HITL_AWAITING_STATUS,
        "message": snapshot_values.get("pergunta_ao_usuario", "Pode confirmar o prosseguimento?"),
        "chat_history": snapshot_values.get("historico_conversa", []),
        "thread_id": thread_id,
    }


def _resultado_hitl_bloqueado(snapshot_values: dict[str, Any]) -> dict[str, Any]:
    resultado_final = dict(snapshot_values)
    resultado_final.update(
        {
            "status": HITL_BLOCKED_STATUS,
            "erro_execucao": (
                "Fluxo bloqueado: o planejador solicitou intervenção humana, "
                "mas o HITL está desativado (--hitl off)."
            ),
            "saida_terminal": "[HITL] Bloqueado: intervenção humana necessária com HITL off.",
        }
    )
    return resultado_final


def registrar_resposta_humana(grafo_app: Any, config: dict[str, Any], user_response: str) -> None:
    """Atualiza o estado da thread com a resposta humana para retomar o fluxo."""
    snapshot = grafo_app.get_state(config)
    historico = list(snapshot.values.get("historico_conversa", []))
    pergunta_agente = snapshot.values.get("pergunta_ao_usuario", "Pode confirmar o prosseguimento?")
    historico.append((f"ai: {pergunta_agente}", f"user: {user_response}"))
    grafo_app.update_state(config, {"historico_conversa": historico, "espera_humana": False})


def executar_fluxo(
    grafo_app: Any,
    config: dict[str, Any],
    estado_execucao: dict[str, Any] | None,
    hitl_ativado: bool,
    thread_id: str,
    on_human_prompt: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Executa o loop de runtime do grafo até finalizar ou aguardar input humano."""
    lat_inicio = time.perf_counter()

    while True:
        for _ in grafo_app.stream(estado_execucao, config, stream_mode="values"):
            pass

        snapshot = grafo_app.get_state(config)

        if not snapshot.next:
            resultado_final = snapshot.values
            salvar_metricas_csv(resultado_final, time.perf_counter() - lat_inicio)
            return resultado_final

        if "espera_humana" in snapshot.next:
            if not hitl_ativado:
                print("\n[HITL] Intervenção humana solicitada, mas o modo HITL está DESATIVADO.")
                print("[HITL] Encerrando execução com status de bloqueio.")
                resultado_final = _resultado_hitl_bloqueado(snapshot.values)
                salvar_metricas_csv(resultado_final, time.perf_counter() - lat_inicio)
                return resultado_final

            pergunta_agente = snapshot.values.get("pergunta_ao_usuario", "Pode confirmar o prosseguimento?")
            if on_human_prompt is None:
                return _resultado_aguardando_usuario(snapshot.values, thread_id)

            user_response = on_human_prompt(pergunta_agente)
            if user_response is None:
                return _resultado_aguardando_usuario(snapshot.values, thread_id)

            registrar_resposta_humana(grafo_app, config, str(user_response))
            estado_execucao = None
