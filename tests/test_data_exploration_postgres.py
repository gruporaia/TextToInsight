"""
Data exploration em PostgreSQL.

Os testes que precisam de um banco real usam a fixture `pg_engine` e são
pulados se TEXT_TO_INSIGHT_TEST_PG_URL não estiver definida (a CI sobe um
serviço PostgreSQL e define a variável).
"""

import os

import pytest
from sqlalchemy import create_engine, text

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
    """Explora uma tabela pelo mesmo caminho do nó (conexão, transação, devolução ao pool)."""
    parcial = {tabela: colunas} if colunas else {}
    return de._explorar_no_banco(engine, "postgresql", [tabela], parcial, {})[tabela]


class _CursorAlterado:
    """Cursor que troca as consultas para as quais `trocar(sql)` devolve outra SQL."""

    def __init__(self, cursor, trocar):
        self._cursor = cursor
        self._trocar = trocar

    def execute(self, sql, *args):
        return self._cursor.execute(self._trocar(sql) or sql, *args)

    def __getattr__(self, nome):
        return getattr(self._cursor, nome)


class _ConexaoAlterada:
    """Conexão do pool cujos cursores passam por `_CursorAlterado`."""

    def __init__(self, conn, trocar):
        self._conn = conn
        self._trocar = trocar

    def cursor(self):
        return _CursorAlterado(self._conn.cursor(), self._trocar)

    def __getattr__(self, nome):
        return getattr(self._conn, nome)


def _engine_pg(**kwargs):
    url = os.environ["TEXT_TO_INSIGHT_TEST_PG_URL"]
    return create_engine(url, **kwargs)


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
        # Todas as colunas, inclusive os valores de exemplo das categóricas e datas.
        estado = {"contexto_rag_schema": _rag("produtos")}
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

    @pytest.mark.parametrize("consulta", ["variancia", "mediana"])
    def test_erro_numa_consulta_opcional_nao_derruba_a_coluna_seguinte(self, pg_banco, consulta):
        # Variância e mediana podem falhar sem perder a coluna; no PostgreSQL o
        # erro aborta a transação, que precisa voltar ao savepoint antes da
        # próxima coluna.
        def trocar(sql):
            if consulta == "variancia" and sql.startswith('SELECT AVG(("preco"'):
                return "SELECT 1/0"
            if consulta == "mediana" and 'ORDER BY "preco"' in sql:
                return "SELECT 1/0"
            return None

        conn = _ConexaoAlterada(pg_banco.raw_connection(), trocar)
        try:
            de._preparar_sessao_postgres(conn)
            colunas = de.explore_table(conn, "produtos", ["preco", "categoria"], dialeto="postgresql").columns
        finally:
            conn.rollback()
            conn.close()

        assert colunas["preco"].mean == 150.75
        assert (colunas["preco"].std_dev is None) == (consulta == "variancia")
        assert (colunas["preco"].median is None) == (consulta == "mediana")
        assert colunas["categoria"].cardinality == 3

    def test_numeric_fora_do_alcance_do_float(self, pg_banco):
        with pg_banco.begin() as conn:
            conn.execute(text("CREATE TABLE enormes (enorme numeric, depois int)"))
            conn.execute(text("INSERT INTO enormes VALUES (1e400, 1), (2e400, 3)"))

        colunas = _explorar(pg_banco, "enormes").columns

        assert colunas["enorme"].mean is None and colunas["enorme"].max is None
        assert (colunas["depois"].mean, colunas["depois"].median) == (2, 2)

    def test_conexao_perdida_mantem_as_tabelas_ja_exploradas(self, pg_banco, monkeypatch):
        with pg_banco.begin() as conn:
            conn.execute(text("CREATE TABLE depois_da_queda (a int)"))

        def trocar(sql):
            if '"Tabela Com Espaço"' in sql:
                return "SELECT pg_terminate_backend(pg_backend_pid())"
            return None

        original = pg_banco.raw_connection
        monkeypatch.setattr(pg_banco, "raw_connection", lambda: _ConexaoAlterada(original(), trocar))
        r = nos_nodo_data_exploration(
            {"contexto_rag_schema": _rag("produtos", "Tabela Com Espaço", "depois_da_queda")}, engine=pg_banco
        )
        monkeypatch.undo()

        assert list(r["cache_data_exploration"]) == ["produtos"]
        # A conexão morta foi descartada pelo pool; a engine continua utilizável.
        with pg_banco.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1

    def test_tabela_nunca_analisada_conta_ate_o_limite(self, pg_banco, monkeypatch):
        # Sem ANALYZE, reltuples é -1: a contagem roda, mas para no limite.
        with pg_banco.begin() as conn:
            conn.execute(text("CREATE TABLE sem_analyze AS SELECT g AS a FROM generate_series(1, 300) g"))
            assert conn.execute(text("SELECT reltuples FROM pg_class WHERE relname = 'sem_analyze'")).scalar() < 0
        monkeypatch.setattr(de, "LIMIAR_ESTIMATIVA_LINHAS", 100)

        assert _explorar(pg_banco, "sem_analyze").row_count_estimate == 100


