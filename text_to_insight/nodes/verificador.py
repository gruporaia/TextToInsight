from __future__ import annotations

import difflib
import numbers
import re
import sqlite3
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import sqlglot
from sqlglot import exp

from ..state import EstadoTextToInsight

LIMITE_CONSULTA_S = 2.0
LIMITE_SONDAGEM_S = 5.0
MAX_VALORES_CHECADOS = 20
MAX_LINHAS_SONDA = 5000
MAX_FILTROS_TESTADOS = 5
MUDANCA_RELEVANTE = 0.10
_DIALETOS = {"sqlite": "sqlite", "postgresql": "postgres", "mysql": "mysql"}

PEDE_VARIAS = re.compile(
    r"\b(and|indicate|list|state the|give the|provide|along with|with (their|its|the)|e|liste|informe|indique)\b|,",
    re.I,
)
PEDE_UM_NUMERO = re.compile(
    r"^\s*(how many|what (is|was) the (percentage|ratio|average|number|total|difference)|what percentage|calculate"
    r"|quant[oa]s|qual (é|foi) (a|o) (porcentagem|percentual|média|total|número|diferença))",
    re.I,
)
POR_GRUPO = re.compile(r"\b(cada|por|each|per|for every)\b", re.I)
FALA_DE_FUTURO = re.compile(r"\b(próxim\w*|futur\w*|agendad\w*|vai|vão|upcoming|future|next|will|scheduled|planned)\b", re.I)
DATA_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?")
MARCAS_NAO_PESSOA = {"account", "conta", "test", "teste", "deleted", "removed", "apagado", "apagada", "bot", "system", "sistema",
                     "demo", "fake", "dummy", "internal", "interno", "interna", "institucional"}

MENSAGENS = {
    "resultado_vazio": "a consulta não devolveu nenhuma linha",
    "resultado_zero": "o resultado deu zero ou vazio (NULL)",
    "valor_inexistente": "o filtro usa valores que não existem no banco: {detalhe}",
    "numero_com_varias_colunas": "a pergunta pede um número, mas a resposta tem várias colunas ({detalhe})",
    "colunas_nao_pedidas": "a resposta tem colunas que a pergunta não pediu ({detalhe})",
    "coluna_de_apoio": "a resposta devolve colunas usadas só para filtrar ou ordenar ({detalhe})",
    "colunas_juntadas": "a resposta junta várias colunas numa só ({detalhe})",
    "data_futura": "a consulta escolheu um registro com data no futuro ({detalhe})",
    "chave_json_inexistente": "a consulta usa uma chave JSON que não existe nos dados: {detalhe}",
    "coluna_zerada": "a coluna {detalhe} veio inteira zerada ou vazia",
    "um_grupo_so": "a pergunta pede o valor por grupo, mas saiu um grupo só",
    "coluna_irma": "{detalhe}",
    "linhas_que_nao_sao_pessoas": "a contagem inclui linhas marcadas como {detalhe}, que podem não ser o que a pergunta quer contar",
}


def _pede_varias_coisas(pergunta: str) -> bool:
    return bool(PEDE_VARIAS.search(pergunta)) or pergunta.count("?") > 1


def _parse(sql: str, dialeto: str = "sqlite"):
    try:
        return sqlglot.parse_one(sql, read=dialeto)
    except Exception:
        return None


def _colunas_de_apoio(arvore) -> list[str]:
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
    sel = arvore if isinstance(arvore, exp.Select) else arvore.find(exp.Select)
    if sel is None or any(isinstance(e, exp.Star) or e.find(exp.Star) for e in sel.expressions):
        return []
    return [e.alias_or_name for e in sel.expressions]


