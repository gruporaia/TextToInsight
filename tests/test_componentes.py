"""
Testes de componentes do Text-to-Insight (sem chamadas de API).

Valida as partes determinísticas: schema, SQL validation/execution,
executor node e routers.
"""

import os
import sys
import pytest
import sqlite3
from dotenv import load_dotenv
load_dotenv()

# Garante que o diretório raiz do projeto está no path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "olist_relational.db")


# ============================================================
# Schema Node
# ============================================================

def test_schema_extrai_tabelas(olist_engine):
    """Schema node retorna contexto com tabelas do olist DB."""
    from text_to_insight.nodes.schema import nos_nodo_esquema

    resultado = nos_nodo_esquema({"pergunta_atual": "teste"}, engine=olist_engine)

    assert resultado["status"] == "schema_obtido"
    assert resultado["tem_descricao"] is False
    assert "Tabela:" in resultado["contexto_schema"]
    assert len(resultado["contexto_schema"]) > 100


def test_schema_erro_engine_sem_banco(tmp_path):
    """Schema node retorna exec_erro quando a engine não consegue abrir o banco."""
    from tests.conftest import criar_engine_sqlite
    from text_to_insight.nodes.schema import nos_nodo_esquema

    engine = criar_engine_sqlite(str(tmp_path / "inexistente.db"))
    try:
        resultado = nos_nodo_esquema({"pergunta_atual": "teste"}, engine=engine)
    finally:
        engine.dispose()

    assert resultado["status"] == "exec_erro"
    assert resultado["contexto_schema"] == ""


def test_schema_usa_cache_enriquecido_so_com_enrich_rag(olist_engine, tmp_path):
    """O schema enriquecido em cache só é usado quando enrich_rag está ligado."""
    from text_to_insight.cache import salvar_schema_enriquecido
    from text_to_insight.nodes.schema import nos_nodo_esquema

    bruto = nos_nodo_esquema({}, engine=olist_engine, cache_dir=tmp_path)["contexto_schema"]
    salvar_schema_enriquecido(tmp_path, bruto, "SCHEMA ENRIQUECIDO")

    sem_enrich = nos_nodo_esquema({}, engine=olist_engine, enrich_rag=False, cache_dir=tmp_path)
    com_enrich = nos_nodo_esquema({}, engine=olist_engine, enrich_rag=True, cache_dir=tmp_path)

    assert sem_enrich["contexto_schema"] == bruto
    assert sem_enrich["tem_descricao"] is False
    assert com_enrich["contexto_schema"] == "SCHEMA ENRIQUECIDO"
    assert com_enrich["tem_descricao"] is True


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


# Variantes de "SELECT ... INTO OUTFILE/DUMPFILE" (escrita de arquivo no servidor
# MySQL). Todas começam com SELECT, então só o blacklist as barra. O MySQL aceita
# comentários entre INTO e OUTFILE, então espaço não pode ser o único separador.
VARIANTES_ESCRITA_DE_ARQUIVO = [
    "SELECT * FROM t INTO OUTFILE '/tmp/x'",
    "select * from t into  outfile '/tmp/x'",
    "SELECT * FROM t INTO DUMPFILE '/tmp/x'",
    "SELECT * FROM t INTO/**/OUTFILE '/tmp/x'",
    "SELECT * FROM t INTO/*qualquer coisa*/DUMPFILE '/tmp/x'",
    "SELECT * FROM t INTO /*!50000 OUTFILE*/ '/tmp/x'",
    "SELECT * FROM t INTO -- x\nOUTFILE '/tmp/x'",
    "SELECT * FROM t INTO #x\nOUTFILE '/tmp/x'",
    "SELECT * FROM t\nINTO\nOUTFILE '/tmp/x'",
]


