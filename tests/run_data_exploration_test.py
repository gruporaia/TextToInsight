#!/usr/bin/env python3
"""Quick smoke test for data_exploration module."""
import sqlite3
import sys
import tempfile
from pathlib import Path

# Ensure the project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from text_to_insight.nodes.data_exploration import (
    _classify_column_type,
    explore_table,
    explore_tables,
    format_exploration_for_prompt,
    nos_nodo_data_exploration,
    NumericColumnStats,
    CategoricalColumnStats,
    DateColumnStats,
    BooleanColumnStats,
)

def create_test_db():
    db_path = Path(tempfile.mkdtemp()) / "test.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("""
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
    products = [
        (1, "Widget A", 19.99, "Electronics", 1, "2024-01-15 10:30:00", 150),
        (2, "Widget B", 29.99, "Electronics", 1, "2024-02-20 14:00:00", 80),
        (3, "Gadget C", 49.99, "Home", 0, "2024-03-10 09:15:00", 0),
        (4, "Gadget D", 9.99, "Home", 1, "2024-04-05 16:45:00", 200),
        (5, "Tool E", 99.99, "Industrial", 1, "2024-05-01 11:00:00", 50),
        (6, "Tool F", None, "Industrial", None, None, None),
    ]
    cur.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", products)
    conn.commit()
    conn.close()
    return str(db_path)

def main():
    passed = 0
    failed = 0

    # Test 1: Type classification
    print("Test 1: Type classification...")
    assert _classify_column_type("INTEGER") == "numeric"
    assert _classify_column_type("TEXT") == "categorical"
    assert _classify_column_type("DATETIME") == "date"
    assert _classify_column_type("BOOLEAN") == "boolean"
    assert _classify_column_type("") == "categorical"
    print("  PASSED")
    passed += 1

    # Test 2: Explore single table
    print("Test 2: Explore single table...")
    db_path = create_test_db()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    result = explore_table(conn, "products")
    assert result.table_name == "products"
    assert result.row_count_estimate == 6
    assert len(result.columns) == 7
    assert isinstance(result.columns["price"], NumericColumnStats)
    assert isinstance(result.columns["category"], CategoricalColumnStats)
    assert isinstance(result.columns["is_active"], BooleanColumnStats)
    assert isinstance(result.columns["created_at"], DateColumnStats)
    conn.close()
    print("  PASSED")
    passed += 1

    # Test 3: Numeric stats
    print("Test 3: Numeric column stats...")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    result = explore_table(conn, "products")
    price = result.columns["price"]
    assert price.min is not None
    assert price.max is not None
    assert price.mean is not None
    assert price.null_rate > 0  # 1 NULL out of 6
    conn.close()
    print("  PASSED")
    passed += 1

    # Test 4: Multi-table exploration
    print("Test 4: Multi-table exploration...")
    results = explore_tables(db_path, ["products"])
    assert "products" in results
    print("  PASSED")
    passed += 1

    # Test 5: Format for prompt
    print("Test 5: Format for prompt...")
    results = explore_tables(db_path, ["products"])
    text = format_exploration_for_prompt(results)
    assert "Tabela: products" in text
    assert "DATA EXPLORATION" in text
    assert "price [numeric]" in text
    assert "category [categorical]" in text
    print("  PASSED")
    passed += 1

    # Test 6: Graph node
    print("Test 6: Graph node integration...")
    estado = {
        "db_path": db_path,
        "contexto_rag_schema": "Tabela: products\n- id: INTEGER\n",
    }
    result = nos_nodo_data_exploration(estado)
    assert "contexto_data_exploration" in result
    assert "products" in result["contexto_data_exploration"]
    print("  PASSED")
    passed += 1

    # Test 7: Empty context returns empty
    print("Test 7: Missing context returns empty...")
    result = nos_nodo_data_exploration({"db_path": db_path, "contexto_rag_schema": ""})
    assert result == {}
    print("  PASSED")
    passed += 1

    # Test 8: Format compactness
    print("Test 8: Output compactness...")
    results = explore_tables(db_path, ["products"])
    text = format_exploration_for_prompt(results)
    assert len(text) < 5000, f"Output too large: {len(text)} chars"
    print("  PASSED")
    passed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
    print("All tests passed!")

if __name__ == "__main__":
    main()
