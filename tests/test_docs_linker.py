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