def _filtros_de_texto(arvore) -> list[tuple[str, str, bool]]:
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
    sonda: _Sonda | None = None,
) -> list[dict[str, str]]:
    alertas: list[dict[str, str]] = []
    from .jev_cliente import separar_dica
    pergunta, _ = separar_dica(pergunta)

    def alerta(regra: str, detalhe: str = "") -> None:
        alertas.append({"regra": regra, "mensagem": MENSAGENS[regra].format(detalhe=detalhe)})

    colunas = list(linhas[0].keys()) if linhas and isinstance(linhas[0], dict) else []
    if not linhas:
        alerta("resultado_vazio")
    elif len(linhas) == 1 and len(colunas) == 1 and linhas[0][colunas[0]] in (0, 0.0, None):
        alerta("resultado_zero")

    arvore = _parse(sql, sonda.dialeto if sonda else "sqlite") if sql else None
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
            if not POR_GRUPO.search(pergunta):
                alerta("numero_com_varias_colunas", ", ".join(colunas))
        elif not pede_varias:
            alerta("colunas_nao_pedidas", ", ".join(colunas))
    if arvore is not None and not pede_varias:
        apoio = _colunas_de_apoio(arvore)
        if apoio:
            alerta("coluna_de_apoio", ", ".join(apoio))
    if arvore is not None:
        juntadas = _colunas_juntadas(arvore)
        if juntadas:
            alerta("colunas_juntadas", ", ".join(juntadas))
    zerada = _coluna_zerada(linhas)
    if zerada:
        alerta("coluna_zerada", zerada)
    if arvore is not None and _um_grupo_so(pergunta, arvore, len(linhas)):
        alerta("um_grupo_so")
    if arvore is not None and sonda is not None:
        for regra, achar in (("data_futura", lambda: _data_futura(pergunta, arvore, sonda)),
                             ("chave_json_inexistente", lambda: _chave_json_inexistente(arvore, sonda)),
                             ("coluna_irma", lambda: _coluna_irma(arvore, sonda)),
                             ("linhas_que_nao_sao_pessoas", lambda: _linhas_que_nao_sao_pessoas(arvore, sonda))):
            try:
                detalhe = achar()
            except Exception as e:
                print(f"[VERIFICADOR] Regra {regra} ignorada ({type(e).__name__}).")
                continue
            if detalhe:
                alerta(regra, detalhe)
    return alertas


def _coluna_zerada(linhas: list[dict[str, Any]]) -> str:
    if len(linhas) < 2 or not isinstance(linhas[0], dict):
        return ""
    for coluna in linhas[0]:
        valores = [l.get(coluna) for l in linhas]
        if all(v is None for v in valores):
            return str(coluna)
        if all(isinstance(v, numbers.Number) and not isinstance(v, bool) and v == 0 for v in valores):
            return str(coluna)
    return ""


def _um_grupo_so(pergunta: str, arvore, n_linhas: int) -> bool:
    if n_linhas != 1 or not POR_GRUPO.search(pergunta):
        return False
    final = arvore if isinstance(arvore, exp.Select) else arvore.find(exp.Select)
    return final is not None and final.args.get("group") is not None and final.args.get("limit") is None


def _colunas_juntadas(arvore) -> list[str]:
    sel = arvore if isinstance(arvore, exp.Select) else arvore.find(exp.Select)
    if sel is None:
        return []
    juntadas = []
    for e in sel.expressions:
        alvo = e.this if isinstance(e, exp.Alias) else e
        if alvo.find(exp.DPipe) is not None or alvo.find(exp.Concat) is not None or alvo.find(exp.ConcatWs) is not None:
            juntadas.append(e.alias_or_name or alvo.sql(dialect="sqlite")[:40])
    return juntadas


_PEDIU_EXCLUIR = re.compile(r"\b(not null|null|exclud\w*|non-?zero|greater than 0|more than 0|não nul\w*|diferente de zero)\b", re.I)


def _pedacos_da_coluna(nome: str) -> list[str]:
    partes = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", nome.replace("_", " "))
    return [p.lower() for p in partes if len(p) > 2] or [nome.lower()]


