"""
Testes de componentes do Text-to-Insight (sem chamadas de API).

Valida as partes determinísticas: schema, SQL validation/execution,
executor node e routers.
"""

import os
import sys

# Garante que o diretório raiz do projeto está no path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "olist_relational.db")


# ============================================================
# Schema Node
# ============================================================

def test_schema_extrai_tabelas():
    """Schema node retorna contexto com tabelas do olist DB."""
    from text_to_insight.nodes.schema import nos_nodo_esquema

    estado = {"db_path": DB_PATH, "pergunta_atual": "teste"}
    resultado = nos_nodo_esquema(estado)

    assert resultado["status"] == "schema_obtido"
    assert "Tabela:" in resultado["contexto_schema"]
    assert len(resultado["contexto_schema"]) > 100


def test_schema_erro_db_invalido():
    """Schema node retorna erro quando db_path não existe."""
    from text_to_insight.nodes.schema import nos_nodo_esquema

    estado = {"db_path": "/caminho/inexistente.db", "pergunta_atual": "teste"}
    resultado = nos_nodo_esquema(estado)

    assert resultado["status"] == "exec_erro"
    assert resultado["contexto_schema"] == ""


# ============================================================
# SQL Validation (code_sql.py)
# ============================================================

def test_sql_valida_select():
    """Aceita SELECT válido."""
    from text_to_insight.nodes.code_agent.code_sql import validar_sql_segura

    ok, msg = validar_sql_segura("SELECT COUNT(*) FROM orders")
    assert ok is True
    assert msg == ""


def test_sql_valida_cte():
    """Aceita WITH/CTE."""
    from text_to_insight.nodes.code_agent.code_sql import validar_sql_segura

    ok, msg = validar_sql_segura("WITH totals AS (SELECT id FROM orders) SELECT * FROM totals")
    assert ok is True


def test_sql_rejeita_insert():
    """Rejeita INSERT."""
    from text_to_insight.nodes.code_agent.code_sql import validar_sql_segura

    ok, msg = validar_sql_segura("INSERT INTO orders VALUES (1, 2, 3)")
    assert ok is False


def test_sql_rejeita_drop():
    """Rejeita DROP."""
    from text_to_insight.nodes.code_agent.code_sql import validar_sql_segura

    ok, msg = validar_sql_segura("DROP TABLE orders")
    assert ok is False


def test_sql_rejeita_multiplas():
    """Rejeita múltiplas statements."""
    from text_to_insight.nodes.code_agent.code_sql import validar_sql_segura

    ok, msg = validar_sql_segura("SELECT 1; SELECT 2")
    assert ok is False


def test_sql_rejeita_vazia():
    """Rejeita SQL vazia."""
    from text_to_insight.nodes.code_agent.code_sql import validar_sql_segura

    ok, msg = validar_sql_segura("")
    assert ok is False


# ============================================================
# SQL Execution (code_sql.py)
# ============================================================

def test_execucao_sql_valida():
    """Executa SELECT real no olist DB e retorna resultados."""
    from text_to_insight.nodes.code_agent.code_sql import executar_sql_sqlite

    resultado = executar_sql_sqlite(DB_PATH, "SELECT COUNT(*) as total FROM orders")
    assert resultado["ok"] is True
    assert resultado["total_linhas_resultado"] == 1
    assert resultado["linhas_resultado_preview"][0]["total"] > 0


def test_execucao_sql_com_limite():
    """Respeita limite_preview."""
    from text_to_insight.nodes.code_agent.code_sql import executar_sql_sqlite

    resultado = executar_sql_sqlite(DB_PATH, "SELECT * FROM orders", limite_preview=5)
    assert resultado["ok"] is True
    assert len(resultado["linhas_resultado_preview"]) <= 5


def test_execucao_sql_invalida():
    """Retorna erro para SQL com sintaxe inválida."""
    from text_to_insight.nodes.code_agent.code_sql import executar_sql_sqlite

    resultado = executar_sql_sqlite(DB_PATH, "SELECT FROM")
    assert resultado["ok"] is False
    assert resultado["erro_execucao"] != ""


def test_execucao_db_inexistente():
    """Retorna erro para DB inexistente."""
    from text_to_insight.nodes.code_agent.code_sql import executar_sql_sqlite

    resultado = executar_sql_sqlite("/nao/existe.db", "SELECT 1")
    assert resultado["ok"] is False


# ============================================================
# Executor Node (sandbox.py refatorado)
# ============================================================

def test_executor_sucesso():
    """Executor executa SQL do estado e retorna resultado."""
    from text_to_insight.nodes.sandbox import nos_nodo_sandbox

    estado = {
        "sql_gerada": "SELECT COUNT(*) as total FROM orders",
        "db_path": DB_PATH,
        "pergunta_atual": "teste",
    }
    resultado = nos_nodo_sandbox(estado)

    assert resultado["status"] == "exec_ok"
    assert resultado["total_linhas_resultado"] == 1
    assert len(resultado["linhas_resultado_preview"]) > 0


def test_executor_sql_vazia():
    """Executor retorna erro quando sql_gerada está vazia."""
    from text_to_insight.nodes.sandbox import nos_nodo_sandbox

    estado = {
        "sql_gerada": "",
        "db_path": DB_PATH,
        "pergunta_atual": "teste",
    }
    resultado = nos_nodo_sandbox(estado)

    assert resultado["status"] == "exec_erro"


def test_executor_sql_com_erro():
    """Executor retorna exec_erro para SQL com problema."""
    from text_to_insight.nodes.sandbox import nos_nodo_sandbox

    estado = {
        "sql_gerada": "SELECT * FROM tabela_que_nao_existe",
        "db_path": DB_PATH,
        "pergunta_atual": "teste",
    }
    resultado = nos_nodo_sandbox(estado)

    assert resultado["status"] == "exec_erro"
    assert resultado["erro_execucao"] != ""


