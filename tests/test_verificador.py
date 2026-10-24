"""
Testes do nó verificador (depois da SQL). Nenhum teste chama a API.
"""
import sqlite3

import pytest

from text_to_insight.nodes import verificador as ver


@pytest.fixture
def banco(tmp_path):
    caminho = tmp_path / "lab.sqlite"
    c = sqlite3.connect(caminho)
    c.execute('CREATE TABLE "Laboratory" (ID INTEGER, CENTROMEA TEXT, "SSB" TEXT)')
    c.executemany('INSERT INTO "Laboratory" VALUES (?, ?, ?)', [(1, "0", "negative"), (2, "negative", "0"), (3, None, "8")])
    c.execute('CREATE TABLE "Patient" (ID INTEGER, SEX TEXT, "First Name" TEXT)')
    c.executemany('INSERT INTO "Patient" VALUES (?, ?, ?)', [(1, "M", "Ana"), (2, "F", "Bia")])
    c.commit(); c.close()
    return str(caminho)


def _regras(alertas):
    return [a["regra"] for a in alertas]


def test_resultado_vazio_e_zero():
    assert _regras(ver.checar("How many?", "SELECT 1", [])) == ["resultado_vazio"]
    assert _regras(ver.checar("How many patients?", "SELECT COUNT(*) AS n FROM t", [{"n": 0}])) == ["resultado_zero"]
    assert ver.checar("How many patients?", "SELECT COUNT(*) AS n FROM t", [{"n": 3}]) == []


def test_pede_um_numero_e_devolve_varias_colunas():
    linhas = [{"dc": 3, "marvel": 5, "diff": -2}]
    assert "numero_com_varias_colunas" in _regras(ver.checar("What is the difference in the number of heroes", "SELECT 1", linhas))


def test_colunas_nao_pedidas_mas_respeita_pedidos_multiplos():
    linhas = [{"superhero_name": "Jean", "gender": "Female"}]
    assert _regras(ver.checar("Identify the gender of the superhero.", "SELECT a, b FROM t", linhas)) == ["colunas_nao_pedidas"]
    # a pergunta pede duas coisas (Q522 do BIRD: o pedido está na segunda frase)
    q522 = "Which cards are ranked 1st on EDHRec? List all of the cards name and its banned play format."
    assert ver.checar(q522, "SELECT name, format FROM t", [{"name": "x", "format": "y"}]) == []
    # duas perguntas na mesma frase (Q173)
    assert ver.checar("How often? What was the aim?", "SELECT a, b FROM t", [{"a": 1, "b": 2}]) == []


def test_coluna_de_apoio():
    sql = "SELECT player_name, birthday FROM Player WHERE player_name IN ('A', 'B') ORDER BY birthday LIMIT 1"
    regras = _regras(ver.checar("Which player is older?", sql, [{"player_name": "A", "birthday": "1990"}]))
    assert "coluna_de_apoio" in regras


def test_valor_inexistente_no_banco(banco):
    estado = {"db_path": banco}
    conn, fechar = ver._abrir_sqlite(estado)
    try:
        existe = ver._checador_de_valores(conn)
        sql = "SELECT COUNT(DISTINCT p.ID) FROM Patient p JOIN Laboratory l ON l.ID = p.ID WHERE p.SEX = 'M' AND l.CENTROMEA IN ('-', '+-')"
        alertas = ver.checar("How many of them are male?", sql, [{"n": 0}], existe)
        assert "valor_inexistente" in _regras(alertas)
        assert "CENTROMEA = '-'" in next(a["mensagem"] for a in alertas if a["regra"] == "valor_inexistente")
        # valor que existe não acusa; coluna desconhecida não acusa
        assert existe("SEX", "M", False) is True
        assert existe("coluna_que_nao_existe", "x", False) is None
        assert existe("CENTROMEA", "neg%", True) is True
    finally:
        fechar()


def test_valor_com_aspas_e_nome_estranho_nao_quebram(banco):
    conn, fechar = ver._abrir_sqlite({"db_path": banco})
    try:
        existe = ver._checador_de_valores(conn)
        # o valor vai como parâmetro: aspas e tentativa de injeção não executam nada
        assert existe("SEX", "M' OR '1'='1", False) is False
        assert existe("SEX", "x'); DROP TABLE Patient; --", False) is False
        assert existe("First Name", "Ana", False) is True
        assert conn.execute("SELECT COUNT(*) FROM Patient").fetchone()[0] == 2
    finally:
        fechar()