def _coluna_citada(nome: str, texto: str) -> bool:
    if nome.lower() in texto.lower():
        return True
    palavras = set(re.findall(r"[a-z0-9]+", texto.lower()))
    return all(any(w.startswith(p[:4]) for w in palavras) for p in _pedacos_da_coluna(nome))


def _valor_citado(outro, texto: str) -> bool:
    if outro is None:
        return False
    literais = [outro] if isinstance(outro, exp.Literal) else list(outro.find_all(exp.Literal))
    t = texto.lower()
    t_sem = re.sub(r"[^a-z0-9]", "", t)
    for lit in literais:
        v = str(lit.this).lower().strip("%")
        if not v:
            continue
        v_sem = re.sub(r"[^a-z0-9]", "", v)
        if len(v) < 3:
            if re.search(r"(?<![a-z0-9])" + re.escape(v) + r"(?![a-z0-9])", t):
                return True
            continue
        if v in t or (len(v_sem) >= 3 and v_sem in t_sem) or (len(v) >= 6 and v[:6] in t):
            return True
    return False


def _condicoes_de_filtro(arvore):
    for sel in arvore.find_all(exp.Select):
        for parte in ("where", "having"):
            no = sel.args.get(parte)
            if no is None:
                continue
            for c in no.find_all(exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.In, exp.Like, exp.Is, exp.Not):
                if isinstance(c, exp.Not):
                    if not isinstance(c.this, exp.Is):
                        continue
                    col, outro = c.this.this, c.this.expression
                elif isinstance(c, exp.Is) and isinstance(c.parent, exp.Not):
                    continue
                else:
                    col, outro = c.this, c.args.get("expression")
                    if isinstance(c, exp.In):
                        outro = exp.Tuple(expressions=c.expressions) if c.expressions else c.args.get("query")
                if not isinstance(col, exp.Column) or isinstance(outro, exp.Column):
                    continue
                yield c, col, outro


def _defensivo(c, outro) -> bool:
    if isinstance(c, exp.Not):
        return True
    eh_zero = isinstance(outro, exp.Literal) and outro.this in ("0", "")
    return (isinstance(c, exp.GT) and eh_zero and outro.this == "0") or (isinstance(c, exp.NEQ) and eh_zero)


def _data_citada(outro, texto: str) -> bool:
    literais = [outro] if isinstance(outro, exp.Literal) else list(outro.find_all(exp.Literal)) if outro is not None else []
    return any((m := re.match(r"^(\d{4})-\d{2}", str(lit.this))) and m.group(1) in texto for lit in literais)


def checar_duvidas(pergunta: str, sql: str, sonda: _Sonda | None = None, linhas: list[dict[str, Any]] | None = None) -> list[str]:
    from .jev_cliente import separar_dica
    texto_pergunta, dica = separar_dica(pergunta)
    texto = f"{texto_pergunta} {dica}"
    dialeto = sonda.dialeto if sonda else "sqlite"
    arvore = _parse(sql, dialeto) if sql else None
    if arvore is None:
        return []
    derivadas = {a.alias.lower() for a in arvore.find_all(exp.Alias) if a.alias and not isinstance(a.this, exp.Column)}
    filtros: dict[str, int] = {}
    for i, (c, col, outro) in enumerate(_condicoes_de_filtro(arvore)):
        if col.name.lower() in derivadas or (outro is not None and (outro.find(exp.Select) or outro.find(exp.Column))):
            continue
        nao_citado = not _coluna_citada(col.name, texto) and not _valor_citado(outro, texto) and not _data_citada(outro, texto)
        defensivo = _defensivo(c, outro) and not _PEDIU_EXCLUIR.search(texto)
        if nao_citado or defensivo:
            filtro = c.sql(dialect=dialeto)
            if isinstance(c, exp.Not):
                filtro = f"{col.sql(dialect=dialeto)} IS NOT NULL"
            filtros.setdefault(filtro, i)
    if sonda is None or linhas is None:
        return list(filtros)
    return [f + (_impacto_do_filtro(arvore, i, sonda, linhas) if n < MAX_FILTROS_TESTADOS else "")
            for n, (f, i) in enumerate(filtros.items())]


