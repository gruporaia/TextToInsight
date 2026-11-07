"""
Nó Data Exploration do grafo de agentes Text-to-Insight.

Responsabilidade: após o retriever identificar as tabelas relevantes,
este nó amostra estatísticas por coluna e as injeta no contexto do LLM,
permitindo que o agente de código gere SQL mais precisa.

Fluxo: retriever → data_exploration → planejador
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..cache import diretorio_chroma
from ..state import EstadoTextToInsight
from ..token_tracking import build_token_usage_update
from ..utils import extrair_tokens

# ---------------------------------------------------------------------------
# Pydantic models – saída compacta, pronta para serialização no prompt
# ---------------------------------------------------------------------------

class NumericColumnStats(BaseModel):
    """Estatísticas para colunas numéricas (INTEGER, REAL, FLOAT, NUMERIC, DECIMAL, DOUBLE)."""
    dtype: str = "numeric"
    mean: float | None = None
    median: float | None = None
    std_dev: float | None = None
    min: float | None = None
    max: float | None = None
    null_rate: float = 0.0


class CategoricalColumnStats(BaseModel):
    """Estatísticas para colunas categóricas / texto (TEXT, VARCHAR, CHAR, etc.)."""
    dtype: str = "categorical"
    top_values: list[dict[str, Any]] = Field(default_factory=list, description="Top 5 valores com contagem")
    cardinality: int = 0
    null_rate: float = 0.0
    sample_values: list[str] = Field(default_factory=list, description="3 valores aleatórios (formato/casing)")


class DateColumnStats(BaseModel):
    """Estatísticas para colunas de data/timestamp (DATE, DATETIME, TIMESTAMP)."""
    dtype: str = "date"
    min: str | None = None
    max: str | None = None
    sample_values: list[str] = Field(default_factory=list, description="3 valores para inferir formato")
    null_rate: float = 0.0


class BooleanColumnStats(BaseModel):
    """Estatísticas para colunas booleanas (BOOLEAN, BOOL)."""
    dtype: str = "boolean"
    true_count: int = 0
    false_count: int = 0
    null_rate: float = 0.0


# Tipo union para a saída
ColumnStats = NumericColumnStats | CategoricalColumnStats | DateColumnStats | BooleanColumnStats


class TableExplorationResult(BaseModel):
    """Resultado da exploração de uma tabela, indexado por nome de coluna."""
    table_name: str
    row_count_estimate: int = 0
    columns: dict[str, ColumnStats] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Classificador de tipo de coluna
# ---------------------------------------------------------------------------

_NUMERIC_TYPES = re.compile(
    r"(int|integer|real|float|numeric|decimal|double|number|smallint|bigint|tinyint|mediumint)",
    re.IGNORECASE,
)
_DATE_TYPES = re.compile(r"(date|datetime|timestamp|time|interval)", re.IGNORECASE)
_BOOL_TYPES = re.compile(r"(bool|boolean)", re.IGNORECASE)


def _classify_column_type(declared_type: str) -> str:
    """Classifica o tipo declarado (SQLite ou PostgreSQL) em uma das 4 categorias."""
    if not declared_type:
        # SQLite permite colunas sem tipo; tratamos como categórica por segurança
        return "categorical"
    if _BOOL_TYPES.search(declared_type):
        return "boolean"
    if _DATE_TYPES.search(declared_type):
        return "date"
    if _NUMERIC_TYPES.search(declared_type):
        return "numeric"
    return "categorical"


# ---------------------------------------------------------------------------
# Funções de coleta de estatísticas
# ---------------------------------------------------------------------------

# Limite de amostragem para tabelas grandes
SAMPLE_LIMIT = 10_000

# Dialetos em que a exploração sabe rodar (SQL das estatísticas + introspecção).
DIALETOS_SUPORTADOS = {"sqlite", "postgresql"}

# Timeout por consulta de exploração no PostgreSQL. Uma coluna/tabela que
# estoure o tempo é pulada; as demais continuam.
TIMEOUT_EXPLORACAO_SEGUNDOS = 15

# No PostgreSQL, tabelas com estimativa do catálogo a partir deste número de
# linhas não são contadas: a contagem exata varreria a tabela inteira, e as
# estatísticas já usam no máximo SAMPLE_LIMIT linhas. Abaixo disso (inclusive
# estimativa -1, de tabela nunca analisada, ou 0, de view/tabela particionada)
# a contagem para em LIMIAR_ESTIMATIVA_LINHAS + 1 linhas.
LIMIAR_ESTIMATIVA_LINHAS = 100_000


class ExploracaoInterrompida(Exception):
    """A conexão deixou de responder no meio da exploração; não adianta seguir."""


def _q(identificador: str) -> str:
    """Identificador entre aspas duplas (SQLite e PostgreSQL), escapando aspas internas."""
    return '"' + identificador.replace('"', '""') + '"'


def _amostra(safe_col: str, safe_tbl: str) -> str:
    """A amostra de uma coluna: as primeiras SAMPLE_LIMIT linhas da tabela, nulos incluídos."""
    return f"(SELECT {safe_col} FROM {safe_tbl} LIMIT {SAMPLE_LIMIT}) AS _amostra"


def _preparar_sessao_postgres(conn: Any) -> None:
    """
    Abre a transação da exploração como somente leitura e com timeout.

    SET TRANSACTION e SET LOCAL valem só para a transação corrente, que é
    desfeita ao final: nada disso fica na conexão quando ela volta para o
    pool do chamador. A conexão não pode estar em autocommit (ver
    `_explorar_no_banco`), senão os comandos não têm efeito.
    """
    cursor = conn.cursor()
    try:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(f"SET LOCAL statement_timeout = {int(TIMEOUT_EXPLORACAO_SEGUNDOS * 1000)}")
        # Sem ORDER BY, "LIMIT n" só devolve sempre as mesmas linhas se a varredura
        # for sequencial, sem workers paralelos e começando do início da tabela.
        # Assim todas as estatísticas de uma coluna olham para a mesma amostra.
        cursor.execute("SET LOCAL max_parallel_workers_per_gather = 0")
        cursor.execute("SET LOCAL synchronize_seqscans = off")
    finally:
        cursor.close()


@contextmanager
def _isolar_erros(cursor: Any, dialeto: str, savepoint: str):
    """
    Roda o bloco de modo que um erro de SQL não comprometa as consultas seguintes.

    No PostgreSQL um erro aborta a transação inteira; o bloco roda num
    SAVEPOINT e, se falhar, a transação volta para ele. O erro é relançado
    para quem chamou decidir o que fazer. Se nem o SAVEPOINT ou o ROLLBACK TO
    SAVEPOINT funcionam, a conexão está perdida e levanta ExploracaoInterrompida.
    """
    if dialeto != "postgresql":
        yield
        return
    try:
        cursor.execute(f"SAVEPOINT {savepoint}")
    except Exception as erro:
        raise ExploracaoInterrompida(str(erro)) from erro
    try:
        yield
    except Exception:
        try:
            cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        except Exception as erro_rollback:
            raise ExploracaoInterrompida(str(erro_rollback)) from erro_rollback
        raise
    cursor.execute(f"RELEASE SAVEPOINT {savepoint}")


def _resolver_relacao_postgres(cursor: Any, table: str) -> tuple[str, int]:
    """
    Schema e estimativa de linhas da relação que `table` (sem schema) alcança.

    Usa a mesma resolução pelo search_path que o SQLAlchemy usa para listar
    tabelas no nó de schema e que as consultas de estatística usam ao citar
    a tabela sem schema; com tabelas homônimas, vale a primeira do search_path.
    """
    cursor.execute(
        "SELECT n.nspname, c.reltuples::bigint FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.oid = to_regclass(%s)",
        (_q(table),),
    )
    linha = cursor.fetchone()
    if linha is None:
        raise LookupError(f"tabela {table!r} não encontrada no search_path")
    return linha[0], int(linha[1])


def _listar_colunas(cursor: Any, table: str, schema_pg: str | None = None) -> list[tuple[str, str]]:
    """(nome, tipo declarado) de cada coluna, na ordem da tabela. `schema_pg` só no PostgreSQL."""
    if schema_pg is not None:
        cursor.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s "
            "ORDER BY ordinal_position",
            (schema_pg, table),
        )
        return [(nome, tipo or "") for nome, tipo in cursor.fetchall()]

    cursor.execute(f"PRAGMA table_info({_q(table)})")
    # table_info retorna: cid, name, type, notnull, dflt_value, pk
    return [(info[1], info[2] or "") for info in cursor.fetchall()]


def _contar_linhas(cursor: Any, table: str, estimativa_pg: int | None = None) -> int:
    """
    Total de linhas. `estimativa_pg` (reltuples do PostgreSQL) evita varrer
    tabelas grandes; ver LIMIAR_ESTIMATIVA_LINHAS.
    """
    if estimativa_pg is None:
        cursor.execute(f"SELECT COUNT(*) FROM {_q(table)}")
        return cursor.fetchone()[0]

    if estimativa_pg >= LIMIAR_ESTIMATIVA_LINHAS:
        return estimativa_pg
    cursor.execute(
        f"SELECT COUNT(*) FROM (SELECT 1 FROM {_q(table)} LIMIT {LIMIAR_ESTIMATIVA_LINHAS + 1}) AS _contagem"
    )
    # Passou do limite: a estimativa estava desatualizada; o limite é o melhor piso conhecido.
    return min(cursor.fetchone()[0], LIMIAR_ESTIMATIVA_LINHAS)


def _safe_float(val: Any) -> float | None:
    """Converte para float; None se não der ou se o valor não for finito."""
    if val is None or val == "":
        return None
    try:
        f_val = float(val)
    except (ValueError, TypeError):
        return None
    # numeric do PostgreSQL vai além do float (ex.: 1e400 vira inf), e NaN
    # também é um numeric válido; nenhum dos dois serve como estatística.
    return f_val if math.isfinite(f_val) else None


def _safe_round(val: Any, ndigits: int = 4) -> float | None:
    """Tenta converter para float e arredonda de forma segura."""
    f_val = _safe_float(val)
    if f_val is not None:
        return round(f_val, ndigits)
    return None


def _compute_numeric_stats(
    cursor: Any, table: str, column: str, total_rows: int, dialeto: str = "sqlite",
) -> NumericColumnStats:
    """
    Média, mediana, desvio padrão, mínimo, máximo e taxa de nulos, todos
    calculados sobre a mesma amostra (ver `_amostra`).
    """
    safe_col = _q(column)
    amostra = _amostra(safe_col, _q(table))

    cursor.execute(
        f"SELECT AVG({safe_col}), MIN({safe_col}), MAX({safe_col}), "
        f"COUNT(*) - COUNT({safe_col}), COUNT({safe_col}) "
        f"FROM {amostra}"
    )
    avg_val, min_val, max_val, null_count, non_null_count = cursor.fetchone()

    sample_size = min(total_rows, SAMPLE_LIMIT)
    null_rate = round(null_count / sample_size, 4) if sample_size > 0 else 0.0

    # Desvio padrão populacional e mediana são opcionais: se a consulta falhar,
    # a coluna fica com as estatísticas acima.
    std_dev = None
    avg_float = _safe_float(avg_val)
    if avg_float is not None:
        try:
            with _isolar_erros(cursor, dialeto, "_tti_consulta"):
                cursor.execute(
                    f"SELECT AVG(({safe_col} - {avg_float}) * ({safe_col} - {avg_float})) "
                    f"FROM {amostra} WHERE {safe_col} IS NOT NULL"
                )
                var_float = _safe_float(cursor.fetchone()[0])
            if var_float is not None and var_float >= 0:
                std_dev = round(var_float ** 0.5, 4)
        except ExploracaoInterrompida:
            raise
        except Exception:
            std_dev = None

    median_val = None
    if non_null_count:
        # Com quantidade par de valores, a mediana é a média dos dois centrais.
        meio = (non_null_count - 1) // 2
        centrais = 1 if non_null_count % 2 else 2
        try:
            with _isolar_erros(cursor, dialeto, "_tti_consulta"):
                cursor.execute(
                    f"SELECT {safe_col} FROM {amostra} WHERE {safe_col} IS NOT NULL "
                    f"ORDER BY {safe_col} LIMIT {centrais} OFFSET {meio}"
                )
                valores = [_safe_float(r[0]) for r in cursor.fetchall()]
            if valores and None not in valores:
                median_val = sum(valores) / len(valores)
        except ExploracaoInterrompida:
            raise
        except Exception:
            median_val = None

    return NumericColumnStats(
        mean=_safe_round(avg_val, 4),
        median=_safe_round(median_val, 4),
        std_dev=std_dev,
        min=_safe_round(min_val, 4),
        max=_safe_round(max_val, 4),
        null_rate=null_rate,
    )


def _valores_de_exemplo(cursor: Any, table: str, column: str, tamanho_pool: int) -> list[str]:
    """
    Até 3 valores distintos da amostra, para o LLM ver formato e grafia.

    O DISTINCT roda só sobre a amostra (numa coluna com poucos valores, um
    DISTINCT ... LIMIT na tabela inteira a varreria toda atrás de valores que
    não existem), e o sorteio usa uma semente fixa por coluna, para que a
    mesma amostra dê sempre os mesmos exemplos.
    """
    safe_col = _q(column)
    cursor.execute(
        f"SELECT DISTINCT {safe_col} FROM {_amostra(safe_col, _q(table))} "
        f"WHERE {safe_col} IS NOT NULL LIMIT {tamanho_pool}"
    )
    pool = sorted(str(r[0]) for r in cursor.fetchall())
    if not pool:
        return []
    return random.Random(f"{table}.{column}").sample(pool, min(3, len(pool)))


def _compute_categorical_stats(
    cursor: Any, table: str, column: str, total_rows: int, dialeto: str = "sqlite",
) -> CategoricalColumnStats:
    """Calcula top-5, cardinalidade e amostras para colunas categóricas."""
    safe_col = _q(column)
    safe_tbl = _q(table)

    # Top 5 valores mais frequentes
    cursor.execute(
        f"SELECT {safe_col}, COUNT(*) as cnt "
        f"FROM {_amostra(safe_col, safe_tbl)} "
        f"WHERE {safe_col} IS NOT NULL "
        f"GROUP BY {safe_col} ORDER BY cnt DESC LIMIT 5"
    )
    top_values = [{"value": str(r[0]), "count": r[1]} for r in cursor.fetchall()]

    # Cardinalidade
    cursor.execute(
        f"SELECT COUNT(DISTINCT {safe_col}) "
        f"FROM {_amostra(safe_col, safe_tbl)}"
    )
    cardinality = cursor.fetchone()[0] or 0

    # Null rate
    sample_size = min(total_rows, SAMPLE_LIMIT)
    cursor.execute(
        f"SELECT COUNT(*) - COUNT({safe_col}) "
        f"FROM {_amostra(safe_col, safe_tbl)}"
    )
    null_count = cursor.fetchone()[0]
    null_rate = round(null_count / sample_size, 4) if sample_size > 0 else 0.0

    sample_values = _valores_de_exemplo(cursor, table, column, tamanho_pool=50)

    return CategoricalColumnStats(
        top_values=top_values,
        cardinality=cardinality,
        null_rate=null_rate,
        sample_values=sample_values,
    )


def _compute_date_stats(
    cursor: Any, table: str, column: str, total_rows: int, dialeto: str = "sqlite",
) -> DateColumnStats:
    """Calcula min, max e amostras para colunas de data."""
    safe_col = _q(column)
    safe_tbl = _q(table)

    cursor.execute(
        f"SELECT MIN({safe_col}), MAX({safe_col}), "
        f"COUNT(*) - COUNT({safe_col}) "
        f"FROM {_amostra(safe_col, safe_tbl)}"
    )
    row = cursor.fetchone()
    min_val, max_val, null_count = row

    sample_size = min(total_rows, SAMPLE_LIMIT)
    null_rate = round(null_count / sample_size, 4) if sample_size > 0 else 0.0

    # 3 amostras para inferir formato
    sample_values = _valores_de_exemplo(cursor, table, column, tamanho_pool=30)

    return DateColumnStats(
        min=str(min_val) if min_val is not None else None,
        max=str(max_val) if max_val is not None else None,
        sample_values=sample_values,
        null_rate=null_rate,
    )


def _compute_boolean_stats(
    cursor: Any, table: str, column: str, total_rows: int, dialeto: str = "sqlite",
) -> BooleanColumnStats:
    """Calcula contagens true/false para colunas booleanas."""
    safe_col = _q(column)
    safe_tbl = _q(table)

    if dialeto == "postgresql":
        # boolean nativo: comparar com 1/0 é erro de tipo no PostgreSQL.
        cond_true = f"{safe_col} IS TRUE"
        cond_false = f"{safe_col} IS FALSE"
    else:
        cond_true = f"{safe_col} = 1 OR LOWER(CAST({safe_col} AS TEXT)) = 'true'"
        cond_false = f"{safe_col} = 0 OR LOWER(CAST({safe_col} AS TEXT)) = 'false'"
    cursor.execute(
        f"SELECT "
        f"SUM(CASE WHEN {cond_true} THEN 1 ELSE 0 END), "
        f"SUM(CASE WHEN {cond_false} THEN 1 ELSE 0 END), "
        f"COUNT(*) - COUNT({safe_col}) "
        f"FROM {_amostra(safe_col, safe_tbl)}"
    )
    row = cursor.fetchone()
    true_count, false_count, null_count = row

    sample_size = min(total_rows, SAMPLE_LIMIT)
    null_rate = round(null_count / sample_size, 4) if sample_size > 0 else 0.0

    return BooleanColumnStats(
        true_count=true_count or 0,
        false_count=false_count or 0,
        null_rate=null_rate,
    )


# ---------------------------------------------------------------------------
# Função principal de exploração
# ---------------------------------------------------------------------------

_STAT_DISPATCHER = {
    "numeric": _compute_numeric_stats,
    "categorical": _compute_categorical_stats,
    "date": _compute_date_stats,
    "boolean": _compute_boolean_stats,
}


def explore_table(
    conn: Any,
    table_name: str,
    columns_to_explore: list[str] | None = None,
    row_count: int | None = None,
    dialeto: str = "sqlite",
) -> TableExplorationResult:
    """
    Calcula estatísticas por coluna para uma tabela (SQLite ou PostgreSQL).

    Args:
        conn: Conexão DB-API (preferencialmente read-only). No PostgreSQL, a
            conexão não pode estar em autocommit e a transação já deve ter sido
            preparada com `_preparar_sessao_postgres` (ver `_explorar_no_banco`).
        table_name: Nome da tabela a explorar.
        columns_to_explore: Lista opcional de colunas a explorar. Se omitida (ou None), explora todas.
        row_count: Contagem de linhas já conhecida (ex: vinda do cache) para evitar SELECT COUNT(*).
        dialeto: "sqlite" ou "postgresql".

    Returns:
        TableExplorationResult com stats por coluna.
    """
    cursor = conn.cursor()
    try:
        # Um erro na introspecção desfaz só o que esta tabela fez; quem chama segue para a próxima.
        with _isolar_erros(cursor, dialeto, "_tti_tabela"):
            return _explorar_colunas(cursor, table_name, columns_to_explore, row_count, dialeto)
    finally:
        cursor.close()


def _explorar_colunas(
    cursor: Any,
    table_name: str,
    columns_to_explore: list[str] | None,
    row_count: int | None,
    dialeto: str,
) -> TableExplorationResult:
    schema_pg = estimativa_pg = None
    if dialeto == "postgresql":
        schema_pg, estimativa_pg = _resolver_relacao_postgres(cursor, table_name)

    # Contagem total de linhas (reaproveita do cache se já conhecida)
    total_rows = row_count if row_count is not None else _contar_linhas(cursor, table_name, estimativa_pg)

    columns_info = _listar_colunas(cursor, table_name, schema_pg)
    if not columns_info:
        # Tabela que não existe (ou sem colunas visíveis): não entra no
        # resultado, senão o cache a marcaria como explorada.
        raise LookupError(f"nenhuma coluna encontrada para a tabela {table_name!r}")

    result = TableExplorationResult(
        table_name=table_name,
        row_count_estimate=total_rows,
    )

    for col_name, col_type_raw in columns_info:
        if columns_to_explore is not None and col_name not in columns_to_explore:
            continue
        compute_fn = _STAT_DISPATCHER[_classify_column_type(col_type_raw)]
        try:
            with _isolar_erros(cursor, dialeto, "_tti_coluna"):
                stats = compute_fn(cursor, table_name, col_name, total_rows, dialeto=dialeto)
        except ExploracaoInterrompida:
            raise
        except Exception as e:
            # Se falhar em uma coluna (tipo sem suporte, timeout...), log e segue para as demais
            print(f"[DATA_EXPLORATION] Erro ao computar stats para {table_name}.{col_name}: {e}")
            continue

        result.columns[col_name] = stats

    return result


def _explorar_tabelas(
    conn: Any,
    table_names: list[str],
    colunas_para_explorar: dict[str, list[str]] | None,
    known_row_counts: dict[str, int] | None = None,
    dialeto: str = "sqlite",
) -> dict[str, TableExplorationResult]:
    results: dict[str, TableExplorationResult] = {}
    for table_name in table_names:
        cols_to_explore = None
        if colunas_para_explorar and table_name in colunas_para_explorar:
            cols_to_explore = colunas_para_explorar[table_name]
        row_count = known_row_counts.get(table_name) if known_row_counts else None
        try:
            results[table_name] = explore_table(
                conn, table_name, cols_to_explore, row_count=row_count, dialeto=dialeto
            )
        except ExploracaoInterrompida as e:
            # Conexão perdida: as tabelas já exploradas continuam valendo.
            print(f"[DATA_EXPLORATION] Conexão perdida em '{table_name}' ({e}) — parando a exploração.")
            break
        except Exception as e:
            print(f"[DATA_EXPLORATION] Erro ao explorar tabela '{table_name}': {e}")
            continue
    return results


# ---------------------------------------------------------------------------
# Formatação para injeção no prompt
# ---------------------------------------------------------------------------

def format_exploration_for_prompt(
    exploration_results: dict[str, TableExplorationResult],
) -> str:
    """
    Serializa os resultados da exploração em texto compacto para o prompt LLM.

    Mantém o formato conciso para minimizar consumo de tokens.
    """
    if not exploration_results:
        return ""

    parts = [_CABECALHO_EXPLORACAO]

    for table_name, table_result in exploration_results.items():
        parts.append(_formatar_tabela(table_name, table_result.row_count_estimate))

        for col_name, stats in table_result.columns.items():
            linha = _formatar_coluna(col_name, stats)
            if linha:
                parts.append(linha)
        parts.append("")  # Linha em branco entre tabelas

    return "\n".join(parts)


_CABECALHO_EXPLORACAO = "=== DATA EXPLORATION (estatísticas amostrais) ===\n"


def _formatar_tabela(table_name: str, row_count: int) -> str:
    return f"Tabela: {table_name} (~{row_count} linhas)"


def _formatar_coluna(col_name: str, stats: ColumnStats) -> str | None:
    if isinstance(stats, NumericColumnStats):
        return (
            f"  {col_name} [numeric]: "
            f"mean={stats.mean}, median={stats.median}, std={stats.std_dev}, "
            f"min={stats.min}, max={stats.max}, null_rate={stats.null_rate}"
        )
    if isinstance(stats, CategoricalColumnStats):
        top_str = ", ".join(
            f"{v['value']}({v['count']})" for v in stats.top_values
        )
        samples_str = ", ".join(f'"{s}"' for s in stats.sample_values)
        return (
            f"  {col_name} [categorical]: "
            f"cardinality={stats.cardinality}, null_rate={stats.null_rate}, "
            f"top=[{top_str}], samples=[{samples_str}]"
        )
    if isinstance(stats, DateColumnStats):
        samples_str = ", ".join(f'"{s}"' for s in stats.sample_values)
        return (
            f"  {col_name} [date]: "
            f"min={stats.min}, max={stats.max}, "
            f"null_rate={stats.null_rate}, samples=[{samples_str}]"
        )
    if isinstance(stats, BooleanColumnStats):
        return (
            f"  {col_name} [boolean]: "
            f"true={stats.true_count}, false={stats.false_count}, "
            f"null_rate={stats.null_rate}"
        )
    return None


# ---------------------------------------------------------------------------
# Cache da exploração (por pergunta)
#
# A cada falha de execução o retriever devolve mais tabelas (top_k cresce),
# e sem cache o nó recalculava as estatísticas de TODAS elas — inclusive as já
# exploradas na tentativa anterior. O cache guarda, por tabela, a contagem de
# linhas e a linha formatada de cada coluna já explorada; só o que falta é
# calculado. Fica no estado (strings/ints, serializável pelo checkpointer) e é
# zerado a cada pergunta em `construir_estado_inicial`.
#
# Formato: {tabela: {"linhas": int, "colunas": {coluna: linha}, "completa": bool}}
# "completa" indica que a tabela foi explorada sem filtro de colunas.
# ---------------------------------------------------------------------------

def _pendencias_exploracao(
    table_names: list[str],
    colunas_para_explorar: dict[str, list[str]] | None,
    cache: dict[str, dict[str, Any]],
) -> tuple[list[str], dict[str, list[str]]]:
    """Separa o que ainda precisa ser explorado: tabelas inteiras e colunas faltantes."""
    explorar_tudo: list[str] = []
    explorar_parcial: dict[str, list[str]] = {}
    for tabela in table_names:
        pedidas = colunas_para_explorar.get(tabela) if colunas_para_explorar else None
        entrada = cache.get(tabela)
        if entrada and entrada["completa"]:
            continue
        if pedidas is None:
            explorar_tudo.append(tabela)
            continue
        faltando = [c for c in pedidas if not entrada or c not in entrada["colunas"]]
        if entrada is None or faltando:
            explorar_parcial[tabela] = faltando
    return explorar_tudo, explorar_parcial


def _atualizar_cache(
    cache: dict[str, dict[str, Any]],
    resultados: dict[str, TableExplorationResult],
    completas: set[str],
) -> dict[str, dict[str, Any]]:
    """Devolve um novo cache com os resultados mesclados (não altera o recebido)."""
    novo = {
        t: {"linhas": e["linhas"], "colunas": dict(e["colunas"]), "completa": e["completa"]}
        for t, e in cache.items()
    }
    for tabela, resultado in resultados.items():
        colunas = {}
        for col, stats in resultado.columns.items():
            linha = _formatar_coluna(col, stats)
            if linha:
                colunas[col] = linha
        entrada = novo.get(tabela)
        if tabela in completas or entrada is None:
            # Exploração completa mantém a ordem original das colunas (PRAGMA).
            anteriores = entrada["colunas"] if entrada else {}
            novo[tabela] = {
                "linhas": resultado.row_count_estimate,
                "colunas": {**colunas, **{c: l for c, l in anteriores.items() if c not in colunas}},
                "completa": tabela in completas,
            }
        else:
            entrada["linhas"] = resultado.row_count_estimate
            entrada["colunas"].update(colunas)
    return novo


def _formatar_do_cache(
    table_names: list[str],
    colunas_para_explorar: dict[str, list[str]] | None,
    cache: dict[str, dict[str, Any]],
) -> str:
    """Monta o mesmo texto de `format_exploration_for_prompt`, a partir do cache."""
    parts: list[str] = []
    for tabela in table_names:
        entrada = cache.get(tabela)
        if entrada is None:  # exploração da tabela falhou
            continue
        pedidas = colunas_para_explorar.get(tabela) if colunas_para_explorar else None
        parts.append(_formatar_tabela(tabela, entrada["linhas"]))
        parts.extend(l for c, l in entrada["colunas"].items() if pedidas is None or c in pedidas)
        parts.append("")
    if not parts:
        return ""
    return "\n".join([_CABECALHO_EXPLORACAO, *parts])


# ---------------------------------------------------------------------------
# Extração de nomes de tabelas do contexto RAG
# ---------------------------------------------------------------------------

def _extract_table_names_from_rag_context(contexto_rag: str) -> list[str]:
    """
    Extrai nomes de tabelas do texto formatado pelo retriever RAG.

    O retriever formata cada tabela como 'Tabela: <nome>' numa linha
    própria. O nome vai até o fim da linha, para aceitar nomes com espaço
    ou outros caracteres (possíveis com identificadores entre aspas).
    """
    return [nome.strip() for nome in re.findall(r"^\s*Tabela:[ \t]*(.+?)[ \t]*$", contexto_rag, re.MULTILINE)]


# ---------------------------------------------------------------------------
# Nó do grafo LangGraph
# ---------------------------------------------------------------------------

def _explorar_no_banco(
    engine: Any,
    dialeto: str,
    alvos: list[str],
    explorar_parcial: dict[str, list[str]],
    known_row_counts: dict[str, int],
) -> dict[str, TableExplorationResult]:
    """
    Explora as tabelas numa conexão do pool do chamador e a devolve como recebeu.

    No PostgreSQL tudo roda numa única transação somente leitura, desfeita
    ao final. Se a engine do chamador usa autocommit (isolation_level=
    "AUTOCOMMIT"), ele é desligado durante a exploração, porque sem uma
    transação o READ ONLY e os SET LOCAL não teriam efeito, e religado antes
    de devolver a conexão.
    """
    conn = engine.raw_connection()
    if dialeto != "postgresql":
        try:
            return _explorar_tabelas(conn, alvos, explorar_parcial, known_row_counts, dialeto=dialeto)
        finally:
            conn.close()

    driver = conn.driver_connection
    autocommit_original = bool(getattr(driver, "autocommit", False))
    try:
        if autocommit_original:
            driver.autocommit = False
        _preparar_sessao_postgres(conn)
        return _explorar_tabelas(conn, alvos, explorar_parcial, known_row_counts, dialeto=dialeto)
    finally:
        try:
            conn.rollback()  # encerra a transação read-only
            if autocommit_original:
                driver.autocommit = True
        except Exception:
            # Não dá para garantir o estado da conexão: o pool a descarta.
            conn.invalidate()
        conn.close()


def nos_nodo_data_exploration(
    estado: EstadoTextToInsight,
    engine: Any,
    use_data_exploration: bool = True,
) -> dict:
    """
    Nó Data Exploration: calcula estatísticas por coluna para as tabelas
    identificadas pelo retriever RAG.

    Roda após o retriever e antes do planejador, injetando as estatísticas
    no campo `contexto_data_exploration` do estado.

    Explora o banco da Engine SQLAlchemy injetada pelo chamador. Suporta
    SQLite e PostgreSQL; para outros dialetos a exploração é pulada. No
    PostgreSQL, a exploração roda numa transação somente leitura, com
    timeout por consulta, que é desfeita ao final.
    """
    if not use_data_exploration:
        print("[DATA_EXPLORATION] Data exploration desativada via toggle.")
        return {"contexto_data_exploration": ""}

    dialeto = engine.dialect.name
    if dialeto not in DIALETOS_SUPORTADOS:
        print(f"[DATA_EXPLORATION] Exploração não suporta o dialeto '{dialeto}' — pulando.")
        return {"contexto_data_exploration": ""}

    schema_alvo = estado.get("contexto_rag_schema") or estado.get("contexto_schema", "")

    if not schema_alvo:
        print("[DATA_EXPLORATION] Sem schema — pulando exploração.")
        return {}

    # Extrair nomes de tabelas do contexto de schema (RAG ou completo)
    table_names = list(dict.fromkeys(_extract_table_names_from_rag_context(schema_alvo)))
    if not table_names:
        print("[DATA_EXPLORATION] Nenhuma tabela encontrada no contexto RAG.")
        return {}

    colunas_para_explorar = estado.get("colunas_para_explorar", None)
    cache = estado.get("cache_data_exploration") or {}
    explorar_tudo, explorar_parcial = _pendencias_exploracao(table_names, colunas_para_explorar, cache)
    alvos = explorar_tudo + list(explorar_parcial)

    print(
        f"[DATA_EXPLORATION] {len(table_names)} tabela(s): "
        f"{len(table_names) - len(alvos)} do cache, explorando {alvos}"
    )

    if alvos:
        known_row_counts = {t: cache[t]["linhas"] for t in alvos if t in cache}
        try:
            exploration_results = _explorar_no_banco(engine, dialeto, alvos, explorar_parcial, known_row_counts)
        except Exception as e:
            # A exploração é um complemento do prompt: se o banco falhar aqui
            # (conexão caiu, permissão...), o fluxo segue sem estatísticas novas.
            print(f"[DATA_EXPLORATION] Exploração interrompida ({e}) — seguindo sem estatísticas novas.")
            exploration_results = {}
        cache = _atualizar_cache(cache, exploration_results, set(explorar_tudo))

    # Formatar para injeção no prompt
    exploration_text = _formatar_do_cache(table_names, colunas_para_explorar, cache)

    print(
        f"[DATA_EXPLORATION] Estatísticas prontas: "
        f"{len(exploration_text)} chars (~{len(exploration_text) // 4} tokens)"
    )

    return {"contexto_data_exploration": exploration_text, "cache_data_exploration": cache}


PROMPT_EXPLORATION_SELECTOR = """Você é um analista de dados especialista em bancos de dados SQL.
Sua tarefa é identificar quais colunas de cada tabela são relevantes para responder à pergunta do usuário.
Você deve olhar para a pergunta e para as tabelas recuperadas pelo RAG.
Apenas selecione as colunas que têm relevância direta para a consulta (por exemplo, colunas usadas em SELECT, WHERE, JOIN, GROUP BY, ORDER BY, funções de agregação, etc.).
Ignore colunas completamente irrelevantes para minimizar custos de tokens e processamento.

