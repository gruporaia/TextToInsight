"""
Testes do nó verificador (depois da SQL). Nenhum teste chama a API.
"""
import sqlite3
import time

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
    out = ver.nos_nodo_verificador(_estado_ok(), usar_jev=True, colunas_extras="erro")
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


# ---------------------------------------------------------------------------
# Colunas a mais: "nota" (uso real) x "erro" (benchmark).
# ---------------------------------------------------------------------------

def test_colunas_a_mais_viram_observacao_no_modo_nota():
    estado = {"status": "exec_ok", "pergunta_atual": "Identify the gender of the superhero.", "sql_gerada": "SELECT a, b FROM t",
              "linhas_resultado_completo": [{"a": 1, "b": 2}]}
    out = ver.nos_nodo_verificador(estado, usar_jev=False, colunas_extras="nota")
    assert out["verificacao"]["alerta"] is False and out["alertas_verificador"] == []
    assert out["verificacao"]["notas"] == ["a resposta tem colunas que a pergunta não pediu (a, b)"]
    aviso = ver.texto_do_aviso(out["alertas_verificador"], out["verificacao"])
    assert aviso == "\n\nObservação: a resposta tem colunas que a pergunta não pediu (a, b)."
    out = ver.nos_nodo_verificador(estado, usar_jev=False, colunas_extras="erro")
    assert out["verificacao"]["alerta"] is True and out["verificacao"]["notas"] == []
    assert "pode estar errada" in ver.texto_do_aviso(out["alertas_verificador"], out["verificacao"])


def test_nivel_baixa_do_jev_nao_liga_o_alerta_no_modo_nota():
    baixa = _respostas_jev({"alta": 0.02, "media": 0.03, "baixa": 0.8, "nenhum": 0.15})["answers"]
    assert ver.combinar([], baixa, "nota")["alerta"] is False
    assert ver.combinar([], baixa, "erro")["alerta"] is True
    # o alerta de outra regra continua e o aviso não fala em colunas
    vazio = [{"regra": "resultado_vazio", "mensagem": "a consulta não devolveu nenhuma linha"}]
    v = ver.combinar(vazio, baixa, "nota")
    assert v["alerta"] is True and v["origem"] == "regras" and v["nivel"] == "alta" and v["nivel_jev"] == "baixa"
    assert ver.texto_do_aviso(vazio, v) == "\n\nAtenção: esta resposta pode estar errada, porque a consulta não devolveu nenhuma linha."


def test_jev_baixa_sem_regra_vira_observacao(monkeypatch):
    baixa = _respostas_jev({"alta": 0.02, "media": 0.03, "baixa": 0.8, "nenhum": 0.15})["answers"]
    monkeypatch.setattr(ver, "_julgar_com_jev", lambda estado: baixa)
    out = ver.nos_nodo_verificador(_estado_ok(), usar_jev=True, colunas_extras="nota")
    assert out["verificacao"]["alerta"] is False
    assert out["verificacao"]["notas"] == ["a resposta pode ter colunas que a pergunta não pediu"]


def test_grafo_repassa_o_modo_das_colunas():
    from text_to_insight.graph import Graph
    g = Graph("chave-falsa", "openai/gpt-4o-mini", hitl=False, enable_graphs=False, verificador="avisar", verificador_colunas="erro")
    assert g.grafo_text_to_insight.builder.nodes["verificador"].runnable.func.keywords["colunas_extras"] == "erro"
    g = Graph("chave-falsa", "openai/gpt-4o-mini", hitl=False, enable_graphs=False, verificador="avisar")
    assert g.grafo_text_to_insight.builder.nodes["verificador"].runnable.func.keywords["colunas_extras"] == "nota"


# ---------------------------------------------------------------------------
# Regras que só olham a pergunta, a SQL e o resultado.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pergunta", ["Quantos ingressos teve cada conferência?", "Quantos materiais por categoria?", "How many tickets for each event?"])
def test_pede_um_numero_nao_dispara_em_pergunta_por_grupo(pergunta):
    linhas = [{"nome": "a", "total": 3}, {"nome": "b", "total": 5}]
    assert "numero_com_varias_colunas" not in _regras(ver.checar(pergunta, "SELECT nome, COUNT(*) AS total FROM t GROUP BY nome", linhas))