def _resumo(linhas) -> tuple[int, float]:
    linhas = list(linhas)
    soma = sum(float(v) for l in linhas for v in l if isinstance(v, numbers.Number) and not isinstance(v, bool))
    return len(linhas), soma


def _impacto_do_filtro(arvore, indice: int, sonda: _Sonda, linhas: list[dict[str, Any]]) -> str:
    copia = arvore.copy()
    list(_condicoes_de_filtro(copia))[indice][0].replace(exp.true())
    if isinstance(copia, exp.Query) and copia.args.get("limit") is None:
        copia = copia.limit(MAX_LINHAS_SONDA)
    res = sonda.consultar(copia.sql(dialect=sonda.dialeto))
    if res is None:
        return ""
    (n1, s1), (n2, s2) = _resumo(l.values() for l in linhas), _resumo(res[1])
    mudanca = max(abs(n1 - n2) / max(n1, 1), abs(s1 - s2) / max(abs(s1), 1))
    if mudanca < MUDANCA_RELEVANTE:
        return ""
    return " (sem ele, o resultado mais que dobra)" if mudanca >= 1 else f" (sem ele, o resultado muda {mudanca:.0%})"


class _Sonda:
    def __init__(self, dialeto: str, executar: Callable[[str, dict, float], tuple[list[str], list] | None]):
        self.dialeto = dialeto
        self._executar = executar
        self._fim = time.monotonic() + LIMITE_SONDAGEM_S
        self._colunas: dict[str, list[tuple[str, str]]] = {}

    def consultar(self, sql: str, parametros: dict[str, Any] | None = None) -> tuple[list[str], list] | None:
        resta = self._fim - time.monotonic()
        if resta <= 0:
            return None
        try:
            return self._executar(sql, parametros or {}, min(resta, LIMITE_CONSULTA_S))
        except Exception:
            return None

    def nome(self, identificador: str) -> str:
        aspas = "`" if self.dialeto == "mysql" else '"'
        return aspas + identificador.replace(aspas, aspas * 2) + aspas

    def colunas(self, tabela: str) -> list[tuple[str, str]]:
        if tabela not in self._colunas:
            if self.dialeto == "sqlite":
                sql = "SELECT name, type FROM pragma_table_info(:t)"
            else:
                sql = f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = :t AND {self._schema_atual()}"
            res = self.consultar(sql, {"t": tabela})
            self._colunas[tabela] = [(c, (t or "").lower()) for c, t in res[1]] if res else []
        return self._colunas[tabela]

    def _schema_atual(self) -> str:
        return "table_schema = DATABASE()" if self.dialeto == "mysql" else "table_schema = ANY (current_schemas(false))"

    def colunas_de_texto(self) -> dict[str, list[tuple[str, str]]]:
        res = self.consultar("SELECT table_name, column_name, data_type FROM information_schema.columns "
                             f"WHERE {self._schema_atual()}")
        por_nome: dict[str, list[tuple[str, str]]] = {}
        for tabela, coluna, tipo in (res[1] if res else []):
            if any(x in (tipo or "").lower() for x in ("char", "text", "enum")):
                por_nome.setdefault(coluna.lower(), []).append((tabela, coluna))
        return por_nome

    def contar(self, tabela: str, condicao: str, parametros: dict[str, Any] | None = None) -> int | None:
        res = self.consultar(f"SELECT count(*) FROM {self.nome(tabela)} WHERE {condicao}", parametros)
        return res[1][0][0] if res and res[1] else None


