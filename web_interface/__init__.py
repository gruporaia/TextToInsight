"""Interface web (chat) da biblioteca Text-to-Insight.

Consome apenas a ponte publica `text_to_insight.io_adapter` (`ask`/`resume`),
nunca o grafo/nos diretamente. Acionada pelo entrypoint `t2i-web`.
"""

from .server import main

__all__ = ["main"]