def test_no_so_roda_em_execucao_ok_e_fecha_sem_banco():
    assert ver.nos_nodo_verificador({"status": "exec_erro"}) == {"alertas_verificador": [], "verificacao": {}}
    assert ver.nos_nodo_verificador({"status": "exec_ok"}, modo="off") == {"alertas_verificador": [], "verificacao": {}}
    # sem db_path: roda as regras que não precisam do banco
    out = ver.nos_nodo_verificador({"status": "exec_ok", "pergunta_atual": "How many?", "sql_gerada": "SELECT 1", "linhas_resultado_completo": []})
    assert _regras(out["alertas_verificador"]) == ["resultado_vazio"]


def test_no_com_banco(banco):
    estado = {
        "status": "exec_ok", "db_path": banco, "pergunta_atual": "How many of them are male?",
        "sql_gerada": "SELECT COUNT(*) FROM Laboratory WHERE CENTROMEA IN ('-', '+-')",
        "linhas_resultado_completo": [{"n": 0}],
    }
    regras = _regras(ver.nos_nodo_verificador(estado)["alertas_verificador"])
    assert regras == ["resultado_zero", "valor_inexistente"]


def test_falha_no_banco_nao_derruba(monkeypatch):
    def quebra(*a, **k):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(ver, "_abrir_sqlite", quebra)
    out = ver.nos_nodo_verificador({"status": "exec_ok", "pergunta_atual": "How many?", "sql_gerada": "SELECT 1", "linhas_resultado_completo": [{"n": 2}]})
    assert out["alertas_verificador"] == []
    assert out["verificacao"]["alerta"] is False


def test_sql_invalida_nao_derruba():
    assert ver.checar("How many?", "SELEC lixo ((", [{"n": 2}]) == []


def test_aviso_na_resposta():
    from text_to_insight.nodes.response import nos_nodo_resposta
    alertas = [{"regra": "resultado_zero", "mensagem": "o resultado deu zero ou vazio (NULL)"}]
    estado = {"status": "exec_ok", "pergunta_atual": "How many?", "sql_gerada": "SELECT 0",
              "linhas_resultado_preview": [{"n": 0}], "total_linhas_resultado": 1, "alertas_verificador": alertas}
    texto = nos_nodo_resposta(estado, llm=None)["resposta_natural"]
    assert texto.endswith("Atenção: esta resposta pode estar errada, porque o resultado deu zero ou vazio (NULL).")
    sem = nos_nodo_resposta(estado | {"alertas_verificador": []}, llm=None)["resposta_natural"]
    assert "Atenção" not in sem


@pytest.mark.parametrize("modo", ["off", "avisar"])
@pytest.mark.parametrize("graficos", [True, False])
def test_grafo_inclui_o_verificador_so_quando_ligado(modo, graficos):
    from text_to_insight.graph import Graph

    g = Graph("chave-falsa", "openai/gpt-4o-mini", hitl=False, enable_graphs=graficos, verificador=modo)
    desenho = g.grafo_text_to_insight.get_graph()
    nos = desenho.nodes
    assert ("verificador" in nos) is (modo != "off")
    arestas = {(a.source, a.target) for a in desenho.edges}
    destino = "salvar_csv" if graficos else "resposta"
    if modo == "off":
        assert ("sandbox", destino) in arestas
    else:
        assert ("sandbox", "verificador") in arestas and ("verificador", destino) in arestas
        assert ("sandbox", "resposta") not in arestas and ("sandbox", "salvar_csv") not in arestas


def test_colunas_com_nome_repetido_contam_pelo_select():
    # Q206 do BIRD: duas colunas "element"; no dict do resultado elas viram uma só
    sql = "SELECT a.element, b.element FROM connected c JOIN atom a ON a.atom_id = c.atom_id JOIN atom b ON b.atom_id = c.atom_id2"
    regras = _regras(ver.checar("What elements are in the TR004_8_9 bond atoms?", sql, [{"element": "cl"}]))
    assert regras == ["colunas_nao_pedidas"]
    assert ver.checar("How many?", "SELECT * FROM t", [{"n": 2}]) == []


# ---------------------------------------------------------------------------
# Níveis (regras + Jev). O Jev é simulado: nenhum teste chama a API.
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, dados):
        self._dados = dados

    def raise_for_status(self):
        pass

    def json(self):
        return self._dados


def _respostas_jev(probs, probs_inv=None):
    """Probabilidades da escolha nas duas ordens (se probs_inv não vier, as duas iguais)."""
    def escolha(p):
        return {"type": "choice", "choice": max(p, key=p.get), "confidence": 0.5, "probabilities": p}
    return {"answers": {"grupo": escolha(probs), "grupo_inv": escolha(probs_inv or probs)}}


