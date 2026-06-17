"""Testes da feature de conexão externa (PEP 249) com o banco de dados.

Cobrem o novo contrato em que o `InsightEngine` aceita uma conexão já aberta
(`conn`) além do `db_path` legado, e o comportamento dos nós que tocam o banco
quando recebem uma conexão injetada.

Nenhuma chamada de API é feita: o `Graph` real é substituído por um fake.
"""

from __future__ import annotations

import importlib
import sqlite3

import pytest

from text_to_insight import InsightEngine

ie_module = importlib.import_module("text_to_insight.InsightEngine")
from text_to_insight.nodes.schema import nos_nodo_esquema
from text_to_insight.nodes.sandbox import nos_nodo_sandbox
from text_to_insight.nodes.code_agent.code_sql import executar_sql_conn


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

def _popular_conexao(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.execute("CREATE TABLE pedidos (id INTEGER PRIMARY KEY, valor REAL)")
    conn.executemany("INSERT INTO pedidos (id, valor) VALUES (?, ?)", [(1, 10.0), (2, 20.0)])
    conn.commit()
    return conn


@pytest.fixture
def conn_memoria() -> sqlite3.Connection:
    conn = _popular_conexao(sqlite3.connect(":memory:"))
    yield conn
    conn.close()


class _FakeGraph:
    """Substitui o Graph real para não exigir LLM/API nos testes do engine."""

    def __init__(self, api_key, model, conn=None, hitl=True, **kwargs):
        self.conn_recebida = conn
        self.grafo_text_to_insight = object()


@pytest.fixture
def patched_graph(monkeypatch):
    monkeypatch.setattr(ie_module, "Graph", _FakeGraph)


# --------------------------------------------------------------------------- #
# executar_sql_conn — núcleo agnóstico ao banco
# --------------------------------------------------------------------------- #

def test_executar_sql_conn_retorna_linhas_como_dicts(conn_memoria):
    resultado = executar_sql_conn(conn_memoria, "SELECT id, valor FROM pedidos ORDER BY id")

    assert resultado["ok"] is True
    assert resultado["total_linhas_resultado"] == 2
    assert resultado["linhas_resultado_completo"][0] == {"id": 1, "valor": 10.0}


def test_executar_sql_conn_bloqueia_escrita(conn_memoria):
    resultado = executar_sql_conn(conn_memoria, "DELETE FROM pedidos")

    # A escrita é rejeitada pela validação de segurança e os dados ficam intactos.
    assert resultado["ok"] is False
    cur = conn_memoria.cursor()
    cur.execute("SELECT COUNT(*) FROM pedidos")
    assert cur.fetchone()[0] == 2


def test_executar_sql_conn_nao_fecha_conexao(conn_memoria):
    executar_sql_conn(conn_memoria, "SELECT 1")

    # A conexão deve continuar utilizável: quem abriu é quem fecha.
    cur = conn_memoria.cursor()
    cur.execute("SELECT COUNT(*) FROM pedidos")
    assert cur.fetchone()[0] == 2


# --------------------------------------------------------------------------- #
# Nós com conexão injetada
# --------------------------------------------------------------------------- #

def test_schema_usa_conexao_injetada(conn_memoria):
    resultado = nos_nodo_esquema({"pergunta_atual": "x"}, conn=conn_memoria)

    assert resultado["status"] == "schema_obtido"
    assert "pedidos" in resultado["contexto_schema"]


def test_sandbox_usa_conexao_injetada(conn_memoria):
    estado = {"sql_gerada": "SELECT id FROM pedidos ORDER BY id"}
    resultado = nos_nodo_sandbox(estado, conn=conn_memoria)

    assert resultado["status"] == "exec_ok"
    assert resultado["total_linhas_resultado"] == 2


# --------------------------------------------------------------------------- #
# InsightEngine — resolução de conexão, validação e ownership
# --------------------------------------------------------------------------- #

def test_engine_aceita_conexao_e_nao_a_fecha(patched_graph, conn_memoria):
    engine = InsightEngine(api_key="fake", model="gemini-2.5-flash", conn=conn_memoria)

    assert engine._owns_conn is False
    engine.close()
    # Conexão fornecida pelo usuário não deve ter sido fechada.
    conn_memoria.execute("SELECT 1")


def test_engine_com_db_path_e_dono_da_conexao(patched_graph):
    engine = InsightEngine(
        api_key="fake",
        model="gemini-2.5-flash",
        db_path="data/olist_relational.db",
    )

    assert engine._owns_conn is True
    assert engine._db_path == "data/olist_relational.db"
    conn = engine._conn
    engine.close()
    # Conexão própria deve ter sido fechada.
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_engine_sem_conn_e_sem_db_path_levanta_erro(patched_graph):
    with pytest.raises(ValueError, match="conn.*db_path|db_path"):
        InsightEngine(api_key="fake", model="gemini-2.5-flash")


def test_engine_valida_conexao_inativa(patched_graph):
    conn = sqlite3.connect(":memory:")
    conn.close()  # conexão morta

    with pytest.raises(ValueError, match="inválida ou inativa"):
        InsightEngine(api_key="fake", model="gemini-2.5-flash", conn=conn)


def test_engine_context_manager_fecha_conexao_propria(patched_graph):
    with InsightEngine(
        api_key="fake",
        model="gemini-2.5-flash",
        db_path="data/olist_relational.db",
    ) as engine:
        conn = engine._conn

    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
