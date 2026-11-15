"""Testes de validação e execução de SQL do módulo code_sql.

Os casos do validador e de regressão em memória não dependem de serviços.
Alguns testes de execução usam o banco Olist ou a fixture PostgreSQL.
"""

import pytest
from sqlalchemy import create_engine

from text_to_insight.nodes.code_agent.code_sql import (
    executar_sql_via_engine,
    validar_sql_segura,
)

SQL_DA_RODADA = (
    "SELECT c.name AS conferencia, 'O banco não identifica o lote de cada ingresso; não é possível calcular "
    "ingressos vendidos por lote.' AS observacao FROM conferences_conference c LIMIT 1"
)
LEGITIMOS = [
    pytest.param(SQL_DA_RODADA, id="consulta_original_sem_terminador"),
    pytest.param(SQL_DA_RODADA + ";", id="consulta_original_com_terminador"),
    pytest.param(
        "SELECT 'a; b; c' AS x FROM t;\n", id="varios_pontos_e_virgulas_no_texto"
    ),
    pytest.param("SELECT 'it''s; ok' AS x", id="aspa_simples_duplicada"),
    pytest.param(
        "SELECT 'it''; SELECT 2; --' AS x", id="comandos_aparentes_dentro_do_texto"
    ),
    pytest.param(
        "SELECT 'R$ 100; #1 [aprox] `x`' AS v", id="simbolos_especiais_dentro_do_texto"
    ),
    pytest.param('SELECT "col;una" FROM t', id="identificador_com_ponto_e_virgula"),
    pytest.param('SELECT 1 AS "a"";b"', id="aspa_dupla_duplicada"),
    pytest.param(
        "SELECT a FROM t -- filtro; depois\nWHERE b = 1",
        id="ponto_e_virgula_no_comentario_de_linha",
    ),
    pytest.param(
        "SELECT a FROM t --\tobs; x\nWHERE b = 1", id="tab_apos_tracos_do_comentario"
    ),
    pytest.param(
        "SELECT a /* nota; com ponto e vírgula */ FROM t",
        id="ponto_e_virgula_no_comentario_de_bloco",
    ),
    pytest.param(
        "SELECT a /**/ FROM t /* ; */;", id="comentarios_de_bloco_e_terminador"
    ),
    pytest.param(
        "WITH x AS (SELECT 'a;b' AS c) SELECT * FROM x",
        id="cte_com_ponto_e_virgula_no_texto",
    ),
]
MULTIPLOS_COMANDOS = [
    pytest.param("SELECT 1; SELECT 2", id="dois_selects"),
    pytest.param("SELECT 1; DROP TABLE x", id="select_seguido_de_drop"),
    pytest.param("SELECT 1;DROP TABLE x;", id="comandos_sem_espaco_entre_eles"),
    pytest.param(
        "SELECT 1 /* c */; /* c */ SELECT 2", id="comandos_com_comentarios_de_bloco"
    ),
    pytest.param(
        "SELECT 1 -- c\n; SELECT 2", id="segundo_comando_apos_comentario_de_linha"
    ),
    pytest.param(
        "SELECT 'a;b' AS x; SELECT 2", id="texto_com_ponto_e_virgula_e_segundo_comando"
    ),
    pytest.param('SELECT "a"; SELECT 2', id="aspas_duplas_e_segundo_comando"),
]