def _estado_ok(**extra):
    base = {"status": "exec_ok", "pergunta_atual": "Which school has the lowest rate?\nHint: rate = a / b",
            "sql_gerada": "SELECT School FROM schools ORDER BY a / b LIMIT 1", "linhas_resultado_completo": [{"School": "X"}],
            "contexto_schema": "Tabela: schools\n- School: TEXT\n- a: INTEGER\n- b: INTEGER\n"}
    return base | extra


def test_niveis_so_pelas_regras():
    v = ver.combinar([], None)
    assert v == {"alerta": False, "nivel": "nenhum", "nivel_jev": "", "probs_jev": {}, "niveis_regras": [], "origem": ""}
    alertas = [{"regra": "resultado_zero", "mensagem": "zero"}, {"regra": "valor_inexistente", "mensagem": "valor"}]
    v = ver.combinar(alertas, None)
    assert v["alerta"] is True and v["niveis_regras"] == ["alta", "media"] and v["origem"] == "regras"
    assert v["nivel"] == "alta"  # sem Jev, o nível vem da regra mais forte (plano B)


def test_jev_principal_e_regras_em_segundo_plano():
    nenhum = _respostas_jev({"alta": 0.05, "media": 0.05, "baixa": 0.1, "nenhum": 0.8})["answers"]
    baixa = _respostas_jev({"alta": 0.02, "media": 0.03, "baixa": 0.8, "nenhum": 0.15})["answers"]
    assert ver.combinar([], nenhum)["alerta"] is False
    v = ver.combinar([], baixa)
    assert v["alerta"] is True and v["nivel"] == "baixa" and v["origem"] == "jev"
    # o Jev não viu nada, mas a regra viu: o alerta continua (as regras completam o Jev)
    v = ver.combinar([{"regra": "resultado_vazio", "mensagem": "vazio"}], nenhum)
    assert v["alerta"] is True and v["origem"] == "regras" and v["nivel"] == "alta"
    v = ver.combinar([{"regra": "colunas_nao_pedidas", "mensagem": "colunas"}], baixa)
    assert v["origem"] == "jev e regras" and v["niveis_regras"] == ["baixa"]
    # quando os dois apontam níveis diferentes, o nível principal é o do Jev
    v = ver.combinar([{"regra": "resultado_vazio", "mensagem": "vazio"}], baixa)
    assert v["nivel"] == "baixa" and v["niveis_regras"] == ["alta"]


def test_media_das_duas_ordens_corrige_o_vies_da_primeira_opcao():
    from text_to_insight.nodes.jev_cliente import media_das_duas_ordens
    r = {"u": {"probabilities": {"alta": 0.8, "media": 0.1, "baixa": 0.1}},
         "u_inv": {"probabilities": {"alta": 0.2, "media": 0.1, "baixa": 0.7}}}
    probs = media_das_duas_ordens(r, "u", ["alta", "media", "baixa"])
    assert probs == pytest.approx({"alta": 0.5, "media": 0.1, "baixa": 0.4})


def test_no_com_jev_manda_o_estado_certo_sem_alertas_e_a_chave_so_no_cabecalho(monkeypatch):
    import httpx
    monkeypatch.setenv("TYPESAFE_API_KEY", "chave-de-teste")
    enviado = {}

    def falso_post(url, json=None, headers=None, timeout=None):
        enviado.update(json=json, headers=headers)
        return _Resp(_respostas_jev({"alta": 0.1, "media": 0.1, "baixa": 0.7, "nenhum": 0.1}))

    monkeypatch.setattr(httpx, "post", falso_post)
    out = ver.nos_nodo_verificador(_estado_ok(), usar_jev=True)
    estado_jev = enviado["json"]["state"]
    assert estado_jev["question"] == "Which school has the lowest rate?" and estado_jev["hint"] == "rate = a / b"
    assert estado_jev["result"].startswith("1 row(s); columns: School")
    assert "schools: School, a, b" in estado_jev["schema"]
    assert "alerts" not in estado_jev  # o Jev julga sem ver o que as regras acharam
    assert set(enviado["json"]["questions"]) == {"grupo", "grupo_inv"}
    assert "chave-de-teste" not in str(enviado["json"]) and enviado["headers"]["Authorization"] == "Bearer chave-de-teste"
    assert out["verificacao"]["alerta"] is True and out["verificacao"]["nivel_jev"] == "baixa"


