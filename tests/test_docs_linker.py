from text_to_insight.retriever.docs_linker import extrair_trechos

SCHEMA = """=== SCHEMA ===

Tabela: clientes
- id: INTEGER (PK, NOT NULL)
- email: TEXT
- status: TEXT

Tabela: pedidos
- id: INTEGER (PK, NOT NULL)
- cliente_id: INTEGER
- criado_em: TIMESTAMP
- cancelado: BOOLEAN
  Foreign keys:
  - cliente_id -> clientes.id
"""

DOC = """# Manual da loja

## Regras de negócio

Estas definições valem para todos os relatórios.

1. **Cliente ativo** = cliente com pedido nos últimos 90 dias em `pedidos.criado_em`.
2. **Pedido válido**: ignore as linhas com `cancelado = true` em `pedidos`.
3. **Ticket médio** é a receita dividida pelo número de pedidos válidos.

## Tabelas

### `clientes`

Uma linha por cliente. O e-mail é único.

## `pedidos.cancelado`

Marcado quando o cliente desiste antes do envio.
"""


def regras(doc=DOC):
    return [t for t in extrair_trechos(doc, SCHEMA, "manual.md") if t["tipo"] == "regra"]


def test_lista_numerada_com_titulo_em_negrito_vira_uma_regra_por_item():
    assert [r["titulo"] for r in regras()] == ["Cliente ativo", "Pedido válido", "Ticket médio"]
    assert [r["id"] for r in regras()] == ["rule:cliente_ativo", "rule:pedido_valido", "rule:ticket_medio"]


def test_paragrafo_depois_da_lista_nao_entra_na_ultima_regra():
    doc = "1. **Cliente ativo** = tem pedido recente em `pedidos`.\n\nTexto solto depois da lista.\n"
    assert [r["descricao"] for r in regras(doc)] == ["tem pedido recente em `pedidos`."]


def test_regra_liga_so_nomes_que_existem_no_banco():
    por_titulo = {r["titulo"]: r for r in regras()}
    assert por_titulo["Cliente ativo"]["tabelas_alvo"] == ["pedidos"]
    assert por_titulo["Cliente ativo"]["colunas_alvo"] == ["pedidos.criado_em"]
    assert por_titulo["Pedido válido"]["tabelas_alvo"] == ["pedidos"]
    assert por_titulo["Ticket médio"]["tabelas_alvo"] == []
    inventada = regras("1. **Vendas faturadas** somam `vendas_faturadas` e `pedidos.valor_total` dos `clientes`.\n")
    assert inventada[0]["tabelas_alvo"] == ["clientes"]
    assert inventada[0]["colunas_alvo"] == []


def test_regra_sem_tabela_fica_marcada_para_o_llm():
    por_titulo = {r["titulo"]: r for r in regras()}
    assert por_titulo["Ticket médio"]["precisa_llm"] is True
    assert por_titulo["Cliente ativo"]["precisa_llm"] is False


def test_secao_comum_vira_uma_regra_com_o_titulo_da_secao():
    doc = "## Status permitidos\n\n| coluna | valores |\n|---|---|\n| `clientes.status` | ATIVO, INATIVO |\n"
    trechos = extrair_trechos(doc, SCHEMA, "manual.md")
    assert [(t["tipo"], t["titulo"], t["tabelas_alvo"], t["colunas_alvo"]) for t in trechos] == [
        ("regra", "Status permitidos", ["clientes"], ["clientes.status"])]
    assert "ATIVO, INATIVO" in trechos[0]["descricao"]


def test_secao_de_tabela_com_lista_em_negrito_continua_descricao_da_tabela():
    doc = "### `pedidos`\n\nUma linha por pedido.\n\n- **Volume:** 100 linhas.\n- **Aponta para:** `clientes`.\n"
    trechos = extrair_trechos(doc, SCHEMA, "manual.md")
    assert [(t["tipo"], t["id"]) for t in trechos] == [("tabela", "tbl:pedidos")]
    assert "- **Volume:** 100 linhas." in trechos[0]["descricao"]


def test_trecho_longo_e_quebrado_em_partes_de_ate_500_tokens():
    linhas = [f"- coluna_{i:02d}: guarda o valor número {i} usado nos relatórios da loja" for i in range(80)]
    doc = "## `clientes`\n\n" + "\n".join(linhas) + "\n"
    partes = extrair_trechos(doc, SCHEMA, "manual.md")
    assert len(partes) == 3
    assert all(len(p["descricao"]) <= 2000 for p in partes)
    assert all(p["tipo"] == "tabela" and p["tabelas_alvo"] == ["clientes"] for p in partes)
    assert "\n".join(p["descricao"] for p in partes) == "\n".join(linhas)
    assert len({p["id"] for p in partes}) == 3