@pytest.mark.parametrize("sql", VARIANTES_ESCRITA_DE_ARQUIVO)
def test_sql_rejeita_escrita_de_arquivo_mysql(sql):
    """INTO OUTFILE/DUMPFILE é bloqueado qualquer que seja o separador entre as palavras."""
    from text_to_insight.nodes.code_agent.code_sql import validar_sql_segura

    ok, msg = validar_sql_segura(sql)
    assert ok is False, f"deveria bloquear: {sql!r}"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT LOAD_FILE('/etc/passwd')",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT load_extension('/tmp/x.so')",
    ],
)
def test_sql_rejeita_leitura_de_arquivo_por_funcao(sql):
    """Funções que leem arquivo do servidor também são bloqueadas."""
    from text_to_insight.nodes.code_agent.code_sql import validar_sql_segura

    ok, msg = validar_sql_segura(sql)
    assert ok is False, f"deveria bloquear: {sql!r}"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM orders",
        "SELECT nome INTO @variavel FROM clientes LIMIT 1",  # INTO @var não escreve arquivo
        "SELECT COUNT(*) FROM orders /* comentario inocente */",
        "WITH t AS (SELECT 1) SELECT * FROM t",
    ],
)
def test_sql_legitima_continua_permitida(sql):
    """O blacklist não pode gerar falso positivo em consultas normais."""
    from text_to_insight.nodes.code_agent.code_sql import validar_sql_segura

    ok, msg = validar_sql_segura(sql)
    assert ok is True, f"deveria permitir: {sql!r} ({msg})"


# ============================================================
# SQL Execution (code_sql.py)
# ============================================================

def test_execucao_sql_valida(olist_engine):
    """Executa SELECT real no olist DB e retorna resultados."""
    from text_to_insight.nodes.code_agent.code_sql import executar_sql_via_engine

    resultado = executar_sql_via_engine(olist_engine, "SELECT COUNT(*) as total FROM orders")
    assert resultado["ok"] is True
    assert resultado["total_linhas_resultado"] == 1
    assert resultado["linhas_resultado_preview"][0]["total"] > 0


def test_execucao_sql_com_limite(olist_engine):
    """Respeita limite_preview."""
    from text_to_insight.nodes.code_agent.code_sql import executar_sql_via_engine

    resultado = executar_sql_via_engine(olist_engine, "SELECT * FROM orders", limite_preview=5)
    assert resultado["ok"] is True
    assert len(resultado["linhas_resultado_preview"]) <= 5


def test_execucao_sql_invalida(olist_engine):
    """Retorna erro para SQL com sintaxe inválida."""
    from text_to_insight.nodes.code_agent.code_sql import executar_sql_via_engine

    resultado = executar_sql_via_engine(olist_engine, "SELECT FROM")
    assert resultado["ok"] is False
    assert resultado["erro_execucao"] != ""


