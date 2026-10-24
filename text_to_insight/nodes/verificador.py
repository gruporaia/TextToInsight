"""
Nó Verificador: confere a SQL e o resultado DEPOIS da execução e avisa quando a resposta parece errada.

O Jev é o verificador principal: recebe a pergunta, a dica, a SQL, as primeiras linhas do resultado e o
schema, e responde 6 checagens agrupadas em níveis (alta = 1/2, média = 3/4, baixa = 5/6, ou nenhum).
O nível diz quais checagens apontaram o problema, não a gravidade.

As regras ficam em segundo plano: rodam sempre (são grátis, sem LLM), completam o Jev com o que só se vê
consultando o banco (ex.: um valor de filtro que não existe) e são o plano B quando não há TYPESAFE_API_KEY
ou o Jev está fora do ar.

Modos:
- "off": o nó nem entra no grafo.
- "avisar": guarda o resultado no estado; o nó de resposta acrescenta um aviso ao texto final.

Nos testes com as respostas do BIRD (GPT-6 Luna e gpt-4o-mini), nenhum alerta caiu em resposta certa.

A checagem de valor no banco só roda em SQLite (arquivo local ou Engine SQLite), como a exploração de dados.
"""

from __future__ import annotations

import re
import sqlite3
import time
from typing import Any, Callable

import sqlglot
from sqlglot import exp

from ..state import EstadoTextToInsight

LIMITE_CONSULTA_S = 2.0
MAX_VALORES_CHECADOS = 20

PEDE_VARIAS = re.compile(
    r"\b(and|indicate|list|state the|give the|provide|along with|with (their|its|the)|e|liste|informe|indique)\b|,",
    re.I,
)
PEDE_UM_NUMERO = re.compile(
    r"^\s*(how many|what (is|was) the (percentage|ratio|average|number|total|difference)|what percentage|calculate"
    r"|quant[oa]s|qual (é|foi) (a|o) (porcentagem|percentual|média|total|número|diferença))",
    re.I,
)

MENSAGENS = {
    "resultado_vazio": "a consulta não devolveu nenhuma linha",
    "resultado_zero": "o resultado deu zero ou vazio (NULL)",
    "valor_inexistente": "o filtro usa valores que não existem no banco: {detalhe}",
    "numero_com_varias_colunas": "a pergunta pede um número, mas a resposta tem várias colunas ({detalhe})",
    "colunas_nao_pedidas": "a resposta tem colunas que a pergunta não pediu ({detalhe})",
    "coluna_de_apoio": "a resposta devolve colunas usadas só para filtrar ou ordenar ({detalhe})",
}


def _pede_varias_coisas(pergunta: str) -> bool:
    return bool(PEDE_VARIAS.search(pergunta)) or pergunta.count("?") > 1


def _parse(sql: str):
    try:
        return sqlglot.parse_one(sql, read="sqlite")
    except Exception:
        return None


def _colunas_de_apoio(arvore) -> list[str]:
    """Colunas do SELECT final que também aparecem em WHERE/HAVING/ORDER BY."""
    sel = arvore if isinstance(arvore, exp.Select) else arvore.find(exp.Select)
    if sel is None or len(sel.expressions) < 2:
        return []
    usadas = set()
    for parte in ("where", "having", "order"):
        no = sel.args.get(parte)
        if no is not None:
            usadas |= {c.name.lower() for c in no.find_all(exp.Column)}
    apoio = []
    for e in sel.expressions:
        alvo = e.this if isinstance(e, exp.Alias) else e
        if isinstance(alvo, exp.Column) and alvo.name.lower() in usadas:
            apoio.append(alvo.name)
    return apoio


def _colunas_do_select(arvore) -> list[str]:
    """Nomes das colunas do SELECT final (o resultado em dict perde colunas com nome repetido)."""
    sel = arvore if isinstance(arvore, exp.Select) else arvore.find(exp.Select)
    if sel is None or any(isinstance(e, exp.Star) or e.find(exp.Star) for e in sel.expressions):
        return []
    return [e.alias_or_name for e in sel.expressions]


def _filtros_de_texto(arvore) -> list[tuple[str, str, bool]]:
    """(coluna, valor, é_like) de cada filtro `coluna = 'texto'`, `coluna IN ('...')` ou `coluna LIKE '...'`."""
    filtros = []
    for cmp_ in list(arvore.find_all(exp.EQ)) + list(arvore.find_all(exp.Like)):
        col, lit = cmp_.this, cmp_.expression
        if isinstance(lit, exp.Column) and isinstance(col, exp.Literal):
            col, lit = lit, col
        if isinstance(col, exp.Column) and isinstance(lit, exp.Literal) and lit.is_string:
            filtros.append((col.name, lit.this, isinstance(cmp_, exp.Like)))
    for in_ in arvore.find_all(exp.In):
        if isinstance(in_.this, exp.Column):
            for lit in in_.expressions:
                if isinstance(lit, exp.Literal) and lit.is_string:
                    filtros.append((in_.this.name, lit.this, False))
    return filtros[:MAX_VALORES_CHECADOS]