def test_titulo_com_nome_de_tabela_ou_coluna_vira_descricao_dela():
    outros = {t["id"]: t for t in extrair_trechos(DOC, SCHEMA, "manual.md") if t["tipo"] != "regra"}
    assert sorted(outros) == ["col:pedidos.cancelado", "tbl:clientes"]
    assert outros["tbl:clientes"]["tipo"] == "tabela"
    assert outros["tbl:clientes"]["descricao"] == "Uma linha por cliente. O e-mail é único."
    assert outros["tbl:clientes"]["tabelas_alvo"] == ["clientes"]
    assert outros["col:pedidos.cancelado"]["tipo"] == "coluna"
    assert outros["col:pedidos.cancelado"]["tabelas_alvo"] == ["pedidos"]
    assert outros["col:pedidos.cancelado"]["colunas_alvo"] == ["pedidos.cancelado"]


SCHEMA_GRAFO_SAMPLE = """=== SCHEMA ===

Tabela: tabela_a
- id_a: INTEGER (PK, NOT NULL)
- id_c: INTEGER
- status_a: TEXT
  Foreign keys:
  - id_c -> tabela_c.id_c

Tabela: tabela_c
- id_c: INTEGER (PK, NOT NULL)
- id_b: INTEGER
  Foreign keys:
  - id_b -> tabela_b.id_b

Tabela: tabela_b
- id_b: INTEGER (PK, NOT NULL)
- categoria_b: TEXT
"""

REGRAS_GRAFO_SAMPLE = """# Regras de Negócio da Loja

1. **Regra A**: Filtra registros ativos usando `tabela_a.status_a` na `tabela_a`.
2. **Regra B**: Agrupa resultados pela coluna `tabela_b.categoria_b` na `tabela_b`.
3. **Regra Ponte**: Regra que menciona `tabela_a` e `tabela_b` ao mesmo tempo mas não deve substituir o JOIN físico.
"""


def test_grafo_heterogeneo_expande_regra_para_tabela_e_steiner_apenas_tabelas():
    """
    Verifica o fluxo:
    - Nós heterogêneos ('tabela' e 'regra') e arestas 'aplica_se' / 'foreign_key'.
    - Quando RAG retorna ['rule:regra_a', 'tabela_a', 'rule:regra_b'], as regras ativam
      tabela_a e tabela_b, e a Steiner Tree conecta tabela_a -> tabela_c -> tabela_b
      passando APENAS por tabelas (ignorando 'rule:regra_ponte').
    """
    from text_to_insight.retriever.graph_logic import SchemaGraph

    trechos = extrair_trechos(REGRAS_GRAFO_SAMPLE, SCHEMA_GRAFO_SAMPLE, "regras.md")
    sg = SchemaGraph(schema=SCHEMA_GRAFO_SAMPLE, schema_info=trechos, steiner_apenas_tabelas=True)

    assert sg.graph.nodes["tabela_a"]["tipo"] == "tabela"
    assert sg.graph.nodes["tabela_b"]["tipo"] == "tabela"
    assert sg.graph.nodes["tabela_c"]["tipo"] == "tabela"
    assert sg.graph.nodes["rule:regra_a"]["tipo"] == "regra"
    assert sg.graph.nodes["rule:regra_b"]["tipo"] == "regra"

    relations, missing = sg._get_relations(["rule:regra_a", "tabela_a", "rule:regra_b"])
    assert missing == []

    nos_nas_relacoes = {r[0] for r in relations} | {r[1] for r in relations}
    assert {"rule:regra_a", "tabela_a", "rule:regra_b", "tabela_b", "tabela_c"}.issubset(nos_nas_relacoes)
    assert "rule:regra_ponte" not in nos_nas_relacoes


def test_grafo_heterogeneo_permite_ponte_por_regra_quando_flag_desativada():
    """Quando steiner_apenas_tabelas=False e não há FK entre duas tabelas, permite ponte via regra."""
    from text_to_insight.retriever.graph_logic import SchemaGraph

    schema_sem_fk = """=== SCHEMA ===

Tabela: tabela_isolada_1
- id: INTEGER (PK)

Tabela: tabela_isolada_2
- id: INTEGER (PK)
"""
    regras_ponte = "1. **Conexao Semantica**: Relaciona `tabela_isolada_1` com `tabela_isolada_2`.\n"
    trechos = extrair_trechos(regras_ponte, schema_sem_fk, "regras.md")

    sg_bloqueado = SchemaGraph(schema=schema_sem_fk, schema_info=trechos, steiner_apenas_tabelas=True)
    rels_bloq, _ = sg_bloqueado._get_relations(["tabela_isolada_1", "tabela_isolada_2"])
    assert rels_bloq == []

    sg_liberado = SchemaGraph(schema=schema_sem_fk, schema_info=trechos, steiner_apenas_tabelas=False)
    rels_lib, _ = sg_liberado._get_relations(["tabela_isolada_1", "tabela_isolada_2"])
    nos_lib = {r[0] for r in rels_lib} | {r[1] for r in rels_lib}
    assert "rule:conexao_semantica" in nos_lib


