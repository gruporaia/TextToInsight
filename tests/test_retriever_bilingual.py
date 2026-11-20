"""
Testes do retriever bilíngue (PT/EN):
- BM25 bilíngue (Snowball EN + RSLP PT, sem dupla contagem, camelCase/snake_case,
  proteção do vocabulário do schema e limpeza de ruído DDL/markdown);
- Embeddings multilíngues ONNX (PT -> EN sem docs, e PT -> PT);
- Fusão híbrida sem gate rígido de BM25 em regras semânticas;
- Guardrail `max_tabelas_por_regra` evitando explosão de regras de desambiguação;
- Preservação do DDL bruto retornado ao LLM e collection key com ID do modelo.
"""

from __future__ import annotations

import chromadb

from text_to_insight.retriever.embeddings import (
    OnnxMultilingualE5EmbeddingFunction,
    embedding_model_id,
    get_default_embedding_function,
)
from text_to_insight.retriever.engine import SchemaGraphRAG
from text_to_insight.retriever.rag_logic import (
    RAGRetriever,
    _BilingualBM25Index,
    _clean_ddl_for_search,
    _clean_markdown_table_doc,
)

ECOM_EN_SCHEMA = """=== SCHEMA SQLITE ===

Tabela: customers
- id: INTEGER (PK, NOT NULL)
- firstName: TEXT (NOT NULL)
- lastName: TEXT (NOT NULL)
- email: TEXT (NOT NULL)

Tabela: orders
- id: INTEGER (PK, NOT NULL)
- order_no: TEXT (NOT NULL)
- customer_id: INTEGER (NOT NULL)
- order_date: DATETIME
- status: TEXT
- total_amount: REAL
  Foreign keys:
  - customer_id -> customers.id

Tabela: invoices
- id: INTEGER (PK, NOT NULL)
- order_id: INTEGER (NOT NULL)
- total_amount: REAL (NOT NULL)
- issued_at: DATETIME
- paid: INTEGER
  Foreign keys:
  - order_id -> orders.id

Tabela: products
- id: INTEGER (PK, NOT NULL)
- category_id: INTEGER
- name: TEXT (NOT NULL)
- price: REAL (NOT NULL)
  Foreign keys:
  - category_id -> categories.id

Tabela: categories
- id: INTEGER (PK, NOT NULL)
- name: TEXT (NOT NULL)

Tabela: warehouses
- id: INTEGER (PK, NOT NULL)
- name: TEXT (NOT NULL)
- city: TEXT
- capacity: INTEGER

Tabela: inventory
- id: INTEGER (PK, NOT NULL)
- product_id: INTEGER (NOT NULL)
- warehouse_id: INTEGER (NOT NULL)
- quantity: INTEGER
  Foreign keys:
  - product_id -> products.id
  - warehouse_id -> warehouses.id

Tabela: employees
- id: INTEGER (PK, NOT NULL)
- department_id: INTEGER
- first_name: TEXT (NOT NULL)
- salary: REAL
  Foreign keys:
  - department_id -> departments.id

Tabela: departments
- id: INTEGER (PK, NOT NULL)
- name: TEXT (NOT NULL)
- budget: REAL

Tabela: companies
- id: INTEGER (PK, NOT NULL)
- company_name: TEXT (NOT NULL)
- address: TEXT
"""


def test_bm25_bilingue_resolve_morfologia_ingles_e_portugues():
    idx = _BilingualBM25Index()
    idx.build(
        ids=["t_companies", "t_categories", "t_status", "t_assinaturas"],
        texts=[
            "company address running cancelled",
            "category column delivery",
            "status order_no",
            "assinatura de clientes na livraria",
        ],
        schema_vocab={"order", "no", "company", "category", "status"},
    )

    assert idx.retrieve("companies addresses run cancel", top_k=1)[0][0] == "t_companies"
    assert idx.retrieve("categories columns deliveries", top_k=1)[0][0] == "t_categories"
    assert idx.retrieve("statuses order_no", top_k=1)[0][0] == "t_status"
    assert idx.retrieve("assinaturas dos clientes nas livrarias", top_k=1)[0][0] == "t_assinaturas"


def test_bm25_bilingue_camelcase_e_match_exato_sem_dupla_contagem():
    idx = _BilingualBM25Index()
    idx.build(
        ids=["doc_exact", "doc_stem"],
        texts=["firstName orders", "firstName ordering"],
    )
    # `doc_len` conta palavras reais ("first", "name", "orders" -> 3), sem duplicar stems.
    assert idx._doc_len[0] == 3
    hits = idx.retrieve("first name orders", top_k=2)
    assert hits[0][0] == "doc_exact"
    assert hits[0][1] > hits[1][1]


