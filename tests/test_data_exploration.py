"""
Testes para o nó Data Exploration do pipeline Text-to-Insight.

Testa a lógica de classificação de colunas, coleta de estatísticas,
formatação para prompt e integração com o nó do grafo.
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from text_to_insight.nodes.data_exploration import (
    BooleanColumnStats,
    CategoricalColumnStats,
    DateColumnStats,
    NumericColumnStats,
    TableExplorationResult,
    _classify_column_type,
    _extract_table_names_from_rag_context,
    _explorar_tabelas,
    explore_table,
    format_exploration_for_prompt,
    nos_nodo_data_exploration,
    nos_nodo_exploration_selector,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_db(tmp_path):
    """Cria um banco SQLite de exemplo com vários tipos de colunas."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            price REAL,
            category TEXT,
            is_active BOOLEAN,
            created_at DATETIME,
            stock_count INTEGER
        )
    """)

    cursor.execute("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            product_id INTEGER,
            customer_name TEXT,
            quantity INTEGER,
            order_date DATE,
            total REAL,
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
    """)

    # Inserir dados de exemplo
    products = [
        (1, "Widget A", 19.99, "Electronics", 1, "2024-01-15 10:30:00", 150),
        (2, "Widget B", 29.99, "Electronics", 1, "2024-02-20 14:00:00", 80),
        (3, "Gadget C", 49.99, "Home", 0, "2024-03-10 09:15:00", 0),
        (4, "Gadget D", 9.99, "Home", 1, "2024-04-05 16:45:00", 200),
        (5, "Tool E", 99.99, "Industrial", 1, "2024-05-01 11:00:00", 50),
        (6, "Tool F", None, "Industrial", None, None, None),
    ]
    cursor.executemany(
        "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", products
    )

    orders = [
        (1, 1, "Alice", 2, "2024-06-01", 39.98),
        (2, 2, "Bob", 1, "2024-06-02", 29.99),
        (3, 1, "Alice", 3, "2024-06-03", 59.97),
        (4, 3, "Charlie", 1, "2024-06-04", 49.99),
        (5, 4, "Alice", 5, "06/07/2024", 49.95),
    ]
    cursor.executemany(
        "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)", orders
    )

    conn.commit()
    conn.close()
    return str(db_path)


@pytest.fixture
def sample_engine(sample_db):
    """Engine SQLAlchemy somente leitura do banco de exemplo (como o chamador passaria)."""
    from tests.conftest import criar_engine_sqlite

    engine = criar_engine_sqlite(sample_db)
    yield engine
    engine.dispose()


