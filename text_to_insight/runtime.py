from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

from .utils import salvar_metricas_csv

HITL_AWAITING_STATUS = "AWAITING_USER"
HITL_BLOCKED_STATUS = "bloqueado_hitl"

PROMPT_CLASSIFICADOR_HITL = """Voce e um classificador de respostas de usuario em um fluxo HITL.

Tarefa:
- Dado a pergunta original, a pergunta atual e a resposta do usuario,
  classifique se o usuario fez uma NOVA PERGUNTA ou se apenas ESCLARECEU
  algo da pergunta atual.

Regras:
- Responda ESTRITAMENTE em JSON valido, sem markdown.
- Campos obrigatorios: "tipo" e "pergunta_normalizada".
- "tipo" deve ser: "nova_pergunta" ou "esclarecimento".
- Se for "esclarecimento", mantenha "pergunta_normalizada" como a pergunta atual.
- Se for "nova_pergunta", normalize a nova pergunta a partir da resposta do usuario.

Entrada:
Pergunta original: "{pergunta_original}"
Pergunta atual: "{pergunta_atual}"
Resposta do usuario: "{user_response}"

Retorne apenas:
{{"tipo":"...","pergunta_normalizada":"..."}}
"""


def construir_estado_inicial(pergunta: str, db_path: str) -> dict[str, Any]:
    """Cria o estado inicial padrão para uma execução do grafo."""
    return {
        "pergunta_original": pergunta,
        "pergunta_atual": pergunta,
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
    }


def _limpar_json_markdown(conteudo: str) -> str:
    conteudo_limpo = conteudo.strip()
    if conteudo_limpo.startswith("```json"):
        conteudo_limpo = conteudo_limpo[7:]
        if conteudo_limpo.endswith("```"):
            conteudo_limpo = conteudo_limpo[:-3]
    elif conteudo_limpo.startswith("```"):
        conteudo_limpo = conteudo_limpo[3:]
        if conteudo_limpo.endswith("```"):
            conteudo_limpo = conteudo_limpo[:-3]
    return conteudo_limpo.strip()


def _heuristica_nova_pergunta(resposta: str) -> bool:
    texto = (resposta or "").strip()
    if not texto:
        return False

    texto_lower = texto.lower()
    confirmacoes = {
        "sim",
        "ok",
        "certo",
        "pode",
        "pode prosseguir",
        "pode seguir",
        "continue",
        "prosseguir",
        "segue",
    }
    if texto_lower in confirmacoes or texto_lower.startswith(("sim ", "ok ", "certo ")):
        return False

    if texto_lower.endswith("?"):
        return True

    padroes = [
        r"\bquero saber\b",
        r"\bgostaria de saber\b",
        r"\bpreciso saber\b",
        r"\bme diga\b",
        r"\bme informe\b",
        r"\bme mostre\b",
        r"\bqual\b",
        r"\bquais\b",
        r"\bquantos?\b",
        r"\bquanto\b",
        r"\bquando\b",
        r"\bonde\b",
        r"\bcomo\b",
        r"\bquem\b",
        r"\bpor que\b",
        r"\bporque\b",
        r"\bnova pergunta\b",
    ]
    return any(re.search(padrao, texto_lower) for padrao in padroes)


def classificar_resposta_usuario(
    pergunta_original: str,
    pergunta_atual: str,
    user_response: str,
    llm: Any | None = None,
) -> dict[str, str]:
    pergunta_original = (pergunta_original or "").strip()
    pergunta_atual = (pergunta_atual or "").strip()
    resposta = (user_response or "").strip()

    if llm is not None:
        prompt = PROMPT_CLASSIFICADOR_HITL.format(
            pergunta_original=pergunta_original,
            pergunta_atual=pergunta_atual,
            user_response=resposta,
        )
        try:
            resposta_llm = llm.invoke(prompt)
            conteudo_bruto = _limpar_json_markdown(str(getattr(resposta_llm, "content", "")))
            dados = json.loads(conteudo_bruto)
            tipo = str(dados.get("tipo", "")).strip().lower()
            pergunta_normalizada = str(dados.get("pergunta_normalizada", "")).strip()
            if tipo in {"nova_pergunta", "esclarecimento"}:
                if tipo == "nova_pergunta" and resposta:
                    pergunta_normalizada = resposta
                if not pergunta_normalizada:
                    pergunta_normalizada = pergunta_atual if tipo == "esclarecimento" else resposta
                if pergunta_normalizada:
                    return {
                        "tipo": tipo,
                        "pergunta_normalizada": pergunta_normalizada,
                    }
        except Exception:
            pass

    if _heuristica_nova_pergunta(resposta):
        pergunta_normalizada = resposta if resposta else pergunta_atual or pergunta_original
        return {"tipo": "nova_pergunta", "pergunta_normalizada": pergunta_normalizada}

    return {
        "tipo": "esclarecimento",
        "pergunta_normalizada": pergunta_atual or pergunta_original or resposta,
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
    pergunta_original = snapshot.values.get("pergunta_original", "")
    pergunta_atual = snapshot.values.get("pergunta_atual", "")
    if not pergunta_atual:
        pergunta_atual = snapshot.values.get("pergunta_usuario", "")

    llm = getattr(grafo_app, "hitl_classifier_llm", None)
    if llm is None:
        llm = getattr(grafo_app, "llm", None)

    classificacao = classificar_resposta_usuario(
        pergunta_original=pergunta_original,
        pergunta_atual=pergunta_atual,
        user_response=user_response,
        llm=llm,
    )

    updates = {
        "historico_conversa": historico,
        "espera_humana": False,
    }

    if not pergunta_original:
        updates["pergunta_original"] = pergunta_atual or user_response

    if classificacao.get("tipo") == "nova_pergunta":
        updates.update(
            {
                "pergunta_atual": classificacao.get("pergunta_normalizada", pergunta_atual),
                "sql_gerada": "",
                "feedback_critico": "",
                "erro_execucao": "",
                "tentativas_loop": 0,
                "saida_terminal": "",
                "status": "iniciado",
            }
        )

    grafo_app.update_state(config, updates)


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
