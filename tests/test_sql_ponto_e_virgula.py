import pytest

from text_to_insight.nodes.code_agent.code_sql import executar_sql_via_url, validar_sql_segura

SQL_DA_RODADA = (
    "SELECT c.name AS conferencia, 'O banco não identifica o lote de cada ingresso; não é possível calcular "
    "ingressos vendidos por lote.' AS observacao FROM conferences_conference c LIMIT 1"
)
LEGITIMOS = [
    SQL_DA_RODADA,
    SQL_DA_RODADA + ";",
    "SELECT 'a; b; c' AS x FROM t;\n",
    "SELECT 'it''s; ok' AS x",
    "SELECT 'it''; SELECT 2; --' AS x",
    "SELECT 'R$ 100; #1 [aprox] `x`' AS v",
    'SELECT "col;una" FROM t',
    'SELECT 1 AS "a"";b"',
    "SELECT a FROM t -- filtro; depois\nWHERE b = 1",
    "SELECT a FROM t --\tobs; x\nWHERE b = 1",
    "SELECT a /* nota; com ponto e vírgula */ FROM t",
    "SELECT a /**/ FROM t /* ; */;",
    "WITH x AS (SELECT 'a;b' AS c) SELECT * FROM x",
]
MULTIPLAS = [
    "SELECT 1; SELECT 2",
    "SELECT 1; DROP TABLE x",
    "SELECT 1;DROP TABLE x;",
    "SELECT 1;;",
    "SELECT 1; -- fim",
    "SELECT 1 /* c */; /* c */ SELECT 2",
    "SELECT 1 -- c\n; SELECT 2",
    "SELECT 'a;b' AS x; SELECT 2",
    'SELECT "a"; SELECT 2',
    "SELECT 'abc; SELECT 2",
    'SELECT "abc; SELECT 2',
    "SELECT 1 /* ; SELECT 2",
    "SELECT 'a\\'; SELECT 2; --'",
    "SELECT 'a\\\\'; SELECT 2; --'",
    "SELECT E'a\\'; SELECT 2; --'",
    "SELECT $$x$$; SELECT 2",
    "SELECT $$ ; SELECT 2 ; $$",
    "SELECT $t$ ' $t$; SELECT 2; --'",
    "SELECT 1 /* /* */ ; SELECT 2; -- */",
    "SELECT 1 /*! ; SELECT 2 */",
    "SELECT 1 /*!50000 ; SELECT 2 */",
    "SELECT 1 /*M! ; SELECT 2 */",
    "SELECT 1 # ; SELECT 2",
    "SELECT 1 #\n; SELECT 2",
    "SELECT 1 --; SELECT 2",
    "SELECT 1 --x; SELECT 2",
    "SELECT 1 -- x\r; SELECT 2",
    "SELECT 1 --\r; SELECT 2",
    "SELECT `a;b` FROM t",
    "SELECT `'` ; SELECT 2 ; -- '",
    "SELECT [a;b] FROM t",
    "SELECT 'a;b'\x00; SELECT 2",
]
BLOQUEADOS = [
    "SELECT 'nota; ok' FROM t INTO OUTFILE '/tmp/x'",
    "SELECT 'a;b', LOAD_FILE('/etc/passwd')",
    "SELECT 'nota; ok' AS x FROM t /* ; */ WHERE pg_read_file('/etc/passwd') IS NULL",
]


@pytest.mark.parametrize("sql", LEGITIMOS)
def test_aceita_ponto_e_virgula_dentro_de_texto_ou_comentario(sql):
    ok, msg = validar_sql_segura(sql)
    assert ok is True, f"deveria permitir: {sql!r} ({msg})"


@pytest.mark.parametrize("sql", MULTIPLAS)
def test_rejeita_mais_de_uma_consulta_mesmo_disfarcada(sql):
    ok, msg = validar_sql_segura(sql)
    assert ok is False, f"deveria bloquear: {sql!r}"
    assert "Multiplas statements" in msg


@pytest.mark.parametrize("sql", BLOQUEADOS)
def test_ponto_e_virgula_em_texto_nao_desliga_a_lista_de_bloqueio(sql):
    ok, msg = validar_sql_segura(sql)
    assert ok is False, f"deveria bloquear: {sql!r}"
    assert "bloqueado" in msg


def test_consulta_com_ponto_e_virgula_em_texto_roda_de_ponta_a_ponta():
    resultado = executar_sql_via_url("sqlite://", "SELECT 'Receita fora deste banco; consultar o Stripe' AS nota;")
    assert resultado["ok"] is True, resultado["erro_execucao"]
    assert resultado["linhas_resultado_completo"] == [{"nota": "Receita fora deste banco; consultar o Stripe"}]