# Rejeições conservadoras: estas formas não são interpretadas pela varredura.
# Algumas podem conter vários comandos em dialetos específicos.
SINTAXES_NAO_SUPORTADAS = [
    pytest.param("SELECT 1;;", id="terminadores_extras"),
    pytest.param("SELECT 1; -- fim", id="comentario_apos_terminador"),
    pytest.param("SELECT 'abc; SELECT 2", id="aspa_simples_nao_fechada"),
    pytest.param('SELECT "abc; SELECT 2', id="aspa_dupla_nao_fechada"),
    pytest.param("SELECT 1 /* ; SELECT 2", id="comentario_de_bloco_nao_fechado"),
    pytest.param("SELECT 'a\\'; SELECT 2; --'", id="backslash_antes_de_aspa"),
    pytest.param(
        "SELECT 'a\\\\'; SELECT 2; --'", id="backslash_duplicado_antes_de_aspa"
    ),
    pytest.param("SELECT E'a\\'; SELECT 2; --'", id="literal_escape_postgres"),
    pytest.param("SELECT $$x$$; SELECT 2", id="dollar_quoting_com_segundo_comando"),
    pytest.param("SELECT $$ ; SELECT 2 ; $$", id="literal_postgres_dollar_quoted"),
    pytest.param(
        "SELECT $t$ ' $t$; SELECT 2; --'", id="dollar_quoting_com_tag_e_aspas"
    ),
    pytest.param(
        "SELECT 1 /* /* */ ; SELECT 2; -- */", id="comentario_de_bloco_aninhado"
    ),
    pytest.param("SELECT 1 /*! ; SELECT 2 */", id="comentario_executavel_mysql"),
    pytest.param(
        "SELECT 1 /*!50000 ; SELECT 2 */", id="comentario_executavel_mysql_com_versao"
    ),
    pytest.param("SELECT 1 /*M! ; SELECT 2 */", id="comentario_executavel_mariadb"),
    pytest.param("SELECT 1 # ; SELECT 2", id="comentario_hash_com_separador"),
    pytest.param("SELECT 1 #\n; SELECT 2", id="comentario_hash_com_segundo_comando"),
    pytest.param("SELECT 1 --; SELECT 2", id="tracos_sem_espaco_antes_do_separador"),
    pytest.param("SELECT 1 --x; SELECT 2", id="tracos_seguidos_de_letra"),
    pytest.param("SELECT 1 -- x\r; SELECT 2", id="cr_antes_de_segundo_comando"),
    pytest.param("SELECT 1 --\r; SELECT 2", id="cr_apos_tracos"),
    pytest.param("SELECT `a;b` FROM t", id="identificador_com_backticks"),
    pytest.param("SELECT `'` ; SELECT 2 ; -- '", id="backticks_e_aspas_ambiguos"),
    pytest.param("SELECT [a;b] FROM t", id="identificador_com_colchetes"),
    pytest.param("SELECT 'a;b'\x00; SELECT 2", id="nul_na_entrada"),
]
BLOQUEADOS = [
    pytest.param(
        "SELECT 'nota; ok' FROM t INTO OUTFILE '/tmp/x'",
        id="outfile_com_ponto_e_virgula_no_texto",
    ),
    pytest.param(
        "SELECT 'a;b', LOAD_FILE('/etc/passwd')",
        id="load_file_com_ponto_e_virgula_no_texto",
    ),
    pytest.param(
        "SELECT 'nota; ok' AS x FROM t /* ; */ WHERE pg_read_file('/etc/passwd') IS NULL",
        id="pg_read_file_com_ponto_e_virgula_no_comentario",
    ),
]


@pytest.mark.parametrize("sql", LEGITIMOS)
def test_aceita_ponto_e_virgula_dentro_de_texto_ou_comentario(sql):
    ok, msg = validar_sql_segura(sql)
    assert ok is True, f"deveria permitir: {sql!r} ({msg})"


@pytest.mark.parametrize("sql", MULTIPLOS_COMANDOS)
def test_rejeita_multiplos_comandos(sql):
    ok, msg = validar_sql_segura(sql)
    assert ok is False, f"deveria bloquear: {sql!r}"
    assert "Multiplas statements" in msg


@pytest.mark.parametrize("sql", SINTAXES_NAO_SUPORTADAS)
def test_rejeita_sintaxe_nao_suportada(sql):
    ok, msg = validar_sql_segura(sql)
    assert ok is False, f"deveria bloquear: {sql!r}"
    assert "Multiplas statements" in msg


@pytest.mark.parametrize("sql", BLOQUEADOS)
def test_ponto_e_virgula_em_texto_nao_desliga_a_lista_de_bloqueio(sql):
    ok, msg = validar_sql_segura(sql)
    assert ok is False, f"deveria bloquear: {sql!r}"
    assert "bloqueado" in msg


def test_consulta_com_ponto_e_virgula_em_texto_roda_de_ponta_a_ponta():
    sql = "SELECT 'Receita fora deste banco; consultar o Stripe' AS nota;"
    esperado = "Receita fora deste banco; consultar o Stripe"
    engine = create_engine("sqlite://")
    try:
        resultado = executar_sql_via_engine(engine, sql)
        assert resultado["ok"] is True, resultado["erro_execucao"]
        assert resultado["linhas_resultado_completo"] == [{"nota": esperado}]
    finally:
        engine.dispose()