def _executor_sqlite(conn) -> Callable:
    from .code_agent.code_sql import validar_sql_segura

    def executar(sql: str, parametros: dict, limite: float):
        if not validar_sql_segura(sql)[0]:
            return None
        fim = time.monotonic() + limite
        if hasattr(conn, "set_progress_handler"):
            conn.set_progress_handler(lambda: 1 if time.monotonic() > fim else 0, 10_000)
        try:
            cur = conn.execute(sql, parametros)
            return [d[0] for d in cur.description or []], cur.fetchmany(MAX_LINHAS_SONDA)
        finally:
            if hasattr(conn, "set_progress_handler"):
                conn.set_progress_handler(None, 0)
    return executar


def _executor_engine(engine) -> Callable:
    from .code_agent.code_sql import executar_sql_via_engine

    def executar(sql: str, parametros: dict, limite: float):
        r = executar_sql_via_engine(engine, sql, limite_preview=0, timeout_segundos=max(limite, 0.2), parametros=parametros)
        if not r["ok"]:
            return None
        linhas = r["linhas_resultado_completo"][:MAX_LINHAS_SONDA]
        return (list(linhas[0]) if linhas else []), [tuple(l.values()) for l in linhas]
    return executar


def _abrir_sonda(estado: EstadoTextToInsight, engine=None):
    conn, fechar = _abrir_sqlite(estado, engine)
    if conn is not None:
        return _Sonda("sqlite", _executor_sqlite(conn)), _checador_de_valores(conn), fechar
    db_url = (estado.get("db_url") or "").strip()
    fechar = None
    if engine is None and db_url:
        from .code_agent.code_sql import _criar_engine_de_url
        engine = _criar_engine_de_url(db_url)
        fechar = engine.dispose
    dialeto = _DIALETOS.get(engine.dialect.name) if engine is not None else None
    if dialeto is None:
        return None, None, fechar
    sonda = _Sonda(dialeto, _executor_engine(engine))
    return sonda, _checador_pela_sonda(sonda), fechar


def _tabelas_da_consulta(arvore) -> dict[str, str]:
    ctes = {c.alias.lower() for c in arvore.find_all(exp.CTE)}
    return {t.alias_or_name: t.name for t in arvore.find_all(exp.Table) if t.name.lower() not in ctes}


def _como_data(valor: Any) -> datetime | None:
    if isinstance(valor, datetime):
        return valor.astimezone(timezone.utc).replace(tzinfo=None) if valor.tzinfo else valor
    if isinstance(valor, date):
        return datetime(valor.year, valor.month, valor.day)
    if isinstance(valor, str) and DATA_ISO.match(valor.strip()):
        try:
            return datetime.fromisoformat(valor.strip()[:19].replace(" ", "T"))
        except ValueError:
            return None
    return None


def _data_futura(pergunta: str, arvore, sonda: _Sonda) -> str:
    if FALA_DE_FUTURO.search(pergunta):
        return ""
    agora = datetime.now(timezone.utc).replace(tzinfo=None)
    limite_real = agora + timedelta(days=3652)
    chave_with = "with_" if "with_" in arvore.arg_types else "with"
    ctes = arvore.args.get(chave_with)
    for sel in arvore.find_all(exp.Select):
        ordem, limite = sel.args.get("order"), sel.args.get("limit")
        if ordem is None or limite is None:
            continue
        apelidos = {e.alias: e.this for e in sel.expressions if isinstance(e, exp.Alias)}
        colunas = []
        for i, o in enumerate(ordem.expressions):
            if not o.args.get("desc"):
                continue
            alvo = o.this
            if isinstance(alvo, exp.Literal) and alvo.is_int and int(alvo.this) <= len(sel.expressions):
                alvo = sel.expressions[int(alvo.this) - 1]
                alvo = alvo.this if isinstance(alvo, exp.Alias) else alvo
            elif isinstance(alvo, exp.Column) and not alvo.table and alvo.name in apelidos:
                alvo = apelidos[alvo.name]
            colunas.append(alvo.copy().as_(f"ordem_{i}"))
        if not colunas:
            continue
        q = sel.copy()
        q.set("expressions", list(q.expressions) + colunas)
        if sel is not arvore and ctes is not None:
            q.set(chave_with, ctes.copy())
        res = sonda.consultar(q.sql(dialect=sonda.dialeto))
        for linha in (res[1] if res else []):
            for v in linha[-len(colunas):]:
                d = _como_data(v)
                if d is not None and agora < d <= limite_real:
                    return str(v)[:19]
    return ""