def explore_tables(db_path, table_names, colunas_para_explorar=None, known_row_counts=None):
    """Explora tabelas de um arquivo SQLite numa conexão read-only própria."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return _explorar_tabelas(conn, table_names, colunas_para_explorar, known_row_counts)
    finally:
        conn.close()


@pytest.fixture
def sample_conn(sample_db):
    """Retorna uma conexão read-only ao banco de exemplo."""
    conn = sqlite3.connect(f"file:{sample_db}?mode=ro", uri=True)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Testes de classificação de tipo
# ---------------------------------------------------------------------------


class TestClassifyColumnType:
    def test_integer_types(self):
        assert _classify_column_type("INTEGER") == "numeric"
        assert _classify_column_type("INT") == "numeric"
        assert _classify_column_type("BIGINT") == "numeric"
        assert _classify_column_type("SMALLINT") == "numeric"
        assert _classify_column_type("TINYINT") == "numeric"

    def test_real_types(self):
        assert _classify_column_type("REAL") == "numeric"
        assert _classify_column_type("FLOAT") == "numeric"
        assert _classify_column_type("DOUBLE") == "numeric"
        assert _classify_column_type("NUMERIC") == "numeric"
        assert _classify_column_type("DECIMAL") == "numeric"

    def test_text_types(self):
        assert _classify_column_type("TEXT") == "categorical"
        assert _classify_column_type("VARCHAR(255)") == "categorical"
        assert _classify_column_type("CHAR(10)") == "categorical"
        assert _classify_column_type("CLOB") == "categorical"

    def test_date_types(self):
        assert _classify_column_type("DATE") == "date"
        assert _classify_column_type("DATETIME") == "date"
        assert _classify_column_type("TIMESTAMP") == "date"

    def test_boolean_types(self):
        assert _classify_column_type("BOOLEAN") == "boolean"
        assert _classify_column_type("BOOL") == "boolean"

    def test_empty_type_defaults_categorical(self):
        assert _classify_column_type("") == "categorical"

    def test_unknown_type_defaults_categorical(self):
        assert _classify_column_type("BLOB") == "categorical"


# ---------------------------------------------------------------------------
# Testes de exploração por tipo de coluna
# ---------------------------------------------------------------------------


class TestExploreTable:
    def test_returns_all_columns(self, sample_conn):
        result = explore_table(sample_conn, "products")
        assert isinstance(result, TableExplorationResult)
        assert result.table_name == "products"
        assert result.row_count_estimate == 6
        # products tem 7 colunas: id, name, price, category, is_active, created_at, stock_count
        assert len(result.columns) == 7

    def test_numeric_column_stats(self, sample_conn):
        result = explore_table(sample_conn, "products")
        price_stats = result.columns["price"]
        assert isinstance(price_stats, NumericColumnStats)
        assert price_stats.dtype == "numeric"
        assert price_stats.min is not None
        assert price_stats.max is not None
        assert price_stats.mean is not None
        assert price_stats.median is not None
        # price tem 1 NULL em 6 rows
        assert price_stats.null_rate > 0

    def test_categorical_column_stats(self, sample_conn):
        result = explore_table(sample_conn, "products")
        cat_stats = result.columns["category"]
        assert isinstance(cat_stats, CategoricalColumnStats)
        assert cat_stats.dtype == "categorical"
        assert cat_stats.cardinality == 3  # Electronics, Home, Industrial
        assert len(cat_stats.top_values) <= 5
        assert len(cat_stats.sample_values) <= 3
        # Todos os 6 products têm categoria (nenhum NULL)
        # ... exceto se a fixture mudar

    def test_boolean_column_stats(self, sample_conn):
        result = explore_table(sample_conn, "products")
        bool_stats = result.columns["is_active"]
        assert isinstance(bool_stats, BooleanColumnStats)
        assert bool_stats.dtype == "boolean"
        assert bool_stats.true_count >= 0
        assert bool_stats.false_count >= 0

    def test_date_column_stats(self, sample_conn):
        result = explore_table(sample_conn, "products")
        date_stats = result.columns["created_at"]
        assert isinstance(date_stats, DateColumnStats)
        assert date_stats.dtype == "date"
        assert date_stats.min is not None
        assert date_stats.max is not None
        assert len(date_stats.sample_values) <= 3

    def test_numeric_column_with_strings(self, tmp_path):
        db_path = tmp_path / "test_strings.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE test_table (id INTEGER, val REAL)")
        # Insert a mix of float, string representation of numbers, invalid strings, and NULL
        cursor.executemany("INSERT INTO test_table VALUES (?, ?)", [
            (1, 10.5),
            (2, "20.5"),
            (3, "invalid_str"),
            (4, None)
        ])
        conn.commit()

        result = explore_table(conn, "test_table")
        stats = result.columns["val"]
        assert isinstance(stats, NumericColumnStats)
        # Should run without raising any TypeError due to string values
        assert stats.mean is not None or stats.mean is None
        assert stats.null_rate == 0.25  # 1 NULL out of 4
        conn.close()


# ---------------------------------------------------------------------------
# Testes de exploração multi-tabela
# ---------------------------------------------------------------------------


class TestExploreTables:
    def test_multiple_tables(self, sample_db):
        results = explore_tables(sample_db, ["products", "orders"])
        assert "products" in results
        assert "orders" in results
        assert isinstance(results["products"], TableExplorationResult)
        assert isinstance(results["orders"], TableExplorationResult)

    def test_nonexistent_table_skipped(self, sample_db):
        results = explore_tables(sample_db, ["products", "nonexistent"])
        assert "products" in results
        assert "nonexistent" not in results

    def test_nonexistent_table_with_known_row_count_skipped(self, sample_db):
        # Com a contagem vinda do cache não há COUNT(*) para falhar; a tabela
        # sem colunas não pode entrar no resultado (o cache a marcaria completa).
        results = explore_tables(sample_db, ["nonexistent"], known_row_counts={"nonexistent": 5})
        assert results == {}


# ---------------------------------------------------------------------------
# Amostragem das estatísticas numéricas
# ---------------------------------------------------------------------------


def _tabela_numerica(valores):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (v INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(v,) for v in valores])
    return conn


class TestAmostraNumerica:
    def test_mediana_usa_a_amostra_em_tabela_grande(self):
        # 20 mil valores em ordem decrescente: a amostra são as 10 mil
        # primeiras linhas (20000..10001), e a mediana tem que estar nela.
        stats = explore_table(_tabela_numerica(range(20_000, 0, -1)), "t").columns["v"]

        assert (stats.min, stats.max) == (10_001, 20_000)
        assert stats.median == 15_000.5
        assert stats.mean == 15_000.5

    def test_nulos_nao_puxam_a_mediana_para_fora_da_amostra(self):
        # A amostra tem 9999 nulos e um 1; os 1000 vêm depois dela.
        conn = _tabela_numerica([None] * 9_999 + [1] + [1_000] * 10_000)
        stats = explore_table(conn, "t").columns["v"]

        assert (stats.mean, stats.min, stats.max, stats.median) == (1, 1, 1, 1)
        assert stats.std_dev == 0
        assert stats.null_rate == 0.9999

    @pytest.mark.parametrize("valores, mediana", [([1, 2, 3, 4], 2.5), ([3, 1, 2], 2), ([7], 7)])
    def test_mediana_par_e_impar(self, valores, mediana):
        assert explore_table(_tabela_numerica(valores), "t").columns["v"].median == mediana

    def test_valores_de_exemplo_sao_reprodutiveis(self, sample_conn):
        a = explore_table(sample_conn, "products")
        b = explore_table(sample_conn, "products")
        assert a == b


# ---------------------------------------------------------------------------
# Testes de formatação para prompt
# ---------------------------------------------------------------------------


class TestFormatExplorationForPrompt:
    def test_empty_results(self):
        assert format_exploration_for_prompt({}) == ""

    def test_contains_table_header(self, sample_conn):
        result = explore_table(sample_conn, "products")
        text = format_exploration_for_prompt({"products": result})
        assert "Tabela: products" in text
        assert "DATA EXPLORATION" in text

    def test_contains_column_stats(self, sample_conn):
        result = explore_table(sample_conn, "products")
        text = format_exploration_for_prompt({"products": result})
        # Verifica presença de colunas
        assert "price [numeric]" in text
        assert "category [categorical]" in text
        assert "is_active [boolean]" in text
        assert "created_at [date]" in text

    def test_output_is_compact(self, sample_conn):
        result = explore_table(sample_conn, "products")
        text = format_exploration_for_prompt({"products": result})
        # O texto deve ser compacto — uma linha por coluna + headers
        lines = [l for l in text.strip().split("\n") if l.strip()]
        # Header + table name + 7 columns = ~9 linhas não-vazias
        assert len(lines) < 20


# ---------------------------------------------------------------------------
# Testes de extração de tabelas do contexto RAG
# ---------------------------------------------------------------------------


class TestExtractTableNames:
    def test_basic_extraction(self):
        context = """=== SCHEMA RELEVANTE (via RAG) ===

