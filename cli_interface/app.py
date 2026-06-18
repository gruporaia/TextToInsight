"""Entrypoint `t2i`: loop REPL de chat sobre a biblioteca Text-to-Insight.

Consome apenas a ponte publica `text_to_insight.ask` (io_adapter). O loop:
le input -> se for slash command, despacha; senao, garante o engine, injeta a
memoria de conversa na query, chama `ask()` (com HITL sincrono) e renderiza o
dict normalizado.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

from text_to_insight import ask

from . import commands as cmds
from . import render, ui
from .session import Session

WELCOME = (
    "Chat com seu banco SQLite em linguagem natural.\n"
    "Digite uma pergunta e tecle Enter. Use /help para os comandos."
)


def main() -> int:
    load_dotenv()
    # Console ligado ao stdout real ANTES de qualquer redirect_stdout.
    console = Console(file=sys.stdout)

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        console.print(
            Panel(
                "Variavel GOOGLE_API_KEY nao encontrada. Configure no .env antes de usar o t2i.",
                title="erro",
                border_style="red",
            )
        )
        return 1

    session = Session(api_key=api_key)
    pt = ui.make_session()
    hitl_prompt = ui.make_hitl_prompt(pt, console)

    console.print(Panel(WELCOME, title="Text-to-Insight", border_style="cyan"))

    while True:
        try:
            linha = ui.read_input(pt, session)
        except KeyboardInterrupt:
            continue  # Ctrl+C cancela a linha atual
        except EOFError:
            break  # Ctrl+D sai

        linha = linha.strip()
        if not linha:
            continue

        # 1) Slash command?
        res = cmds.dispatch(linha, session)
        if res.handled:
            if res.should_quit:
                break
            if res.message:
                console.print(
                    Panel(res.message, border_style="red" if res.is_error else "cyan")
                )
            continue

        # 2) Pergunta.
        try:
            engine = session.ensure_engine()
        except Exception as exc:  # noqa: BLE001 - feedback amigavel no terminal
            console.print(
                Panel(f"Falha ao iniciar o engine: {exc}", title="erro", border_style="red")
            )
            continue

        query = session.build_query(linha)
        sink = io.StringIO()  # absorve os prints internos do engine
        try:
            with contextlib.redirect_stdout(sink):
                saida = ask(engine, session.thread_id, query, on_input_needed=hitl_prompt)
        except KeyboardInterrupt:
            console.print(Panel("Pergunta cancelada.", border_style="yellow"))
            continue
        except Exception as exc:  # noqa: BLE001
            console.print(Panel(f"Erro inesperado: {exc}", title="erro", border_style="red"))
            continue

        render.result(console, saida)
        session.record(linha, saida.get("answer"))

    session.close()
    console.print("\nAte logo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