def test_coluna_zerada():
    linhas = [{"nome": "a", "presentes": 0}, {"nome": "b", "presentes": 0}]
    alertas = ver.checar("Quem veio em cada sala?", "SELECT nome, presentes FROM t", linhas)
    assert "coluna_zerada" in _regras(alertas)
    assert "presentes" in next(a["mensagem"] for a in alertas if a["regra"] == "coluna_zerada")
    assert "coluna_zerada" in _regras(ver.checar("Quem?", "SELECT a, b FROM t", [{"a": 1, "b": None}, {"a": 2, "b": None}]))
    # uma linha só é a regra do resultado zero; um valor diferente de zero ou booleano não conta
    assert "coluna_zerada" not in _regras(ver.checar("Quem?", "SELECT a FROM t", [{"a": 0}]))
    assert "coluna_zerada" not in _regras(ver.checar("Quem?", "SELECT a, b FROM t", [{"a": 0, "b": 1}, {"a": 1, "b": 2}]))
    assert "coluna_zerada" not in _regras(ver.checar("Quem?", "SELECT a, b FROM t", [{"a": False, "b": 1}, {"a": False, "b": 2}]))


def test_um_grupo_so_exige_group_by_sem_limit():
    sql = "SELECT track, COUNT(*) AS n FROM a GROUP BY track"
    assert "um_grupo_so" in _regras(ver.checar("Quantas candidaturas em cada trilha?", sql, [{"track": "GENERAL", "n": 403}]))
    # mais de um grupo, pergunta sem "cada", ou o "for each" é só método e a resposta é um valor (Spider local007)
    assert "um_grupo_so" not in _regras(ver.checar("Quantas em cada trilha?", sql, [{"track": "A", "n": 1}, {"track": "B", "n": 2}]))
    assert "um_grupo_so" not in _regras(ver.checar("Quantas candidaturas?", sql, [{"track": "A", "n": 1}]))
    assert "um_grupo_so" not in _regras(ver.checar("For each player, average the span.", "SELECT AVG(s) AS m FROM p", [{"m": 4.2}]))
    assert "um_grupo_so" not in _regras(ver.checar("Which state for each year has the most?", sql + " ORDER BY n DESC LIMIT 1", [{"track": "A", "n": 1}]))


def test_duvida_ignora_a_mecanica_da_propria_sql():
    sql = "WITH r AS (SELECT a, ROW_NUMBER() OVER (ORDER BY b) AS rn FROM t) SELECT a FROM r WHERE rn = 1"
    assert ver.checar_duvidas("What is the top a?", sql) == []
    assert ver.checar_duvidas("Who?", "SELECT a FROM t WHERE saldo > (SELECT AVG(saldo) FROM t)") == []
    assert ver.checar_duvidas("Sales in 2020?", "SELECT a FROM t WHERE dia >= '2020-01-01'") == []
    assert ver.checar_duvidas("Sales?", "SELECT a FROM t WHERE dia >= '2020-01-01'") == ["dia >= '2020-01-01'"]


# ---------------------------------------------------------------------------
# Regras que consultam o banco.
# ---------------------------------------------------------------------------

@pytest.fixture
def portal(tmp_path):
    caminho = tmp_path / "portal.sqlite"
    c = sqlite3.connect(caminho)
    c.executescript("""
        CREATE TABLE evento (id INTEGER, nome TEXT, inicio TEXT);
        INSERT INTO evento VALUES (1, 'Antigo', '2020-04-01T08:00:00'), (2, 'Recente', '2024-04-01T08:00:00'), (3, 'Futuro', '2999-04-01T08:00:00');
        CREATE TABLE sessao (id INTEGER, evento_id INTEGER, inicio TEXT);
        INSERT INTO sessao VALUES (1, 2, '2024-04-01T09:00:00'), (2, 3, '2999-04-01T09:00:00');
        CREATE TABLE cargo (id INTEGER, nome TEXT, level TEXT, descricao TEXT);
        INSERT INTO cargo VALUES (1, 'Diretor de TI', 'DIRECTOR', 'x'), (2, 'Head de Marketing', 'DIRECTOR', 'y'), (3, 'Analista', 'ANALYST', 'z');
        CREATE TABLE membro (id INTEGER, grupo INTEGER, is_bl_account BOOLEAN, is_international BOOLEAN);
        INSERT INTO membro VALUES (1, 1, 1, 0), (2, 1, 0, 1), (3, 2, 0, 0);
    """)
    c.commit(); c.close()
    return str(caminho)