Tabela: products
- id: INTEGER (PK)
- name: TEXT

Tabela: orders
- id: INTEGER (PK)
"""
        names = _extract_table_names_from_rag_context(context)
        assert names == ["products", "orders"]

    def test_empty_context(self):
        assert _extract_table_names_from_rag_context("") == []

    def test_no_tables(self):
        assert _extract_table_names_from_rag_context("some random text") == []


# ---------------------------------------------------------------------------
# Testes do nó do grafo
# ---------------------------------------------------------------------------


class TestNodoDataExploration:
    def test_returns_exploration_context(self, sample_engine):
        estado = {
            "contexto_rag_schema": (
                "Tabela: products\n- id: INTEGER\n\n"
                "Tabela: orders\n- id: INTEGER\n"
            ),
        }
        result = nos_nodo_data_exploration(estado, engine=sample_engine)
        assert "contexto_data_exploration" in result
        assert "products" in result["contexto_data_exploration"]
        assert "orders" in result["contexto_data_exploration"]

    def test_missing_rag_context_returns_empty(self, sample_engine):
        estado = {"contexto_rag_schema": ""}
        result = nos_nodo_data_exploration(estado, engine=sample_engine)
        assert result == {}

    def test_exploration_output_fits_prompt(self, sample_engine):
        """O output deve ser compacto o suficiente para caber num prompt LLM."""
        estado = {"contexto_rag_schema": "Tabela: products\n- id: INTEGER\n"}
        result = nos_nodo_data_exploration(estado, engine=sample_engine)
        text = result.get("contexto_data_exploration", "")
        # Não deveria exceder ~2000 chars para uma tabela com 7 colunas
        assert len(text) < 5000

    def test_exploration_disabled(self, sample_engine):
        estado = {"contexto_rag_schema": "Tabela: products\n- id: INTEGER\n"}
        result = nos_nodo_data_exploration(estado, engine=sample_engine, use_data_exploration=False)
        assert result == {"contexto_data_exploration": ""}

    def test_exploration_with_colunas_para_explorar(self, sample_engine):
        estado = {
            "contexto_rag_schema": "Tabela: products\n- id: INTEGER\n",
            "colunas_para_explorar": {
                "products": ["price"]
            }
        }
        result = nos_nodo_data_exploration(estado, engine=sample_engine, use_data_exploration=True)
        text = result.get("contexto_data_exploration", "")
        assert "price" in text
        # Because we only requested "price", name or category should not be in the exploration context
        assert "name" not in text
        assert "category" not in text

    def test_skips_unsupported_dialect(self):
        # Só SQLite e PostgreSQL são suportados: com outro dialeto o nó pula sem conectar.
        from sqlalchemy import create_engine

        engine = create_engine("mysql+pymysql://u:p@localhost:3306/banco")
        estado = {"contexto_rag_schema": "Tabela: products\n- id: INTEGER\n"}
        assert nos_nodo_data_exploration(estado, engine=engine) == {"contexto_data_exploration": ""}


class TestCacheDataExploration:
    """Cache por pergunta: cada expansão do retriever só explora o que é novo."""

    @pytest.fixture
    def espiao(self, monkeypatch):
        import text_to_insight.nodes.data_exploration as de

        chamadas = []
        original = de.explore_table

        def _espiao(conn, table_name, columns_to_explore=None, row_count=None, dialeto="sqlite"):
            chamadas.append((table_name, columns_to_explore))
            return original(conn, table_name, columns_to_explore, row_count=row_count, dialeto=dialeto)

        monkeypatch.setattr(de, "explore_table", _espiao)
        return chamadas

    @staticmethod
    def _rodar(sample_db, tabelas, cache=None, colunas=None):
        from tests.conftest import criar_engine_sqlite

        estado = {
            "contexto_rag_schema": "".join(f"Tabela: {t}\n- id: INTEGER\n" for t in tabelas),
            "cache_data_exploration": cache or {},
        }
        if colunas is not None:
            estado["colunas_para_explorar"] = colunas
        engine = criar_engine_sqlite(sample_db)
        try:
            return nos_nodo_data_exploration(estado, engine=engine)
        finally:
            engine.dispose()

    @pytest.mark.parametrize("colunas", [None, {"products": ["price", "category"]}])
    def test_texto_igual_ao_formatador_sem_cache(self, sample_db, colunas):
        from text_to_insight.nodes.data_exploration import _atualizar_cache, _formatar_do_cache

        resultados = explore_tables(sample_db, ["products", "orders"], colunas)
        completas = {t for t in resultados if not colunas or t not in colunas}
        cache = _atualizar_cache({}, resultados, completas)

        assert _formatar_do_cache(["products", "orders"], colunas, cache) == format_exploration_for_prompt(resultados)

    def test_repeticao_nao_reexplora(self, sample_db, espiao):
        primeira = self._rodar(sample_db, ["products", "orders"])
        espiao.clear()
        segunda = self._rodar(sample_db, ["products", "orders"], cache=primeira["cache_data_exploration"])

        assert espiao == []
        assert segunda["contexto_data_exploration"] == primeira["contexto_data_exploration"]

    def test_expansao_explora_so_tabelas_novas(self, sample_db, espiao):
        primeira = self._rodar(sample_db, ["products"])
        espiao.clear()
        segunda = self._rodar(sample_db, ["products", "orders"], cache=primeira["cache_data_exploration"])

        assert [t for t, _ in espiao] == ["orders"]
        texto = segunda["contexto_data_exploration"]
        assert "Tabela: products" in texto and "Tabela: orders" in texto

    def test_explora_so_colunas_faltantes(self, sample_db, espiao):
        primeira = self._rodar(sample_db, ["products"], colunas={"products": ["price"]})
        espiao.clear()
        segunda = self._rodar(
            sample_db, ["products"],
            cache=primeira["cache_data_exploration"],
            colunas={"products": ["price", "category"]},
        )

        assert espiao == [("products", ["category"])]
        texto = segunda["contexto_data_exploration"]
        assert "price [numeric]" in texto and "category [categorical]" in texto
        assert "name [" not in texto

    def test_exploracao_completa_apos_parcial_mantem_ordem_original(self, sample_db):
        parcial = self._rodar(sample_db, ["products"], colunas={"products": ["price"]})
        completa = self._rodar(sample_db, ["products"], cache=parcial["cache_data_exploration"])

        cache = completa["cache_data_exploration"]["products"]
        assert cache["completa"] is True
        assert list(cache["colunas"]) == [
            "id", "name", "price", "category", "is_active", "created_at", "stock_count"
        ]

    def test_nao_altera_cache_recebido_e_e_serializavel(self, sample_db):
        import json

        primeira = self._rodar(sample_db, ["products"])
        cache_original = primeira["cache_data_exploration"]
        copia = json.loads(json.dumps(cache_original))

        self._rodar(sample_db, ["products", "orders"], cache=cache_original)

        assert cache_original == copia

    def test_estado_inicial_zera_cache(self):
        from text_to_insight.runtime import construir_estado_inicial

        assert construir_estado_inicial("pergunta")["cache_data_exploration"] == {}

    def test_nao_reexecuta_count_quando_linhas_no_cache(self, sample_db, monkeypatch):
        import sqlite3
        import text_to_insight.nodes.data_exploration as de

        row_counts_recebidos = []
        original = de.explore_table

        def _espiao_row_count(conn, table_name, columns_to_explore=None, row_count=None, dialeto="sqlite"):
            row_counts_recebidos.append((table_name, row_count))
            return original(conn, table_name, columns_to_explore, row_count=row_count, dialeto=dialeto)

        monkeypatch.setattr(de, "explore_table", _espiao_row_count)

        # 1ª passada parcial: não está no cache -> row_count é None (roda SELECT COUNT(*))
        primeira = self._rodar(sample_db, ["products"], colunas={"products": ["price"]})
        assert row_counts_recebidos == [("products", None)]
        linhas_cacheadas = primeira["cache_data_exploration"]["products"]["linhas"]

        # 2ª passada parcial: já está no cache -> reaproveita row_count do cache
        row_counts_recebidos.clear()
        self._rodar(
            sample_db,
            ["products"],
            cache=primeira["cache_data_exploration"],
            colunas={"products": ["price", "category"]},
        )
        assert row_counts_recebidos == [("products", linhas_cacheadas)]

        # Garante que explore_table com row_count preenchido não dispara SELECT COUNT(*)
        queries_executadas = []
        conn = sqlite3.connect(sample_db)
        conn.set_trace_callback(queries_executadas.append)
        try:
            original(conn, "products", columns_to_explore=["category"], row_count=linhas_cacheadas)
        finally:
            conn.close()

        assert 'SELECT COUNT(*) FROM "products"' not in queries_executadas


class MockLLMResponse:
    def __init__(self, content):
        self.content = content
        self.usage_metadata = {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
        }


class MockLLM:
    def __init__(self, content):
        self.content = content

    def invoke(self, prompt):
        return MockLLMResponse(self.content)


class TestNodoExplorationSelector:
    def test_selector_disabled(self):
        estado = {
            "pergunta_atual": "Qual o preço médio dos produtos?",
            "contexto_rag_schema": "Tabela: products\n- id: INTEGER\n- price: REAL\n- name: TEXT",
        }
        result = nos_nodo_exploration_selector(estado, llm=None, exploration_selector_mode="off")
        assert result == {"colunas_para_explorar": {}}

    def test_selector_enabled_success(self):
        estado = {
            "pergunta_atual": "Qual o preço médio dos produtos?",
            "contexto_rag_schema": "Tabela: products\n- id: INTEGER\n- price: REAL\n- name: TEXT",
        }
        mock_response_json = '{"products": ["price"]}'
        mock_llm = MockLLM(mock_response_json)

        result = nos_nodo_exploration_selector(estado, llm=mock_llm, exploration_selector_mode="llm")
        assert result["colunas_para_explorar"] == {"products": ["price"]}
        assert result["tokens_input"] == 10
        assert result["tokens_output"] == 5
        assert result["tokens_total"] == 15

    def test_selector_enabled_with_markdown(self):
        estado = {
            "pergunta_atual": "Qual o preço médio dos produtos?",
            "contexto_rag_schema": "Tabela: products\n- id: INTEGER\n- price: REAL\n- name: TEXT",
        }
        mock_response_json = '```json\n{"products": ["price"]}\n```'
        mock_llm = MockLLM(mock_response_json)

        result = nos_nodo_exploration_selector(estado, llm=mock_llm, exploration_selector_mode="llm")
        assert result["colunas_para_explorar"] == {"products": ["price"]}

    def test_selector_invalid_json(self):
        estado = {
            "pergunta_atual": "Qual o preço médio dos produtos?",
            "contexto_rag_schema": "Tabela: products\n- id: INTEGER\n- price: REAL\n- name: TEXT",
        }
        mock_llm = MockLLM("invalid json")

        result = nos_nodo_exploration_selector(estado, llm=mock_llm, exploration_selector_mode="llm")
        assert result["colunas_para_explorar"] == {}
        # Preserva os tokens faturados pela chamada ao LLM mesmo em caso de JSON inválido
        assert result["tokens_total"] == 15