# Validação e execução SQL: cenários básicos e de política de segurança.


def test_sql_valida_select():
    """Aceita SELECT válido."""

    ok, msg = validar_sql_segura("SELECT COUNT(*) FROM orders")
    assert ok is True
    assert msg == ""


def test_sql_valida_cte():
    """Aceita WITH/CTE."""

    ok, msg = validar_sql_segura(
        "WITH totals AS (SELECT id FROM orders) SELECT * FROM totals"
    )
    assert ok is True


def test_sql_rejeita_insert():
    """Rejeita INSERT."""

    ok, msg = validar_sql_segura("INSERT INTO orders VALUES (1, 2, 3)")
    assert ok is False


def test_sql_rejeita_drop():
    """Rejeita DROP."""

    ok, msg = validar_sql_segura("DROP TABLE orders")
    assert ok is False


def test_sql_rejeita_multiplas():
    """Rejeita múltiplas statements."""

    ok, msg = validar_sql_segura("SELECT 1; SELECT 2")
    assert ok is False


def test_sql_rejeita_vazia():
    """Rejeita SQL vazia."""

    ok, msg = validar_sql_segura("")
    assert ok is False


# Variantes de "SELECT ... INTO OUTFILE/DUMPFILE" (escrita de arquivo no servidor
# MySQL). Todas começam com SELECT, então só o blacklist as barra. O MySQL aceita
# comentários entre INTO e OUTFILE, então espaço não pode ser o único separador.
VARIANTES_ESCRITA_DE_ARQUIVO = [
    pytest.param("SELECT * FROM t INTO OUTFILE '/tmp/x'", id="outfile_basico"),
    pytest.param(
        "select * from t into  outfile '/tmp/x'", id="outfile_minusculo_com_espacos"
    ),
    pytest.param("SELECT * FROM t INTO DUMPFILE '/tmp/x'", id="dumpfile_basico"),
    pytest.param(
        "SELECT * FROM t INTO/**/OUTFILE '/tmp/x'",
        id="outfile_com_comentario_sem_espacos",
    ),
    pytest.param(
        "SELECT * FROM t INTO/*qualquer coisa*/DUMPFILE '/tmp/x'",
        id="dumpfile_com_comentario_sem_espacos",
    ),
    pytest.param(
        "SELECT * FROM t INTO /*!50000 OUTFILE*/ '/tmp/x'",
        id="outfile_em_comentario_executavel_mysql",
    ),
    pytest.param(
        "SELECT * FROM t INTO -- x\nOUTFILE '/tmp/x'",
        id="outfile_apos_comentario_de_linha",
    ),
    pytest.param(
        "SELECT * FROM t INTO #x\nOUTFILE '/tmp/x'", id="outfile_apos_comentario_hash"
    ),
    pytest.param(
        "SELECT * FROM t\nINTO\nOUTFILE '/tmp/x'", id="outfile_com_quebras_de_linha"
    ),
]


@pytest.mark.parametrize("sql", VARIANTES_ESCRITA_DE_ARQUIVO)
def test_sql_rejeita_escrita_de_arquivo_mysql(sql):
    """INTO OUTFILE/DUMPFILE é bloqueado qualquer que seja o separador entre as palavras."""

    ok, msg = validar_sql_segura(sql)
    assert ok is False, f"deveria bloquear: {sql!r}"


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("SELECT LOAD_FILE('/etc/passwd')", id="load_file_mysql"),
        pytest.param("SELECT pg_read_file('/etc/passwd')", id="pg_read_file_postgres"),
        pytest.param("SELECT load_extension('/tmp/x.so')", id="load_extension_sqlite"),
    ],
)
def test_sql_rejeita_leitura_de_arquivo_por_funcao(sql):
    """Funções que leem arquivo do servidor também são bloqueadas."""

    ok, msg = validar_sql_segura(sql)
    assert ok is False, f"deveria bloquear: {sql!r}"


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("SELECT * FROM orders", id="select_simples"),
        pytest.param(
            "SELECT nome INTO @variavel FROM clientes LIMIT 1", id="into_variavel_mysql"
        ),
        pytest.param(
            "SELECT COUNT(*) FROM orders /* comentario inocente */",
            id="comentario_de_bloco_inocente",
        ),
        pytest.param("WITH t AS (SELECT 1) SELECT * FROM t", id="cte_simples"),
    ],
)
def test_sql_legitima_continua_permitida(sql):
    """O blacklist não pode gerar falso positivo em consultas normais."""

    ok, msg = validar_sql_segura(sql)
    assert ok is True, f"deveria permitir: {sql!r} ({msg})"


