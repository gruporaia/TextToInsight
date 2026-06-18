"""Adaptador entre o InsightEngine (core) e qualquer interface de usuario.

Esta e a unica ponte entre o InsightEngine e as interfaces (CLI, web, futuras).
Ela faz duas coisas: orquestra o ciclo completo de uma pergunta (incluindo pausas
HITL) e devolve o resultado num formato publico, limpo e estavel. Interfaces
consomem apenas este adapter, nunca o engine diretamente.

Principios:
- Orquestrar, nao parsear input: o adapter recebe sempre uma string de linguagem
  natural; flags e configuracao sao do InsightEngine, nao daqui.
- Descrever, nao agir: para graficos e CSV o adapter garante que o caminho do
  arquivo esta presente e valido. Nao abre arquivos, nao encoda imagens, nao
  imprime tabelas. Cada interface decide o que fazer com a informacao.
"""

from __future__ import annotations

from typing import Any, Callable

from .runtime import salvar_resultado_csv

# Callback que a interface injeta para obter a resposta do usuario durante o HITL.
# Recebe a mensagem/pergunta do agente e devolve a resposta (string).
OnInputNeeded = Callable[[str], str]

# Campos publicos sempre presentes no output normalizado.
_CAMPOS_PUBLICOS = frozenset(
    {"status", "answer", "data", "chart", "csv", "message", "thread_id"}
)


class UserInputRequired(Exception):
    """Sinaliza que o fluxo pausou em HITL e precisa de input do usuario.

    A interface web pode lanca-la de dentro do callback ``on_input_needed`` para
    devolver um HTTP 202 e aguardar o proximo request; o ciclo retoma quando a
    interface chamar ``resume()`` novamente.
    """

    def __init__(self, message: str, thread_id: str):
        self.message = message
        self.thread_id = thread_id
        super().__init__(message)


def ask(
    engine: Any,
    thread_id: str,
    query: str,
    on_input_needed: OnInputNeeded | None = None,
) -> dict[str, Any]:
    """Inicia uma pergunta nova e roda o ciclo ate um status final.

    Quem passa ``on_input_needed`` e a interface; o adapter so o chama quando o
    engine pausa em HITL.
    """
    estado = engine.run(thread_id=thread_id, query=query)
    return _drive(engine, thread_id, estado, on_input_needed)


def resume(
    engine: Any,
    thread_id: str,
    user_response: str,
    on_input_needed: OnInputNeeded | None = None,
) -> dict[str, Any]:
    """Retoma uma thread pausada em HITL e roda o ciclo ate um status final."""
    estado = engine.resume(thread_id=thread_id, user_response=user_response)
    return _drive(engine, thread_id, estado, on_input_needed)


# --- orquestrar o ciclo de execucao --------------------


def _drive(
    engine: Any,
    thread_id: str,
    estado: dict[str, Any],
    on_input_needed: OnInputNeeded | None,
) -> dict[str, Any]:
    """Roda o ciclo run/resume ate o engine retornar um status final.

    O adapter nao sabe nem se importa com o tempo de espera do HITL: apenas chama
    ``on_input_needed(message)`` e espera uma string. Se nenhum callback for
    fornecido (interface assincrona, ex.: web), devolve o estado de pausa
    normalizado para a interface tratar (ex.: retornar 202 e depois chamar
    ``resume()``).
    """
    while estado.get("status") == "AWAITING_USER":
        message = estado.get("message") or "Pode confirmar o prosseguimento?"
        if on_input_needed is None:
            return _normalizar(estado, thread_id)
        resposta = on_input_needed(message)
        estado = engine.resume(thread_id=thread_id, user_response=resposta)
    return _normalizar(estado, thread_id)


# --- normalizar e descrever -----------------------


def _normalizar(estado: dict[str, Any], thread_id: str) -> dict[str, Any]:
    """Transforma o estado interno do engine num dict publico estavel.

    Sempre os mesmos campos, independente do que foi gerado. Campos internos
    (tentativas_loop, historico_tentativas, tokens_*, etc.) nao aparecem.
    """
    status = _mapear_status(estado)
    linhas = estado.get("linhas_resultado_completo") or []
    return {
        "status": status,
        "answer": estado.get("resposta_natural") or None,
        "data": {
            "rows": linhas,
            "total": int(estado.get("total_linhas_resultado", len(linhas)) or 0),
        },
        "chart": {
            "generated": bool(estado.get("grafico_gerado", False)),
            "path": estado.get("caminho_grafico") or None,
        },
        "csv": {
            "path": _garantir_csv(estado),
        },
        "message": _mensagem(estado, status),
        "thread_id": thread_id,
    }


def _mapear_status(estado: dict[str, Any]) -> str:
    """Traduz o status interno do engine para o vocabulario publico."""
    status_interno = estado.get("status")
    if status_interno == "AWAITING_USER":
        return "awaiting"
    if status_interno == "bloqueado_hitl":
        return "blocked"
    if (estado.get("erro_execucao") or "").strip() or status_interno in {
        "exec_erro",
        "reprovado",
    }:
        return "error"
    return "success"


def _mensagem(estado: dict[str, Any], status: str) -> str | None:
    """Mensagem orientada ao consumidor para pausa HITL, erro ou bloqueio."""
    if status == "awaiting":
        return estado.get("message") or "Pode confirmar o prosseguimento?"
    if status == "error":
        return (estado.get("erro_execucao") or "").strip() or "Erro durante a execucao."
    if status == "blocked":
        return (
            "Fluxo pausado em HITL, mas o modo interativo esta desligado. "
            "Habilite o HITL ou refaca a pergunta com mais detalhes."
        )
    return None


def _garantir_csv(estado: dict[str, Any]) -> str | None:
    """Garante a presenca/validade do caminho do CSV, sem abrir o arquivo.

    Se o estado ja traz o caminho, usa-o; senao, reaproveita a util do runtime
    apenas para materializar um caminho valido a partir das linhas completas.
    """
    caminho = estado.get("caminho_csv_resultado")
    if caminho:
        return str(caminho)
    path = salvar_resultado_csv(estado)
    return str(path) if path is not None else None