def _chave_json_inexistente(arvore, sonda: _Sonda) -> str:
    if sonda.dialeto != "postgres":
        return ""
    tabelas = _tabelas_da_consulta(arvore)
    faltando = []
    for n in arvore.find_all(exp.JSONExtract, exp.JSONExtractScalar):
        col, caminho = n.this, n.expression
        if not isinstance(col, exp.Column) or not isinstance(caminho, exp.JSONPath):
            continue
        chaves = [p.this for p in caminho.expressions if isinstance(p, exp.JSONPathKey)]
        candidatas = [tabelas[col.table]] if col.table in tabelas else sorted(set(tabelas.values()))
        if not chaves:
            continue
        resultados = []
        for t in candidatas:
            if any(c == col.name and "json" in tipo for c, tipo in sonda.colunas(t)):
                coluna = f"CAST({sonda.nome(col.name)} AS jsonb)"
                resultados.append(sonda.contar(t, f"jsonb_exists({coluna}, :k)", {"k": chaves[0]}))
        if resultados and all(r == 0 for r in resultados):
            faltando.append(f"{col.name}->>'{chaves[0]}'")
    return ", ".join(sorted(set(faltando)))


def _sem_acento(texto: Any) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(texto)) if not unicodedata.combining(c)).lower()


def _coluna_irma(arvore, sonda: _Sonda) -> str:
    tabelas = _tabelas_da_consulta(arvore)
    usadas = {c.name.lower() for c in arvore.find_all(exp.Column)}
    for like in list(arvore.find_all(exp.Like)) + list(arvore.find_all(exp.ILike)):
        col, lit = like.this, like.expression
        if not (isinstance(col, exp.Column) and isinstance(lit, exp.Literal) and lit.is_string):
            continue
        termo = _sem_acento(lit.this.strip("%"))
        if len(termo) < 4 or "%" in termo or "_" in termo:
            continue
        candidatas = [tabelas[col.table]] if col.table in tabelas else sorted(set(tabelas.values()))
        for t in candidatas:
            colunas = sonda.colunas(t)
            if not any(c == col.name for c, _ in colunas):
                continue
            for outra, tipo in colunas:
                if outra == col.name or outra.lower() in usadas or not any(x in tipo for x in ("char", "text", "enum")):
                    continue
                res = sonda.consultar(f"SELECT DISTINCT {sonda.nome(outra)} FROM {sonda.nome(t)} "
                                      f"WHERE {sonda.nome(outra)} IS NOT NULL LIMIT 31")
                if not res or len(res[1]) > 30:
                    continue
                for (valor,) in res[1]:
                    if difflib.SequenceMatcher(None, termo, _sem_acento(valor)).ratio() < 0.8:
                        continue
                    op = "ILIKE" if sonda.dialeto == "postgres" and isinstance(like, exp.ILike) else "LIKE"
                    n_filtro = sonda.contar(t, f"{sonda.nome(col.name)} {op} :p", {"p": lit.this})
                    n_categoria = sonda.contar(t, f"{sonda.nome(outra)} = :v", {"v": valor})
                    if n_filtro is not None and n_categoria is not None and n_filtro != n_categoria:
                        return (f"o filtro em {col.name} ('{lit.this}') pega {n_filtro} linhas de {t}, mas a coluna "
                                f"{outra} tem o valor '{valor}', que pega {n_categoria}")
    return ""