@pytest.fixture
def sonda(portal):
    conn = sqlite3.connect(f"file:{portal}?mode=ro", uri=True)
    yield ver._Sonda("sqlite", ver._executor_sqlite(conn))
    conn.close()


def test_data_futura_em_cte_e_cte_dentro_de_cte(sonda):
    pergunta = "Quantas pessoas foram ao evento mais recente?"
    direta = "WITH e AS (SELECT id FROM evento ORDER BY inicio DESC LIMIT 1) SELECT COUNT(*) AS n FROM e"
    alertas = ver.checar(pergunta, direta, [{"n": 1}], sonda=sonda)
    assert "data_futura" in _regras(alertas)
    assert "2999-04-01" in next(a["mensagem"] for a in alertas if a["regra"] == "data_futura")
    # P27 da BRASA: a ordenação usa uma coluna que vem de outra CTE
    encadeada = ("WITH x AS (SELECT evento_id, MAX(inicio) AS ultimo FROM sessao GROUP BY evento_id), "
                 "y AS (SELECT evento_id FROM x ORDER BY ultimo DESC LIMIT 1) SELECT COUNT(*) AS n FROM y")
    assert "data_futura" in _regras(ver.checar(pergunta, encadeada, [{"n": 1}], sonda=sonda))
    # apelido do SELECT e posição no ORDER BY
    assert "data_futura" in _regras(ver.checar(pergunta, "SELECT nome, inicio AS d FROM evento ORDER BY d DESC LIMIT 1", [{"nome": "Futuro", "d": "x"}], sonda=sonda))
    assert "data_futura" in _regras(ver.checar(pergunta, "SELECT nome, inicio FROM evento ORDER BY 2 DESC LIMIT 1", [{"nome": "Futuro", "inicio": "x"}], sonda=sonda))


def test_data_futura_nao_acusa_o_que_esta_certo(sonda):
    certa = "SELECT nome FROM evento WHERE inicio <= '2026-01-01' ORDER BY inicio DESC LIMIT 1"
    assert "data_futura" not in _regras(ver.checar("Qual o evento mais recente?", certa, [{"nome": "Recente"}], sonda=sonda))
    # a pergunta fala em futuro
    futuro = "SELECT nome FROM evento ORDER BY inicio DESC LIMIT 1"
    assert "data_futura" not in _regras(ver.checar("Qual o próximo evento?", futuro, [{"nome": "Futuro"}], sonda=sonda))
    # ordem crescente, ou coluna que não é data
    assert "data_futura" not in _regras(ver.checar("Qual o primeiro evento?", "SELECT nome FROM evento ORDER BY inicio ASC LIMIT 1", [{"nome": "Antigo"}], sonda=sonda))
    assert "data_futura" not in _regras(ver.checar("Qual o maior id?", "SELECT nome FROM evento ORDER BY id DESC LIMIT 1", [{"nome": "Futuro"}], sonda=sonda))


def test_coluna_irma(sonda):
    sql = "SELECT COUNT(*) AS n FROM cargo WHERE nome LIKE '%diretor%'"
    alertas = ver.checar("Quantos diretores?", sql, [{"n": 1}], sonda=sonda)
    assert "coluna_irma" in _regras(alertas)
    texto = next(a["mensagem"] for a in alertas if a["regra"] == "coluna_irma")
    assert "level" in texto and "'DIRECTOR'" in texto and "pega 1 linhas" in texto and "pega 2" in texto
    # a SQL já usa a coluna irmã; ou o termo não parece com nenhum valor de categoria
    junto = "SELECT COUNT(*) AS n FROM cargo WHERE nome LIKE '%diretor%' OR level = 'DIRECTOR'"
    assert "coluna_irma" not in _regras(ver.checar("Quantos diretores?", junto, [{"n": 2}], sonda=sonda))
    assert "coluna_irma" not in _regras(ver.checar("Quantos?", "SELECT COUNT(*) AS n FROM cargo WHERE nome LIKE '%marketing%'", [{"n": 1}], sonda=sonda))
    # o valor parecido pega o mesmo número de linhas: nada a avisar
    igual = "SELECT COUNT(*) AS n FROM cargo WHERE nome LIKE '%analista%'"
    assert "coluna_irma" not in _regras(ver.checar("Quantos analistas?", igual, [{"n": 1}], sonda=sonda))