def test_jev_fora_do_ar_ou_sem_chave_segue_pelas_regras(monkeypatch):
    import httpx
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("sem chave não deveria chamar o Jev"))
    assert ver.nos_nodo_verificador(_estado_ok(), usar_jev=True)["verificacao"]["alerta"] is False

    monkeypatch.setenv("TYPESAFE_API_KEY", "chave-de-teste")

    def falha(*a, **k):
        raise httpx.ConnectTimeout("sem rede")

    monkeypatch.setattr(httpx, "post", falha)
    out = ver.nos_nodo_verificador(_estado_ok(linhas_resultado_completo=[]), usar_jev=True)
    assert out["verificacao"]["alerta"] is True and out["verificacao"]["probs_jev"] == {}
    assert out["verificacao"]["niveis_regras"] == ["alta"]

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp({"answers": {"grupo": {}}}))
    assert ver.nos_nodo_verificador(_estado_ok(), usar_jev=True)["verificacao"]["alerta"] is False


def test_aviso_comeca_pelo_jev_e_regras_viram_detalhe():
    verif = {"alerta": True, "nivel": "baixa", "niveis_regras": [], "nivel_jev": "baixa",
             "probs_jev": {"alta": 0.02, "media": 0.03, "baixa": 0.81, "nenhum": 0.14}, "origem": "jev"}
    aviso = ver.texto_do_aviso([], verif)
    assert aviso.startswith("\n\nAtenção: esta resposta pode estar errada. A checagem automática indica que")
    assert "colunas que a pergunta não pediu" in aviso and "(81%)" in aviso and "Detalhes" not in aviso
    alertas = [{"regra": "colunas_nao_pedidas", "mensagem": "a resposta tem colunas que a pergunta não pediu (a, b)"}]
    aviso = ver.texto_do_aviso(alertas, verif | {"niveis_regras": ["baixa"], "origem": "jev e regras"})
    assert aviso.index("A checagem automática") < aviso.index("Detalhes: a resposta tem colunas que a pergunta não pediu (a, b)")
    # sem Jev (plano B), o aviso vem só das regras
    aviso = ver.texto_do_aviso(alertas, {"alerta": True, "nivel": "baixa", "nivel_jev": "", "niveis_regras": ["baixa"]})
    assert aviso == "\n\nAtenção: esta resposta pode estar errada, porque a resposta tem colunas que a pergunta não pediu (a, b)."
    assert ver.texto_do_aviso([], {"alerta": False, "nivel_jev": "nenhum"}) == ""


def test_jev_vem_ligado_por_padrao_quando_o_verificador_liga():
    from text_to_insight.graph import Graph
    g = Graph("chave-falsa", "openai/gpt-4o-mini", hitl=False, enable_graphs=False, verificador="avisar")
    assert g.grafo_text_to_insight.builder.nodes["verificador"].runnable.func.keywords["usar_jev"] is True
    g = Graph("chave-falsa", "openai/gpt-4o-mini", hitl=False, enable_graphs=False, verificador="avisar", verificador_jev=False)
    assert g.grafo_text_to_insight.builder.nodes["verificador"].runnable.func.keywords["usar_jev"] is False


def test_grafo_repassa_o_jev_ao_verificador():
    from text_to_insight.graph import Graph
    g = Graph("chave-falsa", "openai/gpt-4o-mini", hitl=False, enable_graphs=False, verificador="avisar", verificador_jev=True)
    no = g.grafo_text_to_insight.builder.nodes["verificador"].runnable
    assert no.func.keywords["usar_jev"] is True and no.func.keywords["modo"] == "avisar"


def test_dica_colada_na_pergunta_nao_esconde_coluna_a_mais():
    pergunta = "Identify the gender of the superhero.\nHint: Phoenix Force refers to power_name = 'Phoenix Force', and gender to gender"
    regras = _regras(ver.checar(pergunta, "SELECT s.name, g.gender FROM s JOIN g ON 1=1", [{"name": "Jean", "gender": "F"}]))
    assert regras == ["colunas_nao_pedidas"]


# ---------------------------------------------------------------------------
# Colunas juntadas (regra firme) e filtros não pedidos (dúvida).
# ---------------------------------------------------------------------------