def _linhas_que_nao_sao_pessoas(arvore, sonda: _Sonda) -> str:
    if arvore.find(exp.Count) is None:
        return ""
    usadas = {c.name.lower() for c in arvore.find_all(exp.Column)}
    for t in sorted(set(_tabelas_da_consulta(arvore).values())):
        for coluna, tipo in sonda.colunas(t):
            if "bool" not in tipo or coluna.lower() in usadas or not MARCAS_NAO_PESSOA & set(coluna.lower().split("_")):
                continue
            n = sonda.contar(t, sonda.nome(coluna))
            if n:
                return f"{coluna} em {t} ({n})"
    return ""


def _abrir_sqlite(estado: EstadoTextToInsight, engine=None):
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


def _checador_pela_sonda(sonda: _Sonda) -> Callable[[str, str, bool], bool | None]:
    por_nome = sonda.colunas_de_texto()

    def valor_existe(coluna: str, valor: str, like: bool) -> bool | None:
        alvos = por_nome.get(coluna.lower())
        if not alvos:
            return None
        op = "LIKE" if like else "="
        for tabela, col in alvos:
            res = sonda.consultar(f"SELECT 1 FROM {sonda.nome(tabela)} WHERE {sonda.nome(col)} {op} :v LIMIT 1", {"v": valor})
            if res is None:
                return None
            if res[1]:
                return True
        return False

    return valor_existe


