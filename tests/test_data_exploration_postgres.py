"""
Data exploration em PostgreSQL.

Os testes que precisam de um banco real usam a fixture `pg_engine` e são
pulados se TEXT_TO_INSIGHT_TEST_PG_URL não estiver definida (a CI sobe um
serviço PostgreSQL e define a variável).
"""

import pytest
from sqlalchemy import text

import text_to_insight.nodes.data_exploration as de
from text_to_insight.nodes.data_exploration import (
    _classify_column_type,
    _extract_table_names_from_rag_context,
    _q,
    nos_nodo_data_exploration,
)


def _rag(*tabelas: str) -> str:
    return "".join(f"Tabela: {t}\n- coluna: TIPO\n\n" for t in tabelas)


def _explorar(engine, tabela, colunas=None):
    """explore_table numa transação de exploração, como o nó faz."""
    conn = engine.raw_connection()
    try:
        de._preparar_sessao_postgres(conn)
        return de.explore_table(conn, tabela, colunas, dialeto="postgresql")
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def pg_banco(pg_engine):
    """Tabelas de exemplo com tipos nativos do PostgreSQL."""
    with pg_engine.begin() as conn:
        conn.execute(text("CREATE TYPE humor AS ENUM ('feliz', 'triste', 'neutro')"))
        conn.execute(text("""
            CREATE TABLE produtos (
                id serial PRIMARY KEY,
                preco numeric(10, 2),
                categoria text,
                ativo boolean,
                criado_em timestamptz,
                prazo interval,
                humor humor,
                tags text[],
                extra json,
                "Nome Exibido" varchar(40)
            )
        """))
        conn.execute(text("""
            INSERT INTO produtos (preco, categoria, ativo, criado_em, prazo, humor, tags, extra, "Nome Exibido")
            SELECT g * 1.5,
                   CASE WHEN g % 10 = 0 THEN NULL ELSE 'cat_' || (g % 3) END,
                   g % 4 = 0,
                   TIMESTAMPTZ '2024-01-01 00:00:00+00' + make_interval(days => g),
                   make_interval(hours => g % 48),
                   (ARRAY['feliz', 'triste', 'neutro'])[1 + g % 3]::humor,
                   ARRAY['t' || (g % 2)],
                   json_build_object('g', g),
                   'Produto ' || g
            FROM generate_series(1, 200) g
        """))
        conn.execute(text('CREATE TABLE "Tabela Com Espaço" ("Coluna ""Aspas""" int)'))
        conn.execute(text('INSERT INTO "Tabela Com Espaço" VALUES (1), (2), (NULL)'))
    return pg_engine


# ---------------------------------------------------------------------------
# Sem banco
# ---------------------------------------------------------------------------

class TestHelpersSemBanco:
    def test_nomes_de_tabela_com_espaco(self):
        contexto = "Tabela: orders\n- id: INT\n\nTabela: Tabela Com Espaço  \n- x: INT\n"
        assert _extract_table_names_from_rag_context(contexto) == ["orders", "Tabela Com Espaço"]

    def test_identificador_escapa_aspas(self):
        assert _q('Coluna "Aspas"') == '"Coluna ""Aspas"""'

    @pytest.mark.parametrize("tipo, categoria", [
        ("interval", "date"),
        ("timestamp with time zone", "date"),
        ("double precision", "numeric"),
        ("numeric", "numeric"),
        ("character varying", "categorical"),
        ("USER-DEFINED", "categorical"),
        ("ARRAY", "categorical"),
        ("boolean", "boolean"),
    ])
    def test_classifica_tipos_do_postgres(self, tipo, categoria):
        assert _classify_column_type(tipo) == categoria


# ---------------------------------------------------------------------------
# Com PostgreSQL real
# ---------------------------------------------------------------------------