def test_linhas_que_nao_sao_pessoas(sonda):
    pergunta = "Quantos membros tem cada grupo?"
    sql = "SELECT grupo, COUNT(*) AS n FROM membro GROUP BY grupo"
    alertas = ver.checar(pergunta, sql, [{"grupo": 1, "n": 2}, {"grupo": 2, "n": 1}], sonda=sonda)
    assert "linhas_que_nao_sao_pessoas" in _regras(alertas)
    assert "is_bl_account em membro (1)" in next(a["mensagem"] for a in alertas if a["regra"] == "linhas_que_nao_sao_pessoas")
    # a marca já foi filtrada, ou a consulta não conta nada
    filtrada = "SELECT grupo, COUNT(*) AS n FROM membro WHERE NOT is_bl_account GROUP BY grupo"
    assert "linhas_que_nao_sao_pessoas" not in _regras(ver.checar(pergunta, filtrada, [{"grupo": 1, "n": 1}, {"grupo": 2, "n": 1}], sonda=sonda))
    assert "linhas_que_nao_sao_pessoas" not in _regras(ver.checar("Quais membros?", "SELECT id FROM membro", [{"id": 1}], sonda=sonda))
    # "internal" é marca, "international" não é (a checagem é por parte do nome, não por pedaço de texto)
    assert ver.MARCAS_NAO_PESSOA & set("is_international".split("_")) == set()


def test_chave_json_inexistente_no_postgres():
    class SondaPg(ver._Sonda):
        def colunas(self, tabela):
            return [("institution_info", "jsonb"), ("name", "text")]

    pedidos = []

    def executar(sql, parametros, limite):
        pedidos.append((sql, parametros))
        return ["count"], [(0 if parametros["k"] == "country" else 45,)]

    sonda = SondaPg("postgres", executar)
    arvore = ver._parse("SELECT institution_info->>'country' AS pais, COUNT(*) FROM network_bl GROUP BY 1", "postgres")
    assert ver._chave_json_inexistente(arvore, sonda) == "institution_info->>'country'"
    assert pedidos[0][1] == {"k": "country"} and "country" not in pedidos[0][0]  # a chave vai como parâmetro
    arvore = ver._parse("SELECT institution_info->>'institution_country' AS pais FROM network_bl", "postgres")
    assert ver._chave_json_inexistente(arvore, sonda) == ""
    # banco fora do ar: não acusa
    sonda_falha = SondaPg("postgres", lambda *a: None)
    assert ver._chave_json_inexistente(ver._parse("SELECT institution_info->>'x' FROM t", "postgres"), sonda_falha) == ""


def test_duvida_diz_quanto_o_resultado_muda_sem_o_filtro(sonda):
    pergunta = "Quantos membros existem?"
    sql = "SELECT COUNT(*) AS n FROM membro WHERE is_international = 1"
    assert ver.checar_duvidas(pergunta, sql, sonda, [{"n": 1}]) == ["is_international = 1 (sem ele, o resultado mais que dobra)"]
    sql = "SELECT COUNT(*) AS n FROM membro WHERE grupo < 3"
    assert ver.checar_duvidas(pergunta, sql, sonda, [{"n": 3}]) == ["grupo < 3"]  # não muda nada: fica só o filtro
    # sem sonda continua só o filtro
    assert ver.checar_duvidas(pergunta, "SELECT COUNT(*) AS n FROM membro WHERE is_international = 1") == ["is_international = 1"]


def test_impacto_nao_traz_a_tabela_inteira():
    pedidos = []

    def executar(sql, parametros, limite):
        pedidos.append(sql)
        return ["a"], [(1,)]

    sonda = ver._Sonda("postgres", executar)
    ver.checar_duvidas("Quais?", "SELECT a FROM t WHERE b = 'x'", sonda, [{"a": 1}])
    assert pedidos and f"LIMIT {ver.MAX_LINHAS_SONDA}" in pedidos[-1] and "WHERE TRUE" in pedidos[-1]