def test_schema_graph_rag_posiciona_regra_antes_da_tabela_e_usa_cache(tmp_path):
    """
    Verifica que o SchemaGraphRAG + _formatar_contexto_rag:
    1. Lê arquivos .md de docs_dir;
    2. Retorna as regras vinculadas posicionadas imediatamente antes das respectivas tabelas;
    3. Inclui a tabela intermediária (tabela_c) descoberta pela Steiner Tree;
    4. Reutiliza o cache em disco e em memória tanto para o RAG quanto para o Grafo.
    """
    from text_to_insight.retriever.engine import SchemaGraphRAG
    from text_to_insight.nodes.retriever import _formatar_contexto_rag

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "regras.md").write_text(REGRAS_GRAFO_SAMPLE, encoding="utf-8")
    cache_dir = tmp_path / "cache"

    rag1 = SchemaGraphRAG(
        schema={"contexto_schema": SCHEMA_GRAFO_SAMPLE},
        docs_dir=docs_dir,
        cache_dir=cache_dir,
    )
    rag2 = SchemaGraphRAG(
        schema={"contexto_schema": SCHEMA_GRAFO_SAMPLE},
        docs_dir=docs_dir,
        cache_dir=cache_dir,
    )
    assert rag1.schema_graph is rag2.schema_graph
    assert rag1.rag is rag2.rag
    assert len(list((cache_dir / "rules").glob("*_trechos.json"))) == 1

    retrieved, relations = rag1.retrieve("status_a na tabela_a e categoria_b na tabela_b", top_k=3)
    ids_retornados = retrieved["ids"][0]

    assert "rule:regra_a" in ids_retornados
    assert "tabela_a" in ids_retornados
    assert "rule:regra_b" in ids_retornados
    assert "tabela_b" in ids_retornados
    assert "tabela_c" in ids_retornados
    assert ids_retornados.index("rule:regra_a") < ids_retornados.index("tabela_a")
    assert ids_retornados.index("rule:regra_b") < ids_retornados.index("tabela_b")

    prompt_formatado = _formatar_contexto_rag(retrieved, relations)
    pos_regra_a = prompt_formatado.find("Regra (rule:regra_a -> tabela_a):")
    pos_tabela_a = prompt_formatado.find("Tabela: tabela_a")
    pos_regra_b = prompt_formatado.find("Regra (rule:regra_b -> tabela_b):")
    pos_tabela_b = prompt_formatado.find("Tabela: tabela_b")

    assert 0 <= pos_regra_a < pos_tabela_a
    assert 0 <= pos_regra_b < pos_tabela_b
    assert "Tabela: tabela_c" in prompt_formatado


def test_schema_graph_rag_recupera_regras_das_tabelas_retornadas(tmp_path):
    """
    Verifica a expansão Tabela -> Regra:
    Quando uma tabela é recuperada (mesmo por uma consulta que só cita uma coluna
    do DDL sem termos da regra), todas as regras conectadas a ela no grafo são
    recuperadas e associadas à tabela.
    """
    from text_to_insight.retriever.engine import SchemaGraphRAG

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "regras.md").write_text(
        "1. **Filtro Obrigatorio**: Ignore registros legados na `tabela_c`.\n",
        encoding="utf-8",
    )
    cache_dir = tmp_path / "cache"

    rag = SchemaGraphRAG(
        schema={"contexto_schema": SCHEMA_GRAFO_SAMPLE},
        docs_dir=docs_dir,
        cache_dir=cache_dir,
        use_bm25=True,
        use_embedding=False,
    )

    retrieved, _ = rag.retrieve("id_a id_b", top_k=2)
    assert "tabela_c" in retrieved["tabelas_ordenadas"]
    assert "rule:filtro_obrigatorio" in retrieved["ids"][0]
    assert any(
        r["id"] == "rule:filtro_obrigatorio"
        for r in retrieved["regras_por_tabela"].get("tabela_c", [])
    )