def checar(
    pergunta: str,
    sql: str,
    linhas: list[dict[str, Any]],
    valor_existe: Callable[[str, str, bool], bool | None] | None = None,
) -> list[dict[str, str]]:
    """
    Aplica as regras e devolve a lista de alertas ({"regra", "mensagem"}).

    `valor_existe(coluna, valor, like)` responde se o valor aparece em alguma coluna com esse
    nome no banco (None = não dá para saber). Sem ela, a regra de valor inexistente é pulada.
    """
    alertas: list[dict[str, str]] = []
    # A dica (no BIRD vem colada na pergunta, depois de "Hint:") tem vírgulas e "and";
    # as regras de colunas olham só a pergunta.
    from .jev_cliente import separar_dica
    pergunta, _ = separar_dica(pergunta)

    def alerta(regra: str, detalhe: str = "") -> None:
        alertas.append({"regra": regra, "mensagem": MENSAGENS[regra].format(detalhe=detalhe)})

    colunas = list(linhas[0].keys()) if linhas and isinstance(linhas[0], dict) else []
    if not linhas:
        alerta("resultado_vazio")
    elif len(linhas) == 1 and len(colunas) == 1 and linhas[0][colunas[0]] in (0, 0.0, None):
        alerta("resultado_zero")

    arvore = _parse(sql) if sql else None
    if arvore is not None and valor_existe is not None:
        faltando = []
        for coluna, valor, like in _filtros_de_texto(arvore):
            if valor_existe(coluna, valor, like) is False:
                faltando.append(f"{coluna} = '{valor}'")
        if faltando:
            alerta("valor_inexistente", ", ".join(faltando))

    pede_varias = _pede_varias_coisas(pergunta)
    if arvore is not None and linhas:
        do_select = _colunas_do_select(arvore)
        if len(do_select) > len(colunas):
            colunas = do_select
    if len(colunas) > 1:
        if PEDE_UM_NUMERO.search(pergunta):
            alerta("numero_com_varias_colunas", ", ".join(colunas))
        elif not pede_varias:
            alerta("colunas_nao_pedidas", ", ".join(colunas))
    if arvore is not None and not pede_varias:
        apoio = _colunas_de_apoio(arvore)
        if apoio:
            alerta("coluna_de_apoio", ", ".join(apoio))
    return alertas


def _abrir_sqlite(estado: EstadoTextToInsight, engine=None):
    """Conexão SQLite só de leitura (do Engine ou do db_path), ou None se o banco não for SQLite."""
    if engine is not None:
        if engine.dialect.name != "sqlite":
            return None, None
        conn = engine.raw_connection()
        return conn, conn.close
    from .data_exploration import _backend_suporta_exploracao

    db_path = (estado.get("db_path") or "").strip()
    if not db_path or not _backend_suporta_exploracao(estado, db_path):
        return None, None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None, None
    return conn, conn.close


