"""Estado da sessao do chat CLI: thread, memoria (transcript) e ciclo do engine.

A memoria de conversa vive AQUI (em RAM), nao no engine. Motivo (verificado no
codigo): `construir_estado_inicial` zera `historico_conversa` a cada pergunta e o
campo nao tem reducer, entao o engine nao encadeia perguntas independentes. Logo,
a continuidade ("do item que voce falou...") e obtida injetando o contexto das
ultimas trocas na propria query -- uma modificacao do X, robusta a rebuilds.

HITL/RAG/banco sao flags do `InsightEngine.__init__`. Trocar qualquer uma marca o
engine como "sujo": ele e reconstruido na proxima pergunta (nunca no meio de um
HITL). Como a memoria vive aqui e o checkpoint do engine e resetado por pergunta,
o rebuild nao perde nada util.
"""

from __future__ import annotations

import contextlib
import io
import os

from text_to_insight import InsightEngine

DEFAULT_DB = "data/olist_relational.db"
DEFAULT_MODEL = "gemini-2.5-flash"
# Quantas trocas anteriores reinjetar como contexto na proxima pergunta.
MAX_CONTEXT_TURNS = 5


class Session:
    """Mantem flags, thread e transcript; constroi/reconstroi o engine sob demanda."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        db_path: str = DEFAULT_DB,
        hitl: bool = True,
        rag: bool = False,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.db_path = db_path
        self.hitl = hitl
        self.rag = rag

        self._thread_counter = 1
        self.thread_id = f"sessao-{self._thread_counter}"
        # Memoria da conversa: lista de (pergunta, resposta).
        self.transcript: list[tuple[str, str]] = []

        self._engine: InsightEngine | None = None
        self._dirty = True  # forca a construcao na primeira pergunta

    # --- ciclo de vida do engine -----------------------------------------

    def ensure_engine(self) -> InsightEngine:
        """Devolve um engine valido, reconstruindo se as flags mudaram.

        Os prints de `[CONFIG]` do `InsightEngine.__init__` sao mutados aqui para
        nao sujar o transcript.
        """
        if self._engine is not None and not self._dirty:
            return self._engine

        self.close()
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            self._engine = InsightEngine(
                api_key=self.api_key,
                model=self.model,
                db_path=self.db_path,
                hitl=self.hitl,
                enrich_rag=self.rag,
                show_output=False,
            )
        self._dirty = False
        return self._engine

    def close(self) -> None:
        if self._engine is not None:
            with contextlib.suppress(Exception):
                self._engine.close()
            self._engine = None

    # --- toggles (marcam o engine como sujo) -----------------------------

    def set_hitl(self, value: bool) -> None:
        if value != self.hitl:
            self.hitl = value
            self._dirty = True

    def set_rag(self, value: bool) -> None:
        if value != self.rag:
            self.rag = value
            self._dirty = True

    def set_db(self, path: str) -> None:
        if path != self.db_path:
            self.db_path = path
            self._dirty = True

    def new_thread(self) -> None:
        """Inicia uma conversa nova: novo thread_id e memoria limpa."""
        self._thread_counter += 1
        self.thread_id = f"sessao-{self._thread_counter}"
        self.transcript.clear()

    # --- memoria de conversa ---------------------------------------------

    def build_query(self, pergunta: str) -> str:
        """Monta a query reinjetando as ultimas trocas como contexto (X)."""
        if not self.transcript:
            return pergunta
        recentes = self.transcript[-MAX_CONTEXT_TURNS:]
        linhas = [
            f'{i}. Pergunta: "{q}" | Resposta: "{a}"'
            for i, (q, a) in enumerate(recentes, 1)
        ]
        contexto = "\n".join(linhas)
        return (
            "Contexto da conversa ate agora (turnos anteriores):\n"
            f"{contexto}\n\n"
            f'Pergunta atual: "{pergunta}"'
        )

    def record(self, pergunta: str, resposta: str | None) -> None:
        self.transcript.append((pergunta, resposta or ""))

    # --- util ------------------------------------------------------------

    @property
    def db_name(self) -> str:
        return os.path.basename(self.db_path) or self.db_path
