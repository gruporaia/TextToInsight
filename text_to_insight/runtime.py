from __future__ import annotations

import csv
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from tabulate import tabulate

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
        "linhas_resultado_completo": [],
        "historico_tentativas": [],
    }


def _montar_saida_resultado_terminal(resultado: dict[str, Any]) -> str:
    """Monta o texto do bloco de resultado para exibição no terminal."""
    linhas = resultado.get("linhas_resultado_completo", []) or []
    if not linhas:
        linhas = resultado.get("linhas_resultado_preview", []) or []
    total = int(resultado.get("total_linhas_resultado", 0) or 0)

    if not linhas:
        return "[Nenhum resultado]"

    colunas = list(linhas[0].keys()) if isinstance(linhas[0], dict) else []
    if not colunas:
        return "[Resultado indisponivel para exibicao]"

    def _formatar_tabela(amostras: list[dict[str, Any]]) -> str:
        return tabulate(amostras, headers="keys", tablefmt="grid", showindex=False)

    partes: list[str] = []

    if len(linhas) <= 5:
        partes.append(_formatar_tabela(linhas))
    else:
        partes.append(_formatar_tabela(linhas[:3]))
        partes.append(f"... (omitted {len(linhas) - 5} rows) ...")
        partes.append(_formatar_tabela(linhas[-2:]))

    partes.append(f"Total de linhas retornadas: {total}")
    return "\n".join(partes)


def salvar_resultado_csv(resultado: dict[str, Any], pasta_resultados: Path | None = None) -> Path | None:
    """Salva o resultado completo em CSV quando houver linhas para exportar."""
    linhas = resultado.get("linhas_resultado_completo", []) or []
    if not linhas:
        return None

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    resultados_dir = pasta_resultados or (Path(__file__).parent.parent / "results")
    resultados_dir.mkdir(exist_ok=True)
    csv_path = resultados_dir / f"query_{timestamp}.csv"

    with csv_path.open("w", newline="", encoding="utf-8") as arquivo_csv:
        writer = csv.DictWriter(arquivo_csv, fieldnames=list(linhas[0].keys()))
        writer.writeheader()
        writer.writerows(linhas)

    return csv_path


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

    # Nova lógica: Exibir resultado como DataFrame
    print("\n" + "-" * 70)
    print("RESULTADO:")
    print("-" * 70)

    print(_montar_saida_resultado_terminal(resultado))

    csv_path = salvar_resultado_csv(resultado)
    if csv_path is not None:
        total = int(resultado.get("total_linhas_resultado", 0) or 0)
        print(f"\n✓ Resultados completos salvos em: {csv_path.as_posix()} ({total} linhas)")

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