class TestExploracaoPostgres:
    def test_estatisticas_por_tipo(self, pg_banco):
        resultado = _explorar(pg_banco, "produtos")
        colunas = resultado.columns

        assert resultado.row_count_estimate == 200
        preco = colunas["preco"]
        assert preco.dtype == "numeric"
        assert (preco.min, preco.max) == (1.5, 300.0)
        assert preco.min <= preco.median <= preco.max
        assert colunas["categoria"].null_rate == 0.1
        assert colunas["categoria"].cardinality == 3
        assert (colunas["ativo"].true_count, colunas["ativo"].false_count) == (50, 150)
        assert colunas["criado_em"].dtype == "date"
        # o texto depende do fuso da sessão; só a ordem é garantida
        assert colunas["criado_em"].min < colunas["criado_em"].max
        assert len(colunas["criado_em"].sample_values) == 3
        assert colunas["prazo"].dtype == "date"
        assert colunas["humor"].cardinality == 3
        assert colunas["tags"].cardinality == 2
        assert colunas["Nome Exibido"].cardinality == 200

    def test_no_injeta_estatisticas_no_prompt(self, pg_banco):
        r = nos_nodo_data_exploration({"contexto_rag_schema": _rag("produtos")}, engine=pg_banco)

        texto = r["contexto_data_exploration"]
        assert "Tabela: produtos (~200 linhas)" in texto
        assert "preco [numeric]" in texto and "ativo [boolean]" in texto

    def test_coluna_sem_suporte_e_pulada_sem_derrubar_as_outras(self, pg_banco):
        # GROUP BY em json falha ("could not identify an equality operator");
        # sem o rollback, todas as consultas seguintes falhariam também.
        r = nos_nodo_data_exploration({"contexto_rag_schema": _rag("produtos")}, engine=pg_banco)
        colunas = r["cache_data_exploration"]["produtos"]["colunas"]

        assert "extra" not in colunas
        assert "Nome Exibido" in colunas  # vem depois de `extra` na tabela

    def test_tabela_inexistente_nao_derruba_as_outras(self, pg_banco):
        r = nos_nodo_data_exploration({"contexto_rag_schema": _rag("nao_existe", "produtos")}, engine=pg_banco)

        assert "Tabela: produtos" in r["contexto_data_exploration"]
        assert "nao_existe" not in r["cache_data_exploration"]

    def test_nomes_com_espaco_e_aspas(self, pg_banco):
        r = nos_nodo_data_exploration({"contexto_rag_schema": _rag("Tabela Com Espaço")}, engine=pg_banco)
        assert 'Coluna "Aspas"' in r["cache_data_exploration"]["Tabela Com Espaço"]["colunas"]

        coluna = _explorar(pg_banco, "Tabela Com Espaço").columns['Coluna "Aspas"']
        assert coluna.null_rate == round(1 / 3, 4)

    def test_estatisticas_estaveis_entre_execucoes(self, pg_banco):
        estado = {"contexto_rag_schema": _rag("produtos"), "colunas_para_explorar": {"produtos": ["preco"]}}
        a = nos_nodo_data_exploration(estado, engine=pg_banco)["cache_data_exploration"]
        b = nos_nodo_data_exploration(estado, engine=pg_banco)["cache_data_exploration"]
        assert a == b

    def test_conexao_volta_limpa_para_o_pool(self, pg_banco):
        with pg_banco.connect() as conn:
            antes = {g: conn.execute(text(f"SHOW {g}")).scalar()
                     for g in ("statement_timeout", "transaction_read_only", "max_parallel_workers_per_gather")}

        nos_nodo_data_exploration({"contexto_rag_schema": _rag("produtos")}, engine=pg_banco)

        with pg_banco.connect() as conn:
            depois = {g: conn.execute(text(f"SHOW {g}")).scalar() for g in antes}
            # e a conexão continua podendo escrever (não ficou presa em read-only)
            conn.execute(text("CREATE TEMP TABLE escrita_ok (a int)"))
            conn.rollback()
        assert depois == antes

    def test_sessao_de_exploracao_e_somente_leitura(self, pg_banco):
        conn = pg_banco.raw_connection()
        try:
            de._preparar_sessao_postgres(conn)
            cur = conn.cursor()
            with pytest.raises(Exception, match="read-only"):
                cur.execute("CREATE TABLE nao_pode (a int)")
        finally:
            conn.rollback()
            conn.close()

    def test_tabela_grande_usa_estimativa_do_catalogo(self, pg_banco, monkeypatch):
        # Depois do ANALYZE o catálogo estima 200 linhas; as 100 inseridas em
        # seguida só aparecem num COUNT(*) exato.
        with pg_banco.begin() as conn:
            conn.execute(text("ANALYZE produtos"))
            conn.execute(text("INSERT INTO produtos (preco) SELECT g FROM generate_series(1, 100) g"))
        estado = {"contexto_rag_schema": _rag("produtos"), "colunas_para_explorar": {"produtos": ["preco"]}}

        monkeypatch.setattr(de, "LIMIAR_ESTIMATIVA_LINHAS", 100)
        estimado = nos_nodo_data_exploration(estado, engine=pg_banco)["cache_data_exploration"]["produtos"]["linhas"]
        monkeypatch.setattr(de, "LIMIAR_ESTIMATIVA_LINHAS", 10_000)
        exato = nos_nodo_data_exploration(estado, engine=pg_banco)["cache_data_exploration"]["produtos"]["linhas"]

        assert (estimado, exato) == (200, 300)


def test_falha_de_conexao_nao_derruba_o_fluxo():
    """Se o banco nem conecta, o nó segue sem estatísticas em vez de levantar exceção."""
    from sqlalchemy import create_engine

    engine = create_engine("postgresql+psycopg2://u:p@127.0.0.1:1/nao_conecta", connect_args={"connect_timeout": 1})
    r = nos_nodo_data_exploration({"contexto_rag_schema": _rag("produtos")}, engine=engine)

    assert r["contexto_data_exploration"] == ""
    assert r["cache_data_exploration"] == {}