class TestEngineAutocommit:
    @pytest.fixture
    def engine_autocommit(self, pg_banco):
        from tests.conftest import PG_SCHEMA_TESTES

        engine = _engine_pg(
            isolation_level="AUTOCOMMIT",
            pool_size=1,
            max_overflow=0,
            connect_args={"options": f"-c search_path={PG_SCHEMA_TESTES}"},
        )
        yield engine
        engine.dispose()

    def test_exploracao_tem_transacao_somente_leitura_e_timeout(self, engine_autocommit, monkeypatch):
        vistos = {}
        original = de._STAT_DISPATCHER["numeric"]

        def espiao(cursor, *args, **kwargs):
            for guc in ("transaction_read_only", "statement_timeout"):
                cursor.execute(f"SHOW {guc}")
                vistos[guc] = cursor.fetchone()[0]
            return original(cursor, *args, **kwargs)

        monkeypatch.setitem(de._STAT_DISPATCHER, "numeric", espiao)
        r = nos_nodo_data_exploration(
            {"contexto_rag_schema": _rag("produtos"), "colunas_para_explorar": {"produtos": ["preco"]}},
            engine=engine_autocommit,
        )

        assert "preco" in r["cache_data_exploration"]["produtos"]["colunas"]
        assert vistos == {"transaction_read_only": "on", "statement_timeout": "15s"}

    def test_conexao_volta_ao_pool_em_autocommit(self, engine_autocommit):
        nos_nodo_data_exploration({"contexto_rag_schema": _rag("produtos")}, engine=engine_autocommit)

        with engine_autocommit.connect() as conn:
            assert conn.connection.driver_connection.autocommit is True
            # autocommit de verdade: a escrita vale sem commit explícito
            conn.execute(text("CREATE TABLE criada_em_autocommit (a int)"))
        with engine_autocommit.connect() as conn:
            assert conn.execute(text("SELECT to_regclass('criada_em_autocommit') IS NOT NULL")).scalar()


class TestSearchPath:
    """A exploração enxerga as mesmas tabelas que o SQLAlchemy lista no nó de schema."""

    SEGUNDO = "tti_testes_segundo"

    @pytest.fixture
    def engine_dois_schemas(self, pg_engine):
        from sqlalchemy import inspect

        from tests.conftest import PG_SCHEMA_TESTES

        with pg_engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {self.SEGUNDO} CASCADE"))
            conn.execute(text(f"CREATE SCHEMA {self.SEGUNDO}"))
            conn.execute(text(f"CREATE TABLE {self.SEGUNDO}.so_no_segundo (a int, b text)"))
            conn.execute(text(f"INSERT INTO {self.SEGUNDO}.so_no_segundo SELECT g, 'v' || g FROM generate_series(1, 300) g"))
            conn.execute(text(f"ANALYZE {self.SEGUNDO}.so_no_segundo"))
            # homônimas: vale a do primeiro schema do search_path
            conn.execute(text(f"CREATE TABLE {PG_SCHEMA_TESTES}.homonima (x int)"))
            conn.execute(text(f"INSERT INTO {PG_SCHEMA_TESTES}.homonima VALUES (1)"))
            conn.execute(text(f"CREATE TABLE {self.SEGUNDO}.homonima (y text)"))
            conn.execute(text(f"INSERT INTO {self.SEGUNDO}.homonima VALUES ('a'), ('b')"))
        engine = _engine_pg(connect_args={"options": f"-c search_path={PG_SCHEMA_TESTES},{self.SEGUNDO}"})
        assert {"so_no_segundo", "homonima"} <= set(inspect(engine).get_table_names())
        yield engine
        engine.dispose()
        with pg_engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {self.SEGUNDO} CASCADE"))

    def test_tabela_no_segundo_schema(self, engine_dois_schemas):
        r = nos_nodo_data_exploration({"contexto_rag_schema": _rag("so_no_segundo")}, engine=engine_dois_schemas)
        tabela = r["cache_data_exploration"]["so_no_segundo"]

        assert set(tabela["colunas"]) == {"a", "b"}
        assert tabela["linhas"] == 300

    def test_estimativa_do_catalogo_no_segundo_schema(self, engine_dois_schemas, monkeypatch):
        with engine_dois_schemas.begin() as conn:
            conn.execute(text(f"INSERT INTO {self.SEGUNDO}.so_no_segundo SELECT g, 'w' FROM generate_series(1, 50) g"))
        monkeypatch.setattr(de, "LIMIAR_ESTIMATIVA_LINHAS", 100)

        assert _explorar(engine_dois_schemas, "so_no_segundo").row_count_estimate == 300  # do ANALYZE, não 350

    def test_tabelas_homonimas_usam_a_primeira_do_search_path(self, engine_dois_schemas):
        resultado = _explorar(engine_dois_schemas, "homonima")

        assert list(resultado.columns) == ["x"]
        assert resultado.row_count_estimate == 1


def test_falha_de_conexao_nao_derruba_o_fluxo():
    """Se o banco nem conecta, o nó segue sem estatísticas em vez de levantar exceção."""
    from sqlalchemy import create_engine

    engine = create_engine("postgresql+psycopg2://u:p@127.0.0.1:1/nao_conecta", connect_args={"connect_timeout": 1})
    r = nos_nodo_data_exploration({"contexto_rag_schema": _rag("produtos")}, engine=engine)

    assert r["contexto_data_exploration"] == ""
    assert r["cache_data_exploration"] == {}