def _checador_de_valores(conn) -> Callable[[str, str, bool], bool | None]:
    """Função que procura um valor nas colunas com aquele nome. Nomes vêm do PRAGMA; o valor vai como parâmetro."""
    por_nome: dict[str, list[tuple[str, str]]] = {}
    tabelas = [t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t in tabelas:
        for r in conn.execute('PRAGMA table_info("{}")'.format(t.replace('"', '""'))):
            por_nome.setdefault(r[1].lower(), []).append((t, r[1]))

    def q(nome: str) -> str:
        return '"' + nome.replace('"', '""') + '"'

    def valor_existe(coluna: str, valor: str, like: bool) -> bool | None:
        alvos = por_nome.get(coluna.lower())
        if not alvos:
            return None
        op = "LIKE" if like else "="
        fim = time.monotonic() + LIMITE_CONSULTA_S
        if hasattr(conn, "set_progress_handler"):
            conn.set_progress_handler(lambda: 1 if time.monotonic() > fim else 0, 10_000)
        try:
            for tabela, col in alvos:
                if conn.execute(f"SELECT 1 FROM {q(tabela)} WHERE {q(col)} {op} ? LIMIT 1", (valor,)).fetchone():
                    return True
            return False
        except Exception:
            return None
        finally:
            if hasattr(conn, "set_progress_handler"):
                conn.set_progress_handler(None, 0)

    return valor_existe


# ---------------------------------------------------------------------------
# Níveis: as 6 checagens das regras agrupadas 2 a 2. O nível diz QUAIS checagens apontaram o problema
# (alta = 1 e/ou 2, média = 3 e/ou 4, baixa = 5 e/ou 6); não é uma escala de gravidade.
# Com o Jev ligado, ele responde as mesmas checagens lendo a pergunta, a SQL e o resultado (sem ver os
# alertas das regras), e o alerta sai quando as regras OU o Jev apontam algum grupo.
# ---------------------------------------------------------------------------

NIVEIS = ["alta", "media", "baixa", "nenhum"]
NIVEL_DA_REGRA = {
    "valor_inexistente": "alta",
    "resultado_vazio": "alta",
    "resultado_zero": "media",
    "numero_com_varias_colunas": "media",
    "colunas_nao_pedidas": "baixa",
    "coluna_de_apoio": "baixa",
}
DESCRICAO_DO_NIVEL = {
    "alta": "um filtro usa um valor que não existe nos dados ou o resultado veio vazio",
    "media": "o resultado é um único zero ou nulo, ou a pergunta pede um número e vieram várias colunas",
    "baixa": "a resposta tem colunas que a pergunta não pediu ou que só servem para filtrar ou ordenar",
}

_GRUPOS_JEV = {
    "alta": "(1) a filter in `sql` compares a column with a value that does not appear in that column's data; "
            "or (2) `result` is empty (0 rows).",
    "media": "(3) `result` is a single value that is zero or NULL; "
             "or (4) `question` asks for a single number but `result` has more than one column.",
    "baixa": "(5) `result` has columns that `question` did not ask for; "
             "or (6) `result` returns a column that is only used to filter or sort, not asked for.",
    "nenhum": "None of these problems: the answer has none of the issues (1) to (6).",
}
_INSTRUCAO_GRUPO = ("Read `question`, `hint`, the generated `sql` and its `result` (row count, columns and first rows). "
                    "Which group of problems does this answer have?")


def perguntas_jev() -> dict[str, dict]:
    from .jev_cliente import escolha
    return {
        "grupo": escolha(_INSTRUCAO_GRUPO, _GRUPOS_JEV),
        "grupo_inv": escolha(_INSTRUCAO_GRUPO, dict(reversed(list(_GRUPOS_JEV.items())))),
    }


def resultado_em_texto(linhas: list[dict[str, Any]], sql: str = "", max_linhas: int = 5, max_chars: int = 1500) -> str:
    """Resumo curto do resultado para o Jev: número de linhas, colunas e as primeiras linhas."""
    if not linhas:
        return "0 rows (empty result)"
    colunas = list(linhas[0].keys())
    arvore = _parse(sql) if sql else None
    do_select = _colunas_do_select(arvore) if arvore is not None else []
    if len(do_select) > len(colunas):
        colunas = do_select
    partes = [f"{len(linhas)} row(s); columns: {', '.join(map(str, colunas))}"]
    for linha in linhas[:max_linhas]:
        partes.append(" | ".join(str(v)[:80] for v in linha.values()))
    return "\n".join(partes)[:max_chars]


def estado_para_jev(estado: EstadoTextToInsight) -> dict[str, str]:
    """O que o Jev recebe. Não inclui os alertas das regras: os dois julgam de forma independente."""
    from .jev_cliente import schema_compacto, separar_dica
    pergunta = estado.get("pergunta_atual", "") or estado.get("pergunta_original", "") or estado.get("pergunta_usuario", "")
    texto, dica = separar_dica(pergunta)
    sql = estado.get("sql_gerada", "")
    linhas = estado.get("linhas_resultado_completo") or estado.get("linhas_resultado_preview") or []
    state = {
        "question": texto,
        "sql": sql,
        "result": resultado_em_texto(linhas, sql),
        "schema": schema_compacto(estado.get("contexto_rag_schema") or estado.get("contexto_schema", "")),
    }
    if dica:
        state["hint"] = dica
    return state


def combinar(alertas: list[dict[str, str]], respostas_jev: dict | None) -> dict[str, Any]:
    """
    Junta o Jev (principal) e as regras (segundo plano). `alerta` é True quando o Jev OU as regras apontam
    algum grupo; `nivel` é o do Jev quando ele apontou algo, senão o da regra mais forte.
    Guarda as probabilidades do Jev (média das duas ordens da escolha).
    """
    from .jev_cliente import media_das_duas_ordens
    niveis_regras = sorted({NIVEL_DA_REGRA[a["regra"]] for a in alertas}, key=NIVEIS.index)
    resultado: dict[str, Any] = {"alerta": bool(alertas), "nivel": "", "nivel_jev": "", "probs_jev": {},
                                 "niveis_regras": niveis_regras, "origem": ""}
    if respostas_jev:
        probs = media_das_duas_ordens(respostas_jev, "grupo", NIVEIS)
        nivel_jev = max(probs, key=probs.get)
        resultado.update(nivel_jev=nivel_jev, probs_jev={k: round(v, 3) for k, v in probs.items()})
        resultado["alerta"] = bool(alertas) or nivel_jev != "nenhum"
    jev_apontou = resultado["nivel_jev"] not in ("", "nenhum")
    resultado["origem"] = " e ".join(x for x, ok in (("jev", jev_apontou), ("regras", bool(alertas))) if ok)
    # nível principal: o do Jev quando ele apontou algo; senão o da regra mais forte (plano B)
    resultado["nivel"] = resultado["nivel_jev"] if jev_apontou else (niveis_regras[0] if niveis_regras else "nenhum")
    return resultado


def _julgar_com_jev(estado: EstadoTextToInsight) -> dict | None:
    from .jev_cliente import chave_jev, perguntar
    chave = chave_jev()
    if not chave:
        print("[VERIFICADOR] TYPESAFE_API_KEY não configurada — seguindo só com as regras.")
        return None
    try:
        return perguntar(estado_para_jev(estado), perguntas_jev(), chave)
    except Exception as e:
        # A mensagem não leva o pedido nem a chave.
        print(f"[VERIFICADOR] Jev indisponível ({type(e).__name__}) — seguindo só com as regras.")
        return None


def nos_nodo_verificador(estado: EstadoTextToInsight, modo: str = "avisar", engine=None, usar_jev: bool = True) -> dict:
    """Roda as regras (e o Jev, se ligado) depois de uma execução bem-sucedida e guarda o resultado no estado."""
    if modo == "off" or estado.get("status") != "exec_ok":
        return {"alertas_verificador": [], "verificacao": {}}
    pergunta = estado.get("pergunta_atual", "") or estado.get("pergunta_original", "") or estado.get("pergunta_usuario", "")
    sql = estado.get("sql_gerada", "")
    linhas = estado.get("linhas_resultado_completo") or estado.get("linhas_resultado_preview") or []

    conn, fechar = None, None
    try:
        conn, fechar = _abrir_sqlite(estado, engine)
        valor_existe = _checador_de_valores(conn) if conn is not None else None
    except Exception as e:
        print(f"[VERIFICADOR] Sem checagem de valores no banco: {e}")
        valor_existe = None
    try:
        alertas = checar(pergunta, sql, linhas, valor_existe)
    except Exception as e:
        print(f"[VERIFICADOR] Falha ao verificar, seguindo sem alertas: {e}")
        alertas = []
    finally:
        if fechar is not None:
            fechar()

    respostas_jev = _julgar_com_jev(estado) if usar_jev else None
    try:
        verificacao = combinar(alertas, respostas_jev)
    except Exception as e:
        print(f"[VERIFICADOR] Resposta do Jev inesperada ({type(e).__name__}) — seguindo só com as regras.")
        verificacao = combinar(alertas, None)

    probs = verificacao["probs_jev"]
    jev = (f"{verificacao['nivel_jev']} (" + " / ".join(f"{k} {v:.0%}" for k, v in probs.items()) + ")") if probs else "sem Jev"
    print(f"[VERIFICADOR] Alerta: {'sim' if verificacao['alerta'] else 'não'} | nível: {verificacao['nivel'] or '-'}"
          f" | jev: {jev} | regras: {', '.join(verificacao['niveis_regras']) or '-'} | origem: {verificacao['origem'] or '-'}")
    if alertas:
        print("[VERIFICADOR] Detalhes das regras: " + " | ".join(a["mensagem"] for a in alertas))
    return {"alertas_verificador": alertas, "verificacao": verificacao}


def texto_do_aviso(alertas: list[dict[str, str]], verificacao: dict[str, Any] | None = None) -> str:
    """Aviso no fim da resposta: primeiro o que o Jev apontou, depois os detalhes das regras."""
    verificacao = verificacao or {}
    if not alertas and not verificacao.get("alerta"):
        return ""
    nivel_jev = verificacao.get("nivel_jev", "")
    if nivel_jev in DESCRICAO_DO_NIVEL:
        prob = verificacao.get("probs_jev", {}).get(nivel_jev)
        texto = "\n\nAtenção: esta resposta pode estar errada. A checagem automática indica que " + DESCRICAO_DO_NIVEL[nivel_jev]
        texto += f" ({prob:.0%})" if prob else ""
        if alertas:
            texto += ". Detalhes: " + "; ".join(a["mensagem"] for a in alertas)
        return texto + "."
    return "\n\nAtenção: esta resposta pode estar errada, porque " + "; ".join(a["mensagem"] for a in alertas) + "."
