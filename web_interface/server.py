"""Servidor FastAPI da interface web de chat do Text-to-Insight.

Consome apenas a ponte publica `text_to_insight.ask/resume` (io_adapter). Mantem
uma `ChatSession` por navegador (cookie de sessao), serve a API JSON do chat e o
frontend React buildado (`frontend/dist/`). Entrypoint: `t2i-web`.

HITL e tratado pelo modelo assincrono (sem callback): quando o engine pausa, o
adapter devolve `status="awaiting"` + `message`; o front mostra isso como uma bolha
de esclarecimento e a proxima mensagem do usuario cai em `resume()`.
"""

from __future__ import annotations

import contextlib
import io
import os
import webbrowser
from pathlib import Path

from dotenv import load_dotenv
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from text_to_insight import ask, resume

from . import files
from .session import ChatSession, SessionStore

COOKIE_NAME = "t2i_session"
_FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def create_app(api_key: str) -> FastAPI:
    app = FastAPI(title="Text-to-Insight Web", docs_url=None, redoc_url=None)
    store = SessionStore(api_key=api_key)
    app.state.store = store

    @app.on_event("shutdown")
    def _shutdown() -> None:
        store.close_all()

    def _resolve(request: Request) -> tuple[str, ChatSession]:
        """Recupera (ou cria) a sessao a partir do cookie do request."""
        sid = request.cookies.get(COOKIE_NAME)
        return store.get_or_create(sid)

    def _json(sid: str, payload: Any, status_code: int = 200) -> JSONResponse:
        """JSONResponse com o cookie de sessao setado na PROPRIA resposta retornada.

        Setar o cookie num `Response` injetado nao funciona quando devolvemos um
        objeto Response novo: o FastAPI descarta os headers do injetado. Por isso
        gravamos o cookie aqui, na resposta de fato retornada.
        """
        resp = JSONResponse(payload, status_code=status_code)
        resp.set_cookie(
            COOKIE_NAME, sid, httponly=True, samesite="lax", max_age=60 * 60 * 24
        )
        return resp

    # --- API do chat -----------------------------------------------------

    @app.post("/api/message")
    async def api_message(request: Request):
        body = await request.json()
        texto = (body.get("text") or "").strip()
        sid, sessao = _resolve(request)
        if not texto:
            return _json(sid, {"error": "Mensagem vazia."}, status_code=400)

        try:
            engine = sessao.ensure_engine()
        except Exception as exc:  # noqa: BLE001 - feedback amigavel ao front
            return _json(sid, {"error": f"Falha ao iniciar o motor: {exc}"}, 500)

        with sessao.lock:
            sink = io.StringIO()
            try:
                with contextlib.redirect_stdout(sink):
                    if sessao.awaiting:
                        # `texto` e a resposta ao HITL; nao reinjeta contexto.
                        saida = resume(engine, sessao.thread_id, texto)
                    else:
                        sessao.pending_question = texto
                        query = sessao.build_query(texto)
                        saida = ask(engine, sessao.thread_id, query)
            except Exception as exc:  # noqa: BLE001
                sessao.awaiting = False
                sessao.pending_question = None
                return _json(sid, {"error": f"Erro inesperado: {exc}"}, 500)

            if saida.get("status") == "awaiting":
                sessao.awaiting = True
            else:
                sessao.awaiting = False
                pergunta = sessao.pending_question or texto
                sessao.record(pergunta, saida.get("answer"))
                sessao.pending_question = None

        return _json(sid, saida)

    @app.post("/api/settings")
    async def api_settings(request: Request):
        body = await request.json()
        sid, sessao = _resolve(request)

        if "hitl" in body:
            sessao.set_hitl(bool(body["hitl"]))
        if "rag" in body:
            sessao.set_rag(bool(body["rag"]))
        if "db" in body and body["db"]:
            db = str(body["db"])
            if not os.path.isfile(db):
                return _json(sid, {"error": f"Arquivo nao encontrado: {db}"}, 400)
            sessao.set_db(db)

        return _json(sid, sessao.settings())

    @app.get("/api/state")
    async def api_state(request: Request):
        sid, sessao = _resolve(request)
        return _json(sid, sessao.settings())

    @app.post("/api/new")
    async def api_new(request: Request):
        sid, sessao = _resolve(request)
        sessao.new_thread()
        return _json(sid, sessao.settings())

    @app.get("/api/dbs")
    async def api_dbs():
        nomes = []
        if _DATA_DIR.is_dir():
            nomes = sorted(str(p) for p in _DATA_DIR.glob("*.db"))
        return JSONResponse({"dbs": nomes})

    @app.get("/api/csv")
    async def api_csv(name: str):
        alvo = files.resolve("csv", name)
        if alvo is None:
            return JSONResponse({"error": "Arquivo nao encontrado."}, status_code=404)
        return FileResponse(alvo, media_type="text/csv", filename=alvo.name)

    @app.get("/api/chart")
    async def api_chart(name: str):
        alvo = files.resolve("chart", name)
        if alvo is None:
            return JSONResponse({"error": "Arquivo nao encontrado."}, status_code=404)
        return FileResponse(alvo, media_type="image/png")

    # --- frontend buildado (producao) ------------------------------------
    # Em dev usa-se o Vite (npm run dev) com proxy de /api; aqui servimos o dist/
    # quando ele existir, com fallback de SPA para o index.html.
    if _FRONTEND_DIST.is_dir():
        app.mount(
            "/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend"
        )

    return app


def _load_api_key() -> str | None:
    load_dotenv()
    return os.getenv("GOOGLE_API_KEY")


def main() -> int:
    """Entrypoint `t2i-web`: sobe o servidor local."""
    import uvicorn

    api_key = _load_api_key()
    if not api_key:
        print(
            "Variavel GOOGLE_API_KEY nao encontrada. Configure no .env antes de usar."
        )
        return 1

    host = os.getenv("T2I_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("T2I_WEB_PORT", "8000"))

    app = create_app(api_key)

    if _FRONTEND_DIST.is_dir() and os.getenv("T2I_WEB_NO_BROWSER") != "1":
        with contextlib.suppress(Exception):
            webbrowser.open(f"http://{host}:{port}")
    else:
        if not _FRONTEND_DIST.is_dir():
            print(
                "[aviso] frontend ainda nao foi buildado "
                "(rode `npm install && npm run build` em web_interface/frontend). "
                "A API esta no ar; use o Vite (`npm run dev`) para o front em dev."
            )

    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
