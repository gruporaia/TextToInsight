"""Teste offline (sem API) do backend web. Rodar: python -m web_interface._offline_test"""
from fastapi.testclient import TestClient
import web_interface.session as sess
import web_interface.server as server
import web_interface.files as files


def main():
    # 1) build_query injeta contexto das trocas anteriores
    s = sess.ChatSession(api_key="x")
    assert s.build_query("primeira") == "primeira"
    s.record("quantos pedidos?", "99441")
    q = s.build_query("e quantos cancelados?")
    assert "Contexto da conversa" in q and "99441" in q and "Pergunta atual" in q
    print("[ok] build_query injeta memoria")

    # 2) files.resolve rejeita traversal e aceita basename valido
    files.RESULTS_DIR.mkdir(exist_ok=True)
    p = files.RESULTS_DIR / "teste_offline.csv"
    p.write_text("a,b\n1,2\n")
    assert files.resolve("csv", "teste_offline.csv") is not None
    assert files.resolve("csv", "../../etc/passwd") is None
    assert files.resolve("csv", "nao_existe.csv") is None
    p.unlink()
    print("[ok] files.resolve seguro")

    # 3) fluxo /api/message: ask -> awaiting -> proxima msg cai em resume
    class FakeEngine:
        def __init__(self, **kw):
            pass

        def run(self, thread_id, query):
            self.last_query = query
            return {"status": "AWAITING_USER", "message": "Qual periodo voce quer?"}

        def resume(self, thread_id, user_response):
            self.last_response = user_response
            return {
                "status": "ok",
                "resposta_natural": "Foram 100 no periodo.",
                "linhas_resultado_completo": [{"n": 100}],
                "total_linhas_resultado": 1,
                "caminho_csv_resultado": "/tmp/x.csv",
            }

        def close(self):
            pass

    sess.InsightEngine = FakeEngine  # stub usado por ensure_engine
    app = server.create_app("fake-key")
    client = TestClient(app)

    r1 = client.post("/api/message", json={"text": "quantos pedidos?"})
    # Carrega o cookie de sessao explicitamente (independe da jar do test client).
    cookie = r1.cookies.get(server.COOKIE_NAME)
    assert cookie, "backend deve setar o cookie de sessao"
    hdr = {"Cookie": f"{server.COOKIE_NAME}={cookie}"}
    d1 = r1.json()
    assert d1["status"] == "awaiting", d1
    assert "periodo" in (d1.get("message") or "")
    print("[ok] 1a msg -> awaiting:", d1["message"])

    d2 = client.post(
        "/api/message", json={"text": "mes passado"}, headers=hdr
    ).json()
    assert d2["status"] == "success", d2
    assert d2["answer"] == "Foram 100 no periodo."
    assert d2["data"]["rows"] == [{"n": 100}]
    print("[ok] 2a msg -> resume/success:", d2["answer"])

    rs = client.post("/api/settings", json={"rag": True}, headers=hdr).json()
    assert rs["rag"] is True
    print("[ok] settings toggle rag")

    rd = client.get("/api/dbs").json()
    assert any("olist_relational.db" in x for x in rd["dbs"])
    print("[ok] /api/dbs:", rd["dbs"])

    print("\nALL OK")


if __name__ == "__main__":
    main()