# ============================================================
# Routers
# ============================================================

def test_roteador_sandbox_exec_ok():
    """Roteador sandbox direciona para critico quando exec_ok."""
    from text_to_insight.routers.edges import roteador_sandbox

    estado = {"status": "exec_ok", "tentativas_loop": 1}
    assert roteador_sandbox(estado) == "critico"


def test_roteador_sandbox_exec_erro():
    """Roteador sandbox direciona para planejador quando exec_erro."""
    from text_to_insight.routers.edges import roteador_sandbox

    estado = {"status": "exec_erro", "tentativas_loop": 1}
    assert roteador_sandbox(estado) == "planejador"


def test_roteador_sandbox_muitas_tentativas():
    """Roteador sandbox direciona para planejador com muitas tentativas."""
    from text_to_insight.routers.edges import roteador_sandbox

    estado = {"status": "exec_erro", "tentativas_loop": 5}
    ## alteração: com muitas tentativas, o roteador deve direcionar para "critico" para forçar o fim do loop, não para "planejador"
    #asert roteador_sandbox(estado) == "planejador"
    assert roteador_sandbox(estado) == "critico"


def test_roteador_planejador_sem_schema():
    """Roteador planejador direciona para esquema quando schema vazio."""
    from text_to_insight.routers.edges import roteador_planejador

    estado = {"contexto_schema": "", "status": "iniciado"}
    assert roteador_planejador(estado) == "esquema"


def test_roteador_planejador_pronto():
    """Roteador planejador direciona para agente_codigo quando pronto."""
    from text_to_insight.routers.edges import roteador_planejador

    estado = {"contexto_schema": "tabelas...", "status": "pronto_codificacao"}
    assert roteador_planejador(estado) == "agente_codigo"


def test_roteador_planejador_aprovado():
    """Roteador planejador direciona para fim quando aprovado."""
    from text_to_insight.routers.edges import roteador_planejador

    estado = {"contexto_schema": "tabelas...", "status": "aprovado"}
    assert roteador_planejador(estado) == "fim"

def test_roteador_planejador_espera_humana():
    """Roteador planejador direciona para espera_humana se a flag esperar_usuario for True."""
    from text_to_insight.routers.edges import roteador_planejador

    estado = {
        "espera_humana": True,
        "contexto_schema": "tabelas...",
        "status": "aguardando_input"
    }
    assert roteador_planejador(estado) == "espera_humana"


# ============================================================
# Retriever (GraphRAG) — componentes determinísticos
# ============================================================

def _schema_olist_real():
    from text_to_insight.nodes.schema import nos_nodo_esquema
    return nos_nodo_esquema({"db_path": DB_PATH, "pergunta_usuario": "x"})["contexto_schema"]


def test_schema_graph_constroi_nos_e_arestas_do_olist():
    """SchemaGraph extrai tabelas como nós e FKs como arestas."""
    from text_to_insight.retriever.graph_logic import SchemaGraph

    g = SchemaGraph(schema=_schema_olist_real()).graph
    nos = set(g.nodes)
    assert {"orders", "customers", "order_items", "products", "sellers"}.issubset(nos)
    # FK conhecida do Olist: orders.customer_id -> customers.customer_id
    assert ("orders", "customers") in g.edges


def test_rag_retriever_indexa_e_recupera_tabela_relevante(tmp_path):
    """RAGRetriever indexa chunks-por-tabela e recupera a tabela óbvia."""
    import chromadb
    from text_to_insight.retriever.rag_logic import RAGRetriever

    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    r = RAGRetriever(
        chroma_client=client,
        document_schema={"contexto_schema": _schema_olist_real()},
        collection_name="t_componentes",
    )
    out = r._query("Which columns do we have on the table orders?", top_k=3)
    assert "orders" in out["ids"][0]


def test_schema_graph_rag_retrieve_reduz_e_liga(tmp_path, monkeypatch):
    """SchemaGraphRAG.retrieve devolve tabelas relevantes + caminhos do grafo."""
    import chromadb
    from text_to_insight.retriever import engine as engine_mod

    # Isola o índice persistente em tmp_path para não poluir o disco do projeto.
    monkeypatch.setattr(engine_mod, "BASE_DIR", tmp_path)
    rag = engine_mod.SchemaGraphRAG(schema={"contexto_schema": _schema_olist_real()})
    # Query em inglês: o embedder default do Chroma (all-MiniLM-L6-v2) é treinado em inglês.
    # PT-BR fica out-of-scope para o MVP (ver Fora de escopo no plano).
    tabs, rels = rag.retrieve("How many orders does each customer have?")
    nomes = set(tabs["ids"][0])
    assert "orders" in nomes
    assert "customers" in nomes
    # Deve existir pelo menos um caminho conectando orders e customers.
    assert any({"orders", "customers"}.issubset(set(p)) for p in rels)


def test_no_retriever_reduz_contexto_schema():
    """Nó retriever produz contexto reduzido contendo tabelas relevantes."""
    from text_to_insight.nodes.retriever import nos_nodo_retriever
    from text_to_insight.nodes.schema import nos_nodo_esquema

    estado = {"db_path": DB_PATH, "pergunta_usuario": "How many orders does each customer have?"}
    estado["contexto_schema"] = nos_nodo_esquema(estado)["contexto_schema"]
    tam_original = len(estado["contexto_schema"])

    out = nos_nodo_retriever(estado)
    assert "contexto_schema" in out
    assert len(out["contexto_schema"]) < tam_original
    assert "orders" in out["contexto_schema"].lower()