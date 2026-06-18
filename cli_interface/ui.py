"""Camada de input (prompt_toolkit): PromptSession, atalhos, toolbar e HITL.

O `output` do prompt_toolkit e ligado explicitamente ao stdout real no startup.
Isso garante que, mesmo quando o app envolve as chamadas do engine em
`redirect_stdout` (para mutar o ruido de `print()` do engine), o prompt do HITL
continua aparecendo no terminal de verdade -- e nao no buffer descartado.
"""

from __future__ import annotations

import os
import sys
from typing import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.output.defaults import create_output
from rich.console import Console
from rich.panel import Panel

from .session import Session


def _make_keybindings() -> KeyBindings:
    kb = KeyBindings()

    @kb.add(Keys.Enter)
    def _(event) -> None:  # Enter envia
        event.current_buffer.validate_and_handle()

    @kb.add(Keys.ControlJ)
    def _(event) -> None:  # Ctrl+J insere nova linha
        event.current_buffer.insert_text("\n")

    @kb.add(Keys.ControlL)
    def _(event) -> None:  # Ctrl+L limpa a tela
        event.app.renderer.clear()

    return kb


def make_session() -> PromptSession:
    """Cria o PromptSession ligado ao stdout/stdin reais (chamado no startup)."""
    return PromptSession(
        history=InMemoryHistory(),
        key_bindings=_make_keybindings(),
        multiline=True,
        output=create_output(stdout=sys.stdout),
    )


def _bottom_toolbar(session: Session) -> Callable[[], HTML]:
    def render() -> HTML:
        return HTML(
            f" db: <b>{session.db_name}</b>  |  "
            f"HITL: <b>{'on' if session.hitl else 'off'}</b>  |  "
            f"RAG: <b>{'on' if session.rag else 'off'}</b>  |  "
            f"thread: <b>{session.thread_id}</b> "
        )

    return render


def read_input(pt: PromptSession, session: Session) -> str:
    return pt.prompt(
        HTML("<ansicyan><b>voce ›</b></ansicyan> "),
        bottom_toolbar=_bottom_toolbar(session),
    )


def make_hitl_prompt(pt: PromptSession, console: Console) -> Callable[[str], str]:
    """Cria o callback `on_input_needed` para o io_adapter (HITL sincrono)."""

    def hitl_prompt(message: str) -> str:
        console.print(
            Panel(message, title="HITL - preciso de um esclarecimento", border_style="magenta")
        )
        return pt.prompt(HTML("<ansimagenta><b>resposta ›</b></ansimagenta> "))

    return hitl_prompt