NIVEIS = ["alta", "media", "baixa", "nenhum"]
NIVEL_DA_REGRA = {
    "valor_inexistente": "alta",
    "resultado_vazio": "alta",
    "resultado_zero": "media",
    "numero_com_varias_colunas": "media",
    "colunas_nao_pedidas": "baixa",
    "coluna_de_apoio": "baixa",
    "colunas_juntadas": "baixa",
    "data_futura": "alta",
    "chave_json_inexistente": "alta",
    "coluna_zerada": "media",
    "um_grupo_so": "media",
    "coluna_irma": "media",
    "linhas_que_nao_sao_pessoas": "media",
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


def combinar(alertas: list[dict[str, str]], respostas_jev: dict | None, colunas_extras: str = "erro") -> dict[str, Any]:
    from .jev_cliente import media_das_duas_ordens
    niveis_regras = sorted({NIVEL_DA_REGRA[a["regra"]] for a in alertas}, key=NIVEIS.index)
    resultado: dict[str, Any] = {"alerta": bool(alertas), "nivel": "", "nivel_jev": "", "probs_jev": {},
                                 "niveis_regras": niveis_regras, "origem": ""}
    if respostas_jev:
        probs = media_das_duas_ordens(respostas_jev, "grupo", NIVEIS)
        nivel_jev = max(probs, key=probs.get)
        resultado.update(nivel_jev=nivel_jev, probs_jev={k: round(v, 3) for k, v in probs.items()})
    jev_apontou = resultado["nivel_jev"] not in ("", "nenhum") and not (colunas_extras == "nota" and resultado["nivel_jev"] == "baixa")
    resultado["alerta"] = bool(alertas) or jev_apontou
    resultado["origem"] = " e ".join(x for x, ok in (("jev", jev_apontou), ("regras", bool(alertas))) if ok)
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
        print(f"[VERIFICADOR] Jev indisponível ({type(e).__name__}) — seguindo só com as regras.")
        return None


def nos_nodo_verificador(estado: EstadoTextToInsight, modo: str = "avisar", engine=None, usar_jev: bool = True, colunas_extras: str = "nota") -> dict:
    if modo == "off" or estado.get("status") != "exec_ok":
        return {"alertas_verificador": [], "verificacao": {}}
    pergunta = estado.get("pergunta_atual", "") or estado.get("pergunta_original", "") or estado.get("pergunta_usuario", "")
    sql = estado.get("sql_gerada", "")
    linhas = estado.get("linhas_resultado_completo") or estado.get("linhas_resultado_preview") or []

    sonda, valor_existe, fechar = None, None, None
    try:
        sonda, valor_existe, fechar = _abrir_sonda(estado, engine)
    except Exception as e:
        print(f"[VERIFICADOR] Sem consultas no banco ({type(e).__name__}).")
    alertas: list[dict[str, str]] = []
    duvidas: list[str] = []
    try:
        try:
            alertas = checar(pergunta, sql, linhas, valor_existe, sonda)
        except Exception as e:
            print(f"[VERIFICADOR] Falha ao verificar, seguindo sem alertas ({type(e).__name__}).")
        try:
            duvidas = checar_duvidas(pergunta, sql, sonda, linhas)
        except Exception as e:
            print(f"[VERIFICADOR] Falha ao procurar filtros não pedidos ({type(e).__name__}).")
    finally:
        if fechar is not None:
            fechar()

    notas = [a["mensagem"] for a in alertas if colunas_extras == "nota" and NIVEL_DA_REGRA[a["regra"]] == "baixa"]
    alertas = [a for a in alertas if a["mensagem"] not in notas]

    respostas_jev = _julgar_com_jev(estado) if usar_jev else None
    try:
        verificacao = combinar(alertas, respostas_jev, colunas_extras)
    except Exception as e:
        print(f"[VERIFICADOR] Resposta do Jev inesperada ({type(e).__name__}) — seguindo só com as regras.")
        verificacao = combinar(alertas, None, colunas_extras)
    if colunas_extras == "nota" and verificacao["nivel_jev"] == "baixa" and not notas:
        notas.append("a resposta pode ter colunas que a pergunta não pediu")

    probs = verificacao["probs_jev"]
    jev = (f"{verificacao['nivel_jev']} (" + " / ".join(f"{k} {v:.0%}" for k, v in probs.items()) + ")") if probs else "sem Jev"
    print(f"[VERIFICADOR] Alerta: {'sim' if verificacao['alerta'] else 'não'} | nível: {verificacao['nivel'] or '-'}"
          f" | jev: {jev} | regras: {', '.join(verificacao['niveis_regras']) or '-'} | origem: {verificacao['origem'] or '-'}")
    if alertas:
        print("[VERIFICADOR] Detalhes das regras: " + " | ".join(a["mensagem"] for a in alertas))
    verificacao["duvidas"] = duvidas
    verificacao["notas"] = notas
    if duvidas:
        print("[VERIFICADOR] Dúvida (filtros não pedidos): " + " | ".join(duvidas))
    if notas:
        print("[VERIFICADOR] Observação: " + " | ".join(notas))
    return {"alertas_verificador": alertas, "verificacao": verificacao}


def texto_do_aviso(alertas: list[dict[str, str]], verificacao: dict[str, Any] | None = None) -> str:
    verificacao = verificacao or {}
    duvidas = verificacao.get("duvidas") or []
    texto_duvida = ("\n\nConfira se é isso que você quer: a consulta acrescentou filtros que a pergunta não pediu: "
                    + "; ".join(duvidas) + ".") if duvidas else ""
    notas = verificacao.get("notas") or []
    texto_nota = ("\n\nObservação: " + "; ".join(notas) + ".") if notas else ""
    return _texto_do_erro(alertas, verificacao) + texto_duvida + texto_nota


def _texto_do_erro(alertas: list[dict[str, str]], verificacao: dict[str, Any]) -> str:
    if not alertas and not verificacao.get("alerta"):
        return ""
    nivel_jev = verificacao.get("nivel_jev", "")
    if nivel_jev in DESCRICAO_DO_NIVEL and "jev" in verificacao.get("origem", "jev"):
        prob = verificacao.get("probs_jev", {}).get(nivel_jev)
        texto = "\n\nAtenção: esta resposta pode estar errada. A checagem automática indica que " + DESCRICAO_DO_NIVEL[nivel_jev]
        texto += f" ({prob:.0%})" if prob else ""
        if alertas:
            texto += ". Detalhes: " + "; ".join(a["mensagem"] for a in alertas)
        return texto + "."
    return "\n\nAtenção: esta resposta pode estar errada, porque " + "; ".join(a["mensagem"] for a in alertas) + "."