def test_limpeza_ddl_e_markdown_remove_ruido_e_referencias():
    ddl = (
        "Tabela: customer_accounts\n"
        "- id: UUID (PK, NOT NULL)\n"
        "- firstName: VARCHAR (NOT NULL) -- [Primeiro nome]\n"
        "  Foreign keys:\n"
        "  - org_id -> orgs.id\n"
    )
    cleaned, cols = _clean_ddl_for_search("customer_accounts", ddl)
    assert cols == ["id", "firstName"]
    assert "UUID" not in cleaned
    assert "VARCHAR" not in cleaned
    assert "first Name" in cleaned
    assert "Primeiro nome" in cleaned

    md = (
        "Conta de cliente cadastrada.\n\n"
        "- **Referenciada por:** `tabela_nao_relacionada_1.account_id`, `tabela_nao_relacionada_2.account_id`\n\n"
        "| coluna | tipo | obrig. | chave | valores possíveis | uso | descrição |\n"
        "|---|---|---|---|---|---|---|\n"
        "| status | CharField |  |  | ACTIVE, INACTIVE | livre | Estado da conta. |\n"
    )
    cleaned_md = _clean_markdown_table_doc(md)
    assert "tabela_nao_relacionada_1" not in cleaned_md
    assert "CharField" not in cleaned_md
    assert "Estado da conta." in cleaned_md
    assert "ACTIVE, INACTIVE" in cleaned_md


def test_retriever_pt_query_vs_en_schema_sem_docs_e_preserva_ddl_bruto(tmp_path):
    rag = SchemaGraphRAG(
        schema={"contexto_schema": ECOM_EN_SCHEMA},
        cache_dir=tmp_path,
    )
    # Collection name inclui o slug do embedder ativo para evitar colisão de vetores.
    ef = get_default_embedding_function()
    assert embedding_model_id(ef) in rag.rag.collection_name

    # Consultas em português contra schema 100% em inglês (sem dicionário PT)
    res_inv, _ = rag.retrieve("Quantas faturas ainda não foram pagas?", top_k=3)
    assert "invoices" in res_inv["tabelas_ordenadas"]
    # O documento retornado ao LLM deve continuar sendo o DDL original (com tipos/PK/FK)
    assert "INTEGER (PK, NOT NULL)" in res_inv["tabelas_documentos"]["invoices"]

    res_emp, _ = rag.retrieve(
        "Quais funcionários têm o maior salário por departamento?", top_k=3
    )
    assert {"employees", "departments"}.issubset(set(res_emp["tabelas_ordenadas"]))

    res_stock, _ = rag.retrieve(
        "Quais produtos têm menor estoque nos armazéns?", top_k=5
    )
    assert {"products", "inventory", "warehouses"}.issubset(
        set(res_stock["tabelas_ordenadas"])
    )


def test_guardrail_regra_ampla_nao_explode_tabelas_nao_relacionadas(tmp_path):
    schema = """=== SCHEMA ===

Tabela: store_catalog_item
- id: INTEGER (PK)
- title: TEXT
- status: TEXT

Tabela: warehouse_item
- id: INTEGER (PK)
- aisle: TEXT

Tabela: office_asset
- id: INTEGER (PK)
- label: TEXT

Tabela: digital_asset
- id: INTEGER (PK)
- title: TEXT
"""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "regras.md").write_text(
        "1. **Desambiguacao de catalogo**: `warehouse_item` (item de estoque) != "
        "`store_catalog_item` (produto de vitrine); `office_asset` != `digital_asset`.\n\n"
        "#### `store_catalog_item`\n\nProduto anunciado na vitrine da loja virtual.\n",
        encoding="utf-8",
    )

    rag = SchemaGraphRAG(
        schema={"contexto_schema": schema},
        docs_dir=docs_dir,
        cache_dir=tmp_path / "cache",
        max_tabelas_por_regra=2,
    )
    ret, _ = rag.retrieve("Quantos produtos da vitrine estão ativos?", top_k=1)
    assert "store_catalog_item" in ret["tabelas_ordenadas"]
    # A regra de desambiguação é anexada via Tabela -> Regra, sem puxar as outras 3 tabelas
    assert "rule:desambiguacao_de_catalogo" in ret["ids"][0]
    assert len(ret["tabelas_ordenadas"]) == 1


def test_regra_semantica_sem_overlap_bm25_nao_e_descartada(tmp_path):
    schema = """=== SCHEMA ===

Tabela: orders
- id: INTEGER (PK)
- customer_id: INTEGER
- created_at: DATE

Tabela: customers
- id: INTEGER (PK)
- name: TEXT
"""
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    ef = get_default_embedding_function()
    if not isinstance(ef, OnnxMultilingualE5EmbeddingFunction):
        return  # Só valida threshold multilíngue quando E5 ONNX está ativo

    r = RAGRetriever(
        chroma_client=client,
        document_schema={"contexto_schema": schema},
        schema_info=[
            {
                "id": "rule:evasao_90d",
                "tipo": "regra",
                "titulo": "Churn de clientes",
                "descricao": "Cliente sem compras nos últimos 90 dias na tabela orders.",
                "tabelas_alvo": ["orders", "customers"],
                "colunas_alvo": [],
            }
        ],
        collection_name="t_sem_rule",
        embedding_function=ef,
    )
    # Consulta sem overlap léxico com o texto da regra ("evasão" / "inativos" vs "churn" / "sem compras")
    out = r._query("Quais compradores pararam de adquirir produtos há três meses?", top_k=2)
    assert "rule:evasao_90d" in out["ids"][0]