# ============================================================
# SQL Execution (code_sql.py)
# ============================================================


def test_execucao_sql_valida(olist_engine):
    """Executa SELECT real no olist DB e retorna resultados."""

    resultado = executar_sql_via_engine(
        olist_engine, "SELECT COUNT(*) as total FROM orders"
    )
    assert resultado["ok"] is True
    assert resultado["total_linhas_resultado"] == 1
    assert resultado["linhas_resultado_preview"][0]["total"] > 0


def test_execucao_sql_com_limite(olist_engine):
    """Respeita limite_preview."""

    resultado = executar_sql_via_engine(
        olist_engine, "SELECT * FROM orders", limite_preview=5
    )
    assert resultado["ok"] is True
    assert len(resultado["linhas_resultado_preview"]) <= 5


def test_execucao_sql_invalida(olist_engine):
    """Retorna erro para SQL com sintaxe inválida."""

    resultado = executar_sql_via_engine(olist_engine, "SELECT FROM")
    assert resultado["ok"] is False
    assert resultado["erro_execucao"] != ""


# Preservação de literais e isolamento das conexões da engine do chamador.


@pytest.mark.parametrize(
    "sql, esperado",
    [
        pytest.param(
            "SELECT 'Status :ativo' AS v",
            "Status :ativo",
            id="literal_com_nome_aparente_de_parametro",
        ),
        pytest.param("SELECT '12:30' AS v", "12:30", id="literal_com_horario"),
        pytest.param("SELECT 'a % b' AS v", "a % b", id="literal_com_porcento"),
    ],
)
def test_executor_via_engine_preserva_literais_com_dois_pontos(
    sqlite_olist_engine_propria, sql, esperado
):
    """A SQL do agente vai crua ao driver: ':palavra' em literal não vira parâmetro de bind."""

    resultado = executar_sql_via_engine(sqlite_olist_engine_propria, sql)

    assert resultado["ok"], resultado["erro_execucao"]
    assert resultado["linhas_resultado_preview"][0]["v"] == esperado


@pytest.mark.parametrize(
    "sql, esperado",
    [
        pytest.param("SELECT '50%' AS v", "50%", id="literal_com_porcentagem"),
        pytest.param("SELECT 'abc' LIKE '%b%' AS v", True, id="like_com_porcento"),
        pytest.param("SELECT 10 % 3 AS v", 1, id="operador_de_modulo"),
        pytest.param(
            "SELECT '%(x)s' AS v", "%(x)s", id="literal_com_placeholder_aparente"
        ),
        pytest.param(
            "SELECT 'Status :ativo' AS v", "Status :ativo", id="literal_com_dois_pontos"
        ),
    ],
)
def test_executor_postgres_preserva_porcento_e_dois_pontos(pg_engine, sql, esperado):
    """
    psycopg2 trata "%" como marcador de parâmetro: sem no_parameters, LIKE '%b%',
    '50%' e o operador de módulo quebravam no PostgreSQL (no SQLite "%" não é especial).
    """

    resultado = executar_sql_via_engine(pg_engine, sql)

    assert resultado["ok"], resultado["erro_execucao"]
    assert resultado["linhas_resultado_preview"][0]["v"] == esperado


def test_executor_via_engine_nao_deixa_timeout_na_conexao_do_pool(
    sqlite_olist_engine_propria,
):
    """
    A conexão volta para o pool do chamador: o progress handler de timeout
    não pode continuar instalado e abortar queries posteriores do chamador.
    """
    import time
    from sqlalchemy import text

    engine = sqlite_olist_engine_propria
    assert executar_sql_via_engine(engine, "SELECT 1 AS x", timeout_segundos=0.1)["ok"]
    time.sleep(0.3)
    with engine.connect() as conn:
        total = conn.execute(
            text(
                "SELECT COUNT(*) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id"
            )
        ).scalar()

    assert total > 0
