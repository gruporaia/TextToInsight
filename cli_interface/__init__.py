"""Aplicacao CLI de chat (REPL) do Text-to-Insight.

Esta interface consome a biblioteca apenas pela ponte publica
(`text_to_insight.ask`/`resume`), nunca falando com o grafo ou os nos diretamente.
Acionada pelo entrypoint `t2i`.
"""

from .app import main

__all__ = ["main"]
