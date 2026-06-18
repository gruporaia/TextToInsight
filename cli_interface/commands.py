"""Parser/dispatch dos slash commands do chat CLI.

Comandos suportados:
  /hitl on|off   liga/desliga o HITL (aplica na proxima pergunta)
  /rag  on|off   liga/desliga o enriquecimento RAG (aplica na proxima pergunta)
  /db <caminho>  troca o banco SQLite local (aplica na proxima pergunta)
  /new           inicia uma conversa nova (novo thread + memoria limpa)
  /help          mostra a ajuda
  /quit          sai
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .session import Session

HELP_TEXT = (
    "Comandos:\n"
    "  /hitl on|off   liga/desliga o HITL (aplica na proxima pergunta)\n"
    "  /rag  on|off   liga/desliga o RAG  (aplica na proxima pergunta)\n"
    "  /db <caminho>  troca o banco SQLite local\n"
    "  /new           nova conversa (limpa a memoria)\n"
    "  /help          esta ajuda\n"
    "  /quit          sair\n"
    "\n"
    "Teclas: Enter envia - Ctrl+J nova linha - up/down historico - "
    "Ctrl+L limpa - Ctrl+C cancela - Ctrl+D sai"
)


@dataclass
class CommandResult:
    """Resultado do dispatch. `handled=False` significa 'isto e uma pergunta'."""

    handled: bool
    should_quit: bool = False
    message: str | None = None
    is_error: bool = False


def dispatch(linha: str, session: Session) -> CommandResult:
    if not linha.startswith("/"):
        return CommandResult(handled=False)

    partes = linha.strip().split()
    cmd = partes[0].lower()
    args = partes[1:]

    if cmd in ("/quit", "/exit", "/q"):
        return CommandResult(handled=True, should_quit=True)

    if cmd == "/help":
        return CommandResult(handled=True, message=HELP_TEXT)

    if cmd == "/new":
        session.new_thread()
        return CommandResult(
            handled=True,
            message=f"Nova conversa: thread '{session.thread_id}'. Memoria limpa.",
        )

    if cmd == "/hitl":
        val = _parse_on_off(args)
        if val is None:
            return CommandResult(handled=True, is_error=True, message="Uso: /hitl on|off")
        session.set_hitl(val)
        return CommandResult(
            handled=True,
            message=f"HITL {'ligado' if val else 'desligado'} (aplica na proxima pergunta).",
        )

    if cmd == "/rag":
        val = _parse_on_off(args)
        if val is None:
            return CommandResult(handled=True, is_error=True, message="Uso: /rag on|off")
        session.set_rag(val)
        return CommandResult(
            handled=True,
            message=f"RAG {'ligado' if val else 'desligado'} (aplica na proxima pergunta).",
        )

    if cmd == "/db":
        if not args:
            return CommandResult(handled=True, is_error=True, message="Uso: /db <caminho>")
        path = " ".join(args)
        if not os.path.isfile(path):
            return CommandResult(
                handled=True, is_error=True, message=f"Arquivo nao encontrado: {path}"
            )
        session.set_db(path)
        return CommandResult(
            handled=True,
            message=f"Banco trocado para '{path}' (aplica na proxima pergunta).",
        )

    return CommandResult(
        handled=True, is_error=True, message=f"Comando desconhecido: {cmd}. Use /help."
    )


def _parse_on_off(args: list[str]) -> bool | None:
    if not args:
        return None
    v = args[0].lower()
    if v in ("on", "ligar", "1", "true", "sim"):
        return True
    if v in ("off", "desligar", "0", "false", "nao"):
        return False
    return None