Pergunta: "{pergunta}"

Schema relevante das tabelas (RAG):
{contexto_rag}

Responda EXATAMENTE no formato JSON abaixo, mapeando o nome da tabela para uma lista com os nomes das colunas selecionadas. Não inclua blocos de código markdown (como ```json ou ```).

Exemplo de formato esperado:
{{
    "tabela1": ["coluna_a", "coluna_b"],
    "tabela2": ["coluna_c"]
}}
"""


def _exploration_selector_llm(
    estado: EstadoTextToInsight,
    llm: Any,
) -> dict:
    """
    Seleção de colunas via LLM: envia o schema RAG e a pergunta para o LLM
    e recebe de volta um JSON com as colunas relevantes por tabela.
    """
    pergunta = (
        estado.get("pergunta_atual", "")
        or estado.get("pergunta_original", "")
        or estado.get("pergunta_usuario", "")
    )
    contexto_rag = estado.get("contexto_rag_schema", "") or estado.get("contexto_schema", "")

    if not pergunta or not contexto_rag:
        return {"colunas_para_explorar": {}}

    prompt = PROMPT_EXPLORATION_SELECTOR.format(
        pergunta=pergunta,
        contexto_rag=contexto_rag,
    )

    print("[EXPLORATION_SELECTOR] Solicitando seleção de colunas ao LLM...")
    token_update: dict[str, Any] = {
        "tokens_input": 0,
        "tokens_output": 0,
        "tokens_total": 0,
        "tokens_by_node": {},
    }
    try:
        resposta_llm = llm.invoke(prompt)
        token_update = build_token_usage_update("exploration_selector", resposta_llm)
        conteudo_bruto = resposta_llm.content.strip()

        if conteudo_bruto.startswith("```json"):
            conteudo_bruto = conteudo_bruto[7:-3].strip()
        elif conteudo_bruto.startswith("```"):
            conteudo_bruto = conteudo_bruto[3:-3].strip()

        colunas_para_explorar = json.loads(conteudo_bruto)
        if not isinstance(colunas_para_explorar, dict):
            colunas_para_explorar = {}

        # Garantir que todas as listas de colunas tenham strings
        cleaned_dict = {}
        for t_name, cols in colunas_para_explorar.items():
            if isinstance(cols, list):
                cleaned_dict[t_name] = [str(c) for c in cols]
            else:
                cleaned_dict[t_name] = []

        print(f"[EXPLORATION_SELECTOR] Colunas selecionadas: {cleaned_dict}")

        return {
            "colunas_para_explorar": cleaned_dict,
            **token_update,
        }
    except Exception as e:
        print(f"[EXPLORATION_SELECTOR] Erro durante seleção de colunas (LLM): {e}")
        return {
            "colunas_para_explorar": {},
            **token_update,
        }


# ---------------------------------------------------------------------------
# RAG-based column selector
# ---------------------------------------------------------------------------

def _parse_columns_from_rag_context(contexto_rag: str) -> list[dict[str, str]]:
    """
    Extrai documentos individuais por coluna a partir do contexto RAG.

    Cada documento contém o nome da tabela, da coluna, tipo e constraints,
    formando um chunk semântico rico o suficiente para embedding.

    Returns:
        Lista de dicts com keys: 'id', 'table', 'column', 'document'
    """
    docs: list[dict[str, str]] = []
    current_table = None

    for line in contexto_rag.splitlines():
        line_stripped = line.strip()

        # Detecta início de bloco de tabela (ex: "Tabela: products")
        table_match = re.match(r"Tabela:\s*(\w+)", line_stripped)
        if table_match:
            current_table = table_match.group(1)
            continue

        # Detecta linhas de coluna (ex: "- product_id: TEXT (PK, NOT NULL)")
        col_match = re.match(r"-\s+(\w+):\s+(.+)", line_stripped)
        if col_match and current_table:
            col_name = col_match.group(1)
            col_detail = col_match.group(2)
            doc_id = f"{current_table}.{col_name}"
            # Documento semântico: inclui tabela para contexto
            document = (
                f"Tabela {current_table}, coluna {col_name}: {col_detail}"
            )
            docs.append({
                "id": doc_id,
                "table": current_table,
                "column": col_name,
                "document": document,
            })

    return docs


def _exploration_selector_rag(
    estado: EstadoTextToInsight,
    top_k: int = 15,
    cache_dir: str | Path | None = None,
) -> dict:
    """
    Seleção de colunas via RAG: indexa cada coluna do schema recuperado
    como um documento individual no ChromaDB e faz busca semântica pela
    pergunta do usuário para encontrar as colunas mais relevantes.

    Vantagens em relação ao LLM selector:
    - Zero custo de tokens LLM
    - Latência muito baixa
    - Determinístico para o mesmo schema + pergunta
    """
    import chromadb

    pergunta = (
        estado.get("pergunta_atual", "")
        or estado.get("pergunta_original", "")
        or estado.get("pergunta_usuario", "")
    )
    contexto_rag = estado.get("contexto_rag_schema", "") or estado.get("contexto_schema", "")
    print(contexto_rag)

    if not pergunta or not contexto_rag:
        return {"colunas_para_explorar": {}}

    print("[EXPLORATION_SELECTOR] Selecionando colunas via RAG...")

    # Parsear colunas do contexto RAG
    column_docs = _parse_columns_from_rag_context(contexto_rag)
    if not column_docs:
        print("[EXPLORATION_SELECTOR] Nenhuma coluna encontrada no contexto RAG.")
        return {"colunas_para_explorar": {}}

    # Criar collection efêmera com hash do contexto para evitar re-indexação
    context_hash = hashlib.md5(contexto_rag.encode("utf-8")).hexdigest()
    collection_name = f"cols_{context_hash}"

    chroma_client = chromadb.PersistentClient(path=str(diretorio_chroma(cache_dir)))
    collection = chroma_client.get_or_create_collection(name=collection_name)

    # Indexar se necessário
    if collection.count() == 0:
        ids = [d["id"] for d in column_docs]
        documents = [d["document"] for d in column_docs]
        metadatas = [{"table": d["table"], "column": d["column"]} for d in column_docs]
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        print(f"[EXPLORATION_SELECTOR] {len(ids)} colunas indexadas no RAG.")
    else:
        print(f"[EXPLORATION_SELECTOR] Usando índice de colunas existente ({collection.count()} docs).")

    # Query semântica
    n_results = min(top_k, collection.count())
    results = collection.query(
        query_texts=[pergunta],
        n_results=n_results,
    )

    # Agrupar colunas por tabela
    colunas_por_tabela: dict[str, list[str]] = {}
    if results and results.get("metadatas"):
        for meta in results["metadatas"][0]:
            table = meta["table"]
            column = meta["column"]
            colunas_por_tabela.setdefault(table, []).append(column)

    print(f"[EXPLORATION_SELECTOR] Colunas selecionadas (RAG): {colunas_por_tabela}")

    return {"colunas_para_explorar": colunas_por_tabela}


# ---------------------------------------------------------------------------
# Nó unificado do grafo – despacha para LLM ou RAG conforme o modo
# ---------------------------------------------------------------------------

def nos_nodo_exploration_selector(
    estado: EstadoTextToInsight,
    llm: Any,
    exploration_selector_mode: str = "off",
    cache_dir: str | Path | None = None,
) -> dict:
    """
    Nó que decide quais colunas de cada tabela devem ser exploradas.

    Modos:
        - "off": não faz seleção (explora todas as colunas).
        - "llm": usa o LLM para selecionar colunas relevantes.
        - "rag": usa busca semântica (ChromaDB) para selecionar colunas.
    """
    mode = exploration_selector_mode.lower()

    if mode == "off":
        return {"colunas_para_explorar": {}}

    if mode == "rag":
        return _exploration_selector_rag(estado, cache_dir=cache_dir)

    if mode == "llm":
        return _exploration_selector_llm(estado, llm)

    # Fallback: modo desconhecido → desativado
    print(f"[EXPLORATION_SELECTOR] Modo desconhecido '{mode}', desativando seleção.")
    return {"colunas_para_explorar": {}}
