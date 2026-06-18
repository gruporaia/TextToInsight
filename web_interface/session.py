"""Estado da sessao do chat web: thread, memoria (transcript) e ciclo do engine.

Espelha `cli_interface/session.py`. A memoria de conversa vive AQUI (em RAM), nao
no engine: `construir_estado_inicial` zera `historico_conversa` a cada pergunta e o
campo nao tem reducer, entao o engine nao encadeia perguntas independentes. A
continuidade ("o item que voce falou...") e obtida injetando o contexto das ultimas
trocas na propria query (`build_query`) -- uma modificacao do X, robusta a rebuilds.

HITL/RAG/banco sao flags do `InsightEngine.__init__`. Trocar qualquer uma marca o
engine como "sujo": ele e reconstruido na proxima pergunta (nunca no meio de um
HITL). Como a memoria vive aqui e o checkpoint do engine e resetado por pergunta, o
rebuild nao perde nada util.

Diferenca para o CLI: o HITL na web e assincrono (sem callback). O adapter devolve
`status="awaiting"` e a sessao guarda `awaiting`/`pending_question` para que a
proxima mensagem do usuario caia em `resume()` em vez de `ask()`.
"""

from __future__ import annotations

import contextlib
import io
import os
import threading
import uuid

from text_to_insight import InsightEngine

DEFAULT_DB = "data/olist_relational.db"
DEFAULT_MODEL = "gemini-2.5-flash"
# Quantas trocas anteriores reinjetar como contexto na proxima pergunta.
MAX_CONTEXT_TURNS = 5


class ChatSession:
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
        self.thread_id = f"web-{self._thread_counter}"
        # Memoria da conversa: lista de (pergunta, resposta).
        self.transcript: list[tuple[str, str]] = []

        self._engine: InsightEngine | None = None
        self._dirty = True  # forca a construcao na primeira pergunta

        # HITL assincrono: aguardando resposta de esclarecimento do usuario?
        self.awaiting = False
        self.pending_question: str | None = None

        # Serializa ask/resume desta sessao (redirect_stdout e global ao processo).
        self.lock = threading.Lock()

    # --- ciclo de vida do engine -----------------------------------------

    def ensure_engine(self) -> InsightEngine:
        """Devolve um engine valido, reconstruindo se as flags mudaram.

        Os prints de `[CONFIG]` do `InsightEngine.__init__` sao mutados aqui para
        nao sujar os logs do servidor.
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
        """Inicia uma conversa nova: novo thread_id, memoria limpa e HITL zerado."""
        self._thread_counter += 1
        self.thread_id = f"web-{self._thread_counter}"
        self.transcript.clear()
        self.awaiting = False
        self.pending_question = None

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

    def settings(self) -> dict[str, object]:
        """Snapshot das configuracoes atuais (para o front hidratar a UI)."""
        return {
            "hitl": self.hitl,
            "rag": self.rag,
            "db_path": self.db_path,
            "db_name": self.db_name,
            "thread_id": self.thread_id,
            "awaiting": self.awaiting,
        }


class SessionStore:
    """Mapa thread-safe de session_id -> ChatSession (uma por aba/navegador)."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._sessions: dict[str, ChatSession] = {}
        self._lock = threading.Lock()

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex

    def get(self, session_id: str | None) -> ChatSession | None:
        if not session_id:
            return None
        with self._lock:
            return self._sessions.get(session_id)

    def create(self) -> tuple[str, ChatSession]:
        session_id = self.new_id()
        sessao = ChatSession(api_key=self.api_key)
        with self._lock:
            self._sessions[session_id] = sessao
        return session_id, sessao

    def get_or_create(self, session_id: str | None) -> tuple[str, ChatSession]:
        sessao = self.get(session_id)
        if sessao is not None:
            return session_id, sessao  # type: ignore[return-value]
        return self.create()

    def close_all(self) -> None:
        with self._lock:
            for sessao in self._sessions.values():
                sessao.close()
            self._sessions.clear()