def test_execucao_sqlite_somente_leitura(olist_engine):
    """A engine dos testes abre o SQLite em mode=ro: mesmo sem a validação, nada é escrito."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    with pytest.raises(OperationalError):
        with olist_engine.connect() as conn:
            conn.execute(text("CREATE TABLE x (a INT)"))


# ============================================================
# Executor Node (sandbox.py)
# ============================================================

def test_executor_sucesso(olist_engine):
    """Executor executa SQL do estado e retorna resultado."""
    from text_to_insight.nodes.sandbox import nos_nodo_sandbox

    estado = {"sql_gerada": "SELECT COUNT(*) as total FROM orders", "pergunta_atual": "teste"}
    resultado = nos_nodo_sandbox(estado, engine=olist_engine)

    assert resultado["status"] == "exec_ok"
    assert resultado["total_linhas_resultado"] == 1
    assert len(resultado["linhas_resultado_preview"]) > 0


def test_executor_sql_vazia(olist_engine):
    """Executor retorna erro quando sql_gerada está vazia."""
    from text_to_insight.nodes.sandbox import nos_nodo_sandbox

    resultado = nos_nodo_sandbox({"sql_gerada": "", "pergunta_atual": "teste"}, engine=olist_engine)

    assert resultado["status"] == "exec_erro"


def test_executor_sql_com_erro(olist_engine):
    """Executor retorna exec_erro para SQL com problema."""
    from text_to_insight.nodes.sandbox import nos_nodo_sandbox

    estado = {"sql_gerada": "SELECT * FROM tabela_que_nao_existe", "pergunta_atual": "teste"}
    resultado = nos_nodo_sandbox(estado, engine=olist_engine)

    assert resultado["status"] == "exec_erro"
    assert resultado["erro_execucao"] != ""


# ============================================================
# Execução via Engine SQLAlchemy
#
# Usamos uma Engine SQLite apontando pro banco de teste só para não
# depender de um Postgres/MySQL real — o código exercitado
# (executar_sql_via_engine, _formatar_schema_sqlalchemy,
# _resolver_info_dialeto) é o mesmo independente do dialeto.
# ============================================================

def _engine_sqlite_fixture():
    """Engine própria (não a da sessão) para testes que mexem no pool."""
    from sqlalchemy import create_engine

    return create_engine(f"sqlite:///{DB_PATH}")


@pytest.mark.parametrize("sql, esperado", [
    ("SELECT 'Status :ativo' AS v", "Status :ativo"),
    ("SELECT '12:30' AS v", "12:30"),
    ("SELECT 'a % b' AS v", "a % b"),
])
def test_executor_via_engine_preserva_literais_com_dois_pontos(sql, esperado):
    """A SQL do agente vai crua ao driver: ':palavra' em literal não vira parâmetro de bind."""
    from text_to_insight.nodes.code_agent.code_sql import executar_sql_via_engine

    engine = _engine_sqlite_fixture()
    try:
        resultado = executar_sql_via_engine(engine, sql)
    finally:
        engine.dispose()

    assert resultado["ok"], resultado["erro_execucao"]
    assert resultado["linhas_resultado_preview"][0]["v"] == esperado


def test_executor_via_engine_nao_deixa_timeout_na_conexao_do_pool():
    """
    A conexão volta para o pool do chamador: o progress handler de timeout
    não pode continuar instalado e abortar queries posteriores do chamador.
    """
    import time
    from sqlalchemy import text
    from text_to_insight.nodes.code_agent.code_sql import executar_sql_via_engine

    engine = _engine_sqlite_fixture()
    try:
        assert executar_sql_via_engine(engine, "SELECT 1 AS x", timeout_segundos=0.1)["ok"]
        time.sleep(0.3)
        with engine.connect() as conn:
            total = conn.execute(text(
                "SELECT COUNT(*) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id"
            )).scalar()
    finally:
        engine.dispose()

    assert total > 0


def test_data_exploration_via_engine(olist_engine):
    """A exploração lê as estatísticas do banco da engine injetada."""
    from text_to_insight.nodes.data_exploration import nos_nodo_data_exploration

    estado = {
        "contexto_rag_schema": "Tabela: orders\n- order_id: TEXT",
        "colunas_para_explorar": {"orders": ["order_status"]},
    }
    resultado = nos_nodo_data_exploration(estado, engine=olist_engine)

    assert "order_status" in resultado["contexto_data_exploration"]


def test_data_exploration_engine_nao_sqlite_pula():
    """Exploração é SQLite-only: com engine de outro dialeto, pula sem conectar."""
    from sqlalchemy import create_engine
    from text_to_insight.nodes.data_exploration import nos_nodo_data_exploration

    engine = create_engine("postgresql+psycopg2://u:p@localhost:1/nao_conecta")
    estado = {"contexto_rag_schema": "Tabela: orders"}
    resultado = nos_nodo_data_exploration(estado, engine=engine)

    assert resultado == {"contexto_data_exploration": ""}


def test_enrich_grava_cache_pelo_hash_do_schema(tmp_path):
    """O enriquecimento grava o cache na pasta de cache, indexado pelo schema recebido."""
    from types import SimpleNamespace
    from text_to_insight.cache import ler_schema_enriquecido
    from text_to_insight.nodes.enrich_schema import nos_nodo_enrich

    class _FakeLLM:
        def bind(self, **_):
            return self

        def batch(self, prompts, config=None):
            return [SimpleNamespace(content='{"t": {"a": "coluna a"}}') for _ in prompts]

    schema = "Tabela: t\n- a: INT\n"
    resultado = nos_nodo_enrich({"contexto_schema": schema}, _FakeLLM(), cache_dir=tmp_path)

    assert resultado["tem_descricao"] is True
    assert "coluna a" in resultado["contexto_schema"]
    assert ler_schema_enriquecido(tmp_path, schema) == resultado["contexto_schema"]
    assert ler_schema_enriquecido(tmp_path, "Tabela: outra\n") is None


def test_resolver_dialeto_via_engine():
    """Dialeto do prompt é lido de engine.dialect.name."""
    from text_to_insight.nodes.code_agent.code_agent import _resolver_info_dialeto

    engine = _engine_sqlite_fixture()
    try:
        nome, nota = _resolver_info_dialeto(engine)
    finally:
        engine.dispose()

    assert nome == "SQLite"
    assert "SQLite" in nota


# ============================================================
# Routers
# ============================================================

def test_roteador_sandbox_exec_ok():
    """Roteador sandbox direciona para critico quando exec_ok."""
    from text_to_insight.routers.edges import roteador_sandbox

    estado = {"status": "exec_ok", "tentativas_loop": 1}
    assert roteador_sandbox(estado) == "salvar_csv"


def test_roteador_sandbox_exec_erro():
    """Roteador sandbox direciona para planejador quando exec_erro."""
    from text_to_insight.routers.edges import roteador_sandbox

    estado = {"status": "exec_erro", "tentativas_loop": 1}
    assert roteador_sandbox(estado) == "planejador"


def test_roteador_sandbox_muitas_tentativas():
    """Roteador sandbox direciona para planejador com muitas tentativas."""
    from text_to_insight.routers.edges import roteador_sandbox

    estado = {"status": "exec_erro", "tentativas_loop": 5}
    ## alteração: com muitas tentativas, o roteador deve direcionar para "salvar_csv" para forçar o fim do loop, não para "planejador"
    #asert roteador_sandbox(estado) == "planejador"
    assert roteador_sandbox(estado) == "salvar_csv"


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
    from tests.conftest import criar_engine_sqlite
    from text_to_insight.nodes.schema import nos_nodo_esquema

    engine = criar_engine_sqlite(DB_PATH)
    try:
        return nos_nodo_esquema({"pergunta_atual": "x"}, engine=engine)["contexto_schema"]
    finally:
        engine.dispose()


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

    # Isola o índice persistente em tmp_path para não poluir o cache do usuário.
    rag = engine_mod.SchemaGraphRAG(schema={"contexto_schema": _schema_olist_real()}, cache_dir=tmp_path)
    # Query em inglês: o embedder default do Chroma (all-MiniLM-L6-v2) é treinado em inglês.
    # PT-BR fica out-of-scope para o MVP (ver Fora de escopo no plano).
    tabs, rels = rag.retrieve("How many orders does each customer have?")
    nomes = set(tabs["ids"][0])
    assert "orders" in nomes
    assert "customers" in nomes
    # Deve existir pelo menos um caminho conectando orders e customers.
    assert any({"orders", "customers"}.issubset(set(p)) for p in rels)


def test_no_retriever_reduz_contexto_schema():
    """Nó retriever produz contexto reduzido contendo tabelas relevantes quando schema >= 5000 chars."""
    from text_to_insight.nodes.retriever import nos_nodo_retriever
    from text_to_insight.nodes.schema import nos_nodo_esquema

    estado = {"pergunta_atual": "How many orders does each customer have?"}
    schema_base = _schema_olist_real()
    # Adiciona linhas de comentário para ultrapassar o limiar de 5000 chars e acionar o RAG
    estado["contexto_schema"] = schema_base + "\n" + ("-- padding de teste para atingir threshold de RAG\n" * 100)
    tam_original = len(estado["contexto_schema"])

    out = nos_nodo_retriever(estado)
    assert "contexto_rag_schema" in out
    assert len(out["contexto_rag_schema"]) <= tam_original
    assert "orders" in out["contexto_rag_schema"].lower()


def test_no_retriever_schema_pequeno_mantem_original():
    """Para schemas pequenos (< 5000 chars), o retriever mantém contexto_rag_schema vazio."""
    from text_to_insight.nodes.retriever import nos_nodo_retriever
    from text_to_insight.nodes.schema import nos_nodo_esquema

    estado = {"pergunta_atual": "How many orders does each customer have?"}
    estado["contexto_schema"] = _schema_olist_real()
    assert len(estado["contexto_schema"]) < 5000

    out = nos_nodo_retriever(estado)
    assert out.get("contexto_rag_schema") == ""
    assert out.get("status") == "schema_obtido"

# ============================================================
# TESTES NOVOS (FKs Virtuais, Roteador e Injeção Matemática)
# ============================================================

def test_inferir_fks_virtuais_sufixos():
    from text_to_insight.nodes.schema import _inferir_fks_virtuais
    schema = (
        "Tabela: orders\n"
        "- order_id: INTEGER (PK)\n"
        "- customer_id: INTEGER\n"
        "\n"
        "Tabela: customers\n"
        "- id: INTEGER (PK)\n"
        "- name: TEXT\n"
        "\n"
    )
    novo_schema = _inferir_fks_virtuais(schema)
    assert "customer_id -> customers.id (virtual)" in novo_schema

def test_inferir_fks_virtuais_prefixo_tabela():
    from text_to_insight.nodes.schema import _inferir_fks_virtuais
    schema = (
        "Tabela: olist_orders\n"
        "- order_id: INTEGER (PK)\n"
        "- status: TEXT\n"
        "\n"
        "Tabela: order_items\n"
        "- item_id: INTEGER\n"
        "- order_id: INTEGER\n"
        "\n"
    )
    novo_schema = _inferir_fks_virtuais(schema)
    assert "order_id -> olist_orders.order_id (virtual)" in novo_schema

def test_inferir_fks_virtuais_colunas_homonimas():
    from text_to_insight.nodes.schema import _inferir_fks_virtuais
    schema = (
        "Tabela: olist_products\n"
        "- product_id: INTEGER (PK)\n"
        "- product_category_name: TEXT\n"
        "\n"
        "Tabela: product_category_name_translation\n"
        "- product_category_name: TEXT (PK)\n"
        "- product_category_name_english: TEXT\n"
        "\n"
    )
    novo_schema = _inferir_fks_virtuais(schema)
    assert "product_category_name -> product_category_name_translation.product_category_name (virtual)" in novo_schema

def test_roteador_planejador_quebra_loop_schema_pequeno():
    from text_to_insight.routers.edges import roteador_planejador
    estado = {
        "contexto_schema": "Tabela: a\n- id: INT\n", # < 1500 chars
        "status": "revisando_estrategia",
        "tentativas_revisao_retriever": 0
    }
    assert roteador_planejador(estado) == "agente_codigo"

def test_roteador_planejador_quebra_loop_max_tentativas():
    from text_to_insight.routers.edges import roteador_planejador
    schema_grande = "A" * 2000
    estado = {
        "contexto_schema": schema_grande,
        "status": "revisando_estrategia",
        "tentativas_revisao_retriever": 2 # MAX_TENTATIVAS_REVISAO = 2
    }
    assert roteador_planejador(estado) == "agente_codigo"

def test_roteador_planejador_fallback_agente_codigo():
    from text_to_insight.routers.edges import roteador_planejador
    estado = {
        "contexto_schema": "Tabela: a\n- id: INT\n",
        "status": "status_inexistente"
    }
    assert roteador_planejador(estado) == "agente_codigo"

def test_roteador_planejador_revisao_com_rag_desativado():
    from text_to_insight.routers.edges import roteador_planejador
    schema_grande = "A" * 2000
    estado = {
        "contexto_schema": schema_grande,
        "contexto_rag_schema": "",
        "status": "revisando_estrategia",
        "tentativas_revisao_retriever": 0
    }
    assert roteador_planejador(estado) == "agente_codigo"

def test_sqlite_math_functions(tmp_path):
    from src.spider.query_executor import SpiderQueryExecutor
    db_id = "test_math_db"
    db_dir = tmp_path / db_id
    db_dir.mkdir(parents=True)
    db_path = db_dir / f"{db_id}.sqlite"
    
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE numbers (val REAL)")
    conn.execute("INSERT INTO numbers VALUES (0), (90), (1)")
    conn.commit()
    conn.close()

    executor = SpiderQueryExecutor(database_dir=str(tmp_path))
    
    res_sin = executor.execute_query(db_id, "SELECT SIN(0) as result FROM numbers LIMIT 1")
    assert res_sin["success"] is True
    assert res_sin["results"][0]["result"] == 0.0

    res_sqrt = executor.execute_query(db_id, "SELECT SQRT(1) as result FROM numbers LIMIT 1")
    assert res_sqrt["success"] is True
    assert res_sqrt["results"][0]["result"] == 1.0
    
    res_power = executor.execute_query(db_id, "SELECT POWER(2, 3) as result FROM numbers LIMIT 1")
    assert res_power["success"] is True
    assert res_power["results"][0]["result"] == 8.0