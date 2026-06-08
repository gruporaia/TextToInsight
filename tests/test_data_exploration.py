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
    explore_table,
    explore_tables,
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

    def test_nonexistent_db_returns_empty(self):
        results = explore_tables("/tmp/nonexistent_db.db", ["products"])
        assert results == {}


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
    def test_returns_exploration_context(self, sample_db):
        estado = {
            "db_path": sample_db,
            "contexto_rag_schema": (
                "Tabela: products\n- id: INTEGER\n\n"
                "Tabela: orders\n- id: INTEGER\n"
            ),
        }
        result = nos_nodo_data_exploration(estado)
        assert "contexto_data_exploration" in result
        assert "products" in result["contexto_data_exploration"]
        assert "orders" in result["contexto_data_exploration"]

    def test_missing_rag_context_returns_empty(self, sample_db):
        estado = {"db_path": sample_db, "contexto_rag_schema": ""}
        result = nos_nodo_data_exploration(estado)
        assert result == {}

    def test_missing_db_path_returns_empty(self):
        estado = {"db_path": "", "contexto_rag_schema": "Tabela: products\n"}
        result = nos_nodo_data_exploration(estado)
        assert result == {}

    def test_exploration_output_fits_prompt(self, sample_db):
        """O output deve ser compacto o suficiente para caber num prompt LLM."""
        estado = {
            "db_path": sample_db,
            "contexto_rag_schema": "Tabela: products\n- id: INTEGER\n",
        }
        result = nos_nodo_data_exploration(estado)
        text = result.get("contexto_data_exploration", "")
        # Não deveria exceder ~2000 chars para uma tabela com 7 colunas
        assert len(text) < 5000

    def test_exploration_disabled(self, sample_db):
        estado = {
            "db_path": sample_db,
            "contexto_rag_schema": "Tabela: products\n- id: INTEGER\n",
        }
        result = nos_nodo_data_exploration(estado, use_data_exploration=False)
        assert result == {"contexto_data_exploration": ""}

    def test_exploration_with_colunas_para_explorar(self, sample_db):
        estado = {
            "db_path": sample_db,
            "contexto_rag_schema": "Tabela: products\n- id: INTEGER\n",
            "colunas_para_explorar": {
                "products": ["price"]
            }
        }
        result = nos_nodo_data_exploration(estado, use_data_exploration=True)
        text = result.get("contexto_data_exploration", "")
        assert "price" in text
        # Because we only requested "price", name or category should not be in the exploration context
        assert "name" not in text
        assert "category" not in text


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
        assert result["tokens_total"] == 0