def test_sonda_nao_executa_o_que_nao_for_leitura_e_nao_quebra(sonda):
    assert sonda.consultar("DROP TABLE evento") is None
    assert sonda.consultar("SELECT 1; DROP TABLE evento") is None
    assert sonda.consultar("SELECT coluna_que_nao_existe FROM evento") is None
    assert sonda.colunas("tabela_que_nao_existe") == []
    assert sonda.consultar("SELECT COUNT(*) FROM evento")[1] == [(3,)]
    assert sonda.contar("evento", "nome = :v", {"v": "x' OR '1'='1"}) == 0  # valor vai como parâmetro


def test_sonda_para_quando_acaba_o_tempo():
    chamadas = []
    sonda = ver._Sonda("sqlite", lambda *a: chamadas.append(a) or (["a"], []))
    sonda._fim = time.monotonic() - 1
    assert sonda.consultar("SELECT 1") is None and chamadas == []


def test_nomes_estranhos_ficam_entre_aspas():
    assert ver._Sonda("postgres", None).nome('a"b') == '"a""b"'
    assert ver._Sonda("sqlite", None).nome("First Name") == '"First Name"'
    assert ver._Sonda("mysql", None).nome("a`b") == "`a``b`"


def test_checador_pela_sonda():
    class Falsa:
        def __init__(self, respostas):
            self.respostas = respostas

        def colunas_de_texto(self):
            return {"status": [("t", "status")]}

        def nome(self, n):
            return f'"{n}"'

        def consultar(self, sql, parametros=None):
            return self.respostas

    assert ver._checador_pela_sonda(Falsa((["x"], [(1,)])))("status", "a", False) is True
    assert ver._checador_pela_sonda(Falsa((["x"], [])))("status", "a", False) is False
    assert ver._checador_pela_sonda(Falsa(None))("status", "a", False) is None  # falhou: não dá para saber
    assert ver._checador_pela_sonda(Falsa(None))("coluna_desconhecida", "a", False) is None


def test_no_com_engine_sqlite(portal):
    from sqlalchemy import create_engine
    engine = create_engine(f"sqlite:///{portal}")
    estado = {"status": "exec_ok", "pergunta_atual": "Quantos diretores?", "sql_gerada": "SELECT COUNT(*) AS n FROM cargo WHERE nome LIKE '%diretor%'",
              "linhas_resultado_completo": [{"n": 1}]}
    out = ver.nos_nodo_verificador(estado, engine=engine, usar_jev=False)
    assert "coluna_irma" in _regras(out["alertas_verificador"]) and out["verificacao"]["alerta"] is True
    engine.dispose()


def test_no_com_engine_de_banco_sem_suporte_segue_sem_sonda():
    class Dialeto:
        name = "oracle"

    class Engine:
        dialect = Dialeto()

    out = ver.nos_nodo_verificador({"status": "exec_ok", "pergunta_atual": "How many?", "sql_gerada": "SELECT 1", "linhas_resultado_completo": []},
                                   engine=Engine(), usar_jev=False)
    assert _regras(out["alertas_verificador"]) == ["resultado_vazio"]


def test_regra_do_banco_que_quebra_nao_apaga_as_outras(monkeypatch, sonda):
    def quebra(*a, **k):
        raise RuntimeError("erro inesperado")
    monkeypatch.setattr(ver, "_coluna_irma", quebra)
    assert _regras(ver.checar("How many?", "SELECT 1", [], sonda=sonda)) == ["resultado_vazio"]


def test_parametros_no_executor_via_engine(portal):
    from sqlalchemy import create_engine
    from text_to_insight.nodes.code_agent.code_sql import executar_sql_via_engine
    engine = create_engine(f"sqlite:///{portal}")
    r = executar_sql_via_engine(engine, "SELECT nome FROM evento WHERE nome = :v", parametros={"v": "Recente"})
    assert r["ok"] and r["linhas_resultado_completo"] == [{"nome": "Recente"}]
    assert executar_sql_via_engine(engine, "SELECT COUNT(*) AS n FROM evento")["linhas_resultado_completo"] == [{"n": 3}]
    engine.dispose()