def test_colunas_juntadas_no_select_final():
    # Q1375 do BIRD: nome e sobrenome numa coluna só
    alertas = ver.checar("List all the members.", "SELECT first_name || ' ' || last_name AS full_name FROM member", [{"full_name": "a b"}])
    assert "colunas_juntadas" in _regras(alertas)
    assert "colunas_juntadas" in _regras(ver.checar("Names?", "SELECT CONCAT(a, b) FROM t", [{"x": "ab"}]))
    # concatenar só no filtro não conta
    assert ver.checar("Who?", "SELECT name FROM t WHERE first || last = 'ab'", [{"name": "x"}]) == []


Q37 = ("What is the complete address of the school with the lowest excellence rate? Indicate the Street, City, Zip and State."
       "\nHint: Execellence Rate = NumGE1500 / NumTstTakr; complete address has Street, City, State, Zip code")


def test_filtros_que_a_pergunta_nao_pediu():
    sql = ("SELECT sch.Street FROM satscores AS sat JOIN schools AS sch ON sch.CDSCode = sat.cds "
           "WHERE sat.rtype = 'S' AND sat.NumTstTakr > 0 AND sat.NumGE1500 IS NOT NULL ORDER BY 1 LIMIT 1")
    assert set(ver.checar_duvidas(Q37, sql)) == {"sat.rtype = 'S'", "sat.NumTstTakr > 0", "sat.NumGE1500 IS NOT NULL"}


def test_filtro_citado_pelo_valor_ou_pela_coluna_nao_e_duvida():
    # Q1514: a data e a hora estão na pergunta/dica, só os nomes das colunas não
    q = "What kind of currency did the customer paid at 16:25:00 in 2012/8/24?\nHint: '2012/8/24' can be represented by '2012-08-24';"
    assert ver.checar_duvidas(q, "SELECT c.Currency FROM t WHERE t.Date = '2012-08-24' AND t.Time = '16:25:00'") == []
    # Q1078: o valor (nome do jogador) está na pergunta
    q = "Which player is older, Aaron Lennon or Abdelaziz Barrada?"
    assert ver.checar_duvidas(q, "SELECT player_name FROM Player WHERE player_name IN ('Aaron Lennon', 'Abdelaziz Barrada')") == []
    # Q1323: "not fundraisers" cobre type <> 'Fundraiser'
    q = "List all the names of events that had an attendance of over 20 students but were not fundraisers."
    assert ver.checar_duvidas(q, "SELECT event_name FROM event e WHERE e.type <> 'Fundraiser'") == []
    # a coluna está na pergunta (Gender ~ gender) e o valor curto 'F' não é acusado só por existir a letra f no texto
    assert ver.checar_duvidas("How many female clients?\nHint: female refers to gender = 'F'", "SELECT COUNT(*) FROM client WHERE gender = 'F'") == []


def test_valor_curto_so_conta_como_palavra_inteira():
    # 'S' não pode ser "citado" só porque a letra s aparece em "schools"
    assert ver.checar_duvidas("How many schools are there?", "SELECT COUNT(*) FROM satscores WHERE rtype = 'S'") == ["rtype = 'S'"]


def test_is_not_null_pedido_na_pergunta_nao_e_duvida():
    q = "How many schools have a non-null phone number?"
    assert ver.checar_duvidas(q, "SELECT COUNT(*) FROM schools WHERE Phone IS NOT NULL") == []


def test_duvida_nao_liga_o_alerta_firme_e_tem_aviso_proprio():
    estado = {"status": "exec_ok", "pergunta_atual": Q37,
              "sql_gerada": "SELECT Street FROM satscores sat JOIN schools sch ON sch.CDSCode = sat.cds WHERE sat.rtype = 'S' ORDER BY 1 LIMIT 1",
              "linhas_resultado_completo": [{"Street": "Rua X"}]}
    out = ver.nos_nodo_verificador(estado, usar_jev=False)
    assert out["verificacao"]["alerta"] is False
    assert out["verificacao"]["duvidas"] == ["sat.rtype = 'S'"]
    aviso = ver.texto_do_aviso(out["alertas_verificador"], out["verificacao"])
    assert aviso == "\n\nConfira se é isso que você quer: a consulta acrescentou filtros que a pergunta não pediu: sat.rtype = 'S'."
    assert "pode estar errada" not in aviso


def test_aviso_de_erro_vem_antes_da_duvida():
    verif = {"alerta": True, "nivel": "alta", "nivel_jev": "", "niveis_regras": ["alta"], "duvidas": ["x > 0"]}
    aviso = ver.texto_do_aviso([{"regra": "resultado_vazio", "mensagem": "a consulta não devolveu nenhuma linha"}], verif)
    assert aviso.index("pode estar errada") < aviso.index("Confira se é isso que você quer")
