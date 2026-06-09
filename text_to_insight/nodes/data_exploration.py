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
import random
import re
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..state import EstadoTextToInsight
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
_DATE_TYPES = re.compile(r"(date|datetime|timestamp|time)", re.IGNORECASE)
_BOOL_TYPES = re.compile(r"(bool|boolean)", re.IGNORECASE)


def _classify_column_type(declared_type: str) -> str:
    """Classifica o tipo declarado SQLite em uma das 4 categorias."""
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


def _safe_float(val: Any) -> float | None:
    """Tenta converter um valor para float de forma segura."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_round(val: Any, ndigits: int = 4) -> float | None:
    """Tenta converter para float e arredonda de forma segura."""
    f_val = _safe_float(val)
    if f_val is not None:
        return round(f_val, ndigits)
    return None


def _compute_numeric_stats(
    cursor: sqlite3.Cursor, table: str, column: str, total_rows: int,
) -> NumericColumnStats:
    """Calcula estatísticas numéricas usando SQL aggregates + amostragem para mediana."""
    safe_col = f'"{column}"'
    safe_tbl = f'"{table}"'

    cursor.execute(
        f"SELECT AVG({safe_col}), MIN({safe_col}), MAX({safe_col}), "
        f"COUNT(*) - COUNT({safe_col}) "
        f"FROM (SELECT {safe_col} FROM {safe_tbl} LIMIT {SAMPLE_LIMIT})"
    )
    row = cursor.fetchone()
    avg_val, min_val, max_val, null_count = row

    sample_size = min(total_rows, SAMPLE_LIMIT)
    null_rate = round(null_count / sample_size, 4) if sample_size > 0 else 0.0

    # std_dev via SQL (population std dev na amostra)
    std_dev = None
    avg_float = _safe_float(avg_val)
    if avg_float is not None:
        try:
            cursor.execute(
                f"SELECT AVG(({safe_col} - {avg_float}) * ({safe_col} - {avg_float})) "
                f"FROM (SELECT {safe_col} FROM {safe_tbl} WHERE {safe_col} IS NOT NULL LIMIT {SAMPLE_LIMIT})"
            )
            variance = cursor.fetchone()[0]
            var_float = _safe_float(variance)
            if var_float is not None and var_float >= 0:
                std_dev = round(var_float ** 0.5, 4)
        except Exception:
            std_dev = None

    # mediana aproximada via ORDER BY + LIMIT/OFFSET na amostra
    median_val = None
    try:
        cursor.execute(
            f"SELECT COUNT(*) FROM (SELECT {safe_col} FROM {safe_tbl} WHERE {safe_col} IS NOT NULL LIMIT {SAMPLE_LIMIT})"
        )
        non_null_count = cursor.fetchone()[0]
        if non_null_count > 0:
            mid = non_null_count // 2
            cursor.execute(
                f"SELECT {safe_col} FROM {safe_tbl} WHERE {safe_col} IS NOT NULL "
                f"ORDER BY {safe_col} LIMIT 1 OFFSET {mid}"
            )
            median_row = cursor.fetchone()
            if median_row:
                median_val = median_row[0]
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


def _compute_categorical_stats(
    cursor: sqlite3.Cursor, table: str, column: str, total_rows: int,
) -> CategoricalColumnStats:
    """Calcula top-5, cardinalidade e amostras para colunas categóricas."""
    safe_col = f'"{column}"'
    safe_tbl = f'"{table}"'

    # Top 5 valores mais frequentes
    cursor.execute(
        f"SELECT {safe_col}, COUNT(*) as cnt "
        f"FROM (SELECT {safe_col} FROM {safe_tbl} LIMIT {SAMPLE_LIMIT}) "
        f"WHERE {safe_col} IS NOT NULL "
        f"GROUP BY {safe_col} ORDER BY cnt DESC LIMIT 5"
    )
    top_values = [{"value": str(r[0]), "count": r[1]} for r in cursor.fetchall()]

    # Cardinalidade
    cursor.execute(
        f"SELECT COUNT(DISTINCT {safe_col}) "
        f"FROM (SELECT {safe_col} FROM {safe_tbl} LIMIT {SAMPLE_LIMIT})"
    )
    cardinality = cursor.fetchone()[0] or 0

    # Null rate
    sample_size = min(total_rows, SAMPLE_LIMIT)
    cursor.execute(
        f"SELECT COUNT(*) - COUNT({safe_col}) "
        f"FROM (SELECT {safe_col} FROM {safe_tbl} LIMIT {SAMPLE_LIMIT})"
    )
    null_count = cursor.fetchone()[0]
    null_rate = round(null_count / sample_size, 4) if sample_size > 0 else 0.0

    # 3 amostras aleatórias (via random offset para evitar viés de posição)
    sample_values: list[str] = []
    cursor.execute(
        f"SELECT DISTINCT {safe_col} FROM {safe_tbl} "
        f"WHERE {safe_col} IS NOT NULL LIMIT 50"
    )
    distinct_pool = [str(r[0]) for r in cursor.fetchall()]
    if distinct_pool:
        sample_values = random.sample(distinct_pool, min(3, len(distinct_pool)))

    return CategoricalColumnStats(
        top_values=top_values,
        cardinality=cardinality,
        null_rate=null_rate,
        sample_values=sample_values,
    )


def _compute_date_stats(
    cursor: sqlite3.Cursor, table: str, column: str, total_rows: int,
) -> DateColumnStats:
    """Calcula min, max e amostras para colunas de data."""
    safe_col = f'"{column}"'
    safe_tbl = f'"{table}"'

    cursor.execute(
        f"SELECT MIN({safe_col}), MAX({safe_col}), "
        f"COUNT(*) - COUNT({safe_col}) "
        f"FROM (SELECT {safe_col} FROM {safe_tbl} LIMIT {SAMPLE_LIMIT})"
    )
    row = cursor.fetchone()
    min_val, max_val, null_count = row

    sample_size = min(total_rows, SAMPLE_LIMIT)
    null_rate = round(null_count / sample_size, 4) if sample_size > 0 else 0.0

    # 3 amostras para inferir formato
    sample_values: list[str] = []
    cursor.execute(
        f"SELECT DISTINCT {safe_col} FROM {safe_tbl} "
        f"WHERE {safe_col} IS NOT NULL LIMIT 30"
    )
    pool = [str(r[0]) for r in cursor.fetchall()]
    if pool:
        sample_values = random.sample(pool, min(3, len(pool)))

    return DateColumnStats(
        min=str(min_val) if min_val is not None else None,
        max=str(max_val) if max_val is not None else None,
        sample_values=sample_values,
        null_rate=null_rate,
    )


def _compute_boolean_stats(
    cursor: sqlite3.Cursor, table: str, column: str, total_rows: int,
) -> BooleanColumnStats:
    """Calcula contagens true/false para colunas booleanas."""
    safe_col = f'"{column}"'
    safe_tbl = f'"{table}"'

    cursor.execute(
        f"SELECT "
        f"SUM(CASE WHEN {safe_col} = 1 OR LOWER(CAST({safe_col} AS TEXT)) = 'true' THEN 1 ELSE 0 END), "
        f"SUM(CASE WHEN {safe_col} = 0 OR LOWER(CAST({safe_col} AS TEXT)) = 'false' THEN 1 ELSE 0 END), "
        f"COUNT(*) - COUNT({safe_col}) "
        f"FROM (SELECT {safe_col} FROM {safe_tbl} LIMIT {SAMPLE_LIMIT})"
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
    conn: sqlite3.Connection,
    table_name: str,
    columns_to_explore: list[str] | None = None,
) -> TableExplorationResult:
    """
    Calcula estatísticas por coluna para uma tabela SQLite.

    Args:
        conn: Conexão SQLite (preferencialmente read-only).
        table_name: Nome da tabela a explorar.
        columns_to_explore: Lista opcional de colunas a explorar. Se omitida (ou None), explora todas.

    Returns:
        TableExplorationResult com stats por coluna.
    """
    cursor = conn.cursor()

    # Contagem total de linhas (estimativa rápida)
    safe_tbl = f'"{table_name}"'
    cursor.execute(f"SELECT COUNT(*) FROM {safe_tbl}")
    total_rows = cursor.fetchone()[0]

    # Introspect colunas
    cursor.execute(f"PRAGMA table_info({safe_tbl})")
    columns_info = cursor.fetchall()
    # table_info retorna: cid, name, type, notnull, dflt_value, pk

    result = TableExplorationResult(
        table_name=table_name,
        row_count_estimate=total_rows,
    )

    for col_info in columns_info:
        col_name = col_info[1]
        if columns_to_explore is not None and col_name not in columns_to_explore:
            continue
        col_type_raw = col_info[2] or ""
        category = _classify_column_type(col_type_raw)

        compute_fn = _STAT_DISPATCHER[category]
        try:
            stats = compute_fn(cursor, table_name, col_name, total_rows)
        except Exception as e:
            # Se falhar em uma coluna, log e segue para as demais
            print(f"[DATA_EXPLORATION] Erro ao computar stats para {table_name}.{col_name}: {e}")
            continue

        result.columns[col_name] = stats

    return result


def explore_tables(
    db_path: str,
    table_names: list[str],
    colunas_para_explorar: dict[str, list[str]] | None = None,
) -> dict[str, TableExplorationResult]:
    """
    Explora múltiplas tabelas e retorna dict indexado por nome de tabela.

    Args:
        db_path: Caminho para o arquivo SQLite.
        table_names: Lista de nomes de tabelas a explorar.
        colunas_para_explorar: Dicionário opcional mapeando tabela para lista de colunas a explorar.

    Returns:
        Dict[table_name, TableExplorationResult]
    """
    caminho = Path(db_path)
    if not caminho.exists():
        print(f"[DATA_EXPLORATION] Banco não encontrado: {db_path}")
        return {}

    conn = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
    try:
        results: dict[str, TableExplorationResult] = {}
        for table_name in table_names:
            try:
                cols_to_explore = None
                if colunas_para_explorar and table_name in colunas_para_explorar:
                    cols_to_explore = colunas_para_explorar[table_name]
                results[table_name] = explore_table(conn, table_name, cols_to_explore)
            except Exception as e:
                print(f"[DATA_EXPLORATION] Erro ao explorar tabela '{table_name}': {e}")
                continue
        return results
    finally:
        conn.close()


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

    parts = ["=== DATA EXPLORATION (estatísticas amostrais) ===\n"]

    for table_name, table_result in exploration_results.items():
        parts.append(f"Tabela: {table_name} (~{table_result.row_count_estimate} linhas)")

        for col_name, stats in table_result.columns.items():
            if isinstance(stats, NumericColumnStats):
                parts.append(
                    f"  {col_name} [numeric]: "
                    f"mean={stats.mean}, median={stats.median}, std={stats.std_dev}, "
                    f"min={stats.min}, max={stats.max}, null_rate={stats.null_rate}"
                )
            elif isinstance(stats, CategoricalColumnStats):
                top_str = ", ".join(
                    f"{v['value']}({v['count']})" for v in stats.top_values
                )
                samples_str = ", ".join(f'"{s}"' for s in stats.sample_values)
                parts.append(
                    f"  {col_name} [categorical]: "
                    f"cardinality={stats.cardinality}, null_rate={stats.null_rate}, "
                    f"top=[{top_str}], samples=[{samples_str}]"
                )
            elif isinstance(stats, DateColumnStats):
                samples_str = ", ".join(f'"{s}"' for s in stats.sample_values)
                parts.append(
                    f"  {col_name} [date]: "
                    f"min={stats.min}, max={stats.max}, "
                    f"null_rate={stats.null_rate}, samples=[{samples_str}]"
                )
            elif isinstance(stats, BooleanColumnStats):
                parts.append(
                    f"  {col_name} [boolean]: "
                    f"true={stats.true_count}, false={stats.false_count}, "
                    f"null_rate={stats.null_rate}"
                )
        parts.append("")  # Linha em branco entre tabelas

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Extração de nomes de tabelas do contexto RAG
# ---------------------------------------------------------------------------

def _extract_table_names_from_rag_context(contexto_rag: str) -> list[str]:
    """
    Extrai nomes de tabelas do texto formatado pelo retriever RAG.

    O retriever formata cada tabela como 'Tabela: <nome>', então
    extraímos todos os nomes com regex.
    """
    return re.findall(r"Tabela:\s*(\w+)", contexto_rag)


# ---------------------------------------------------------------------------
# Nó do grafo LangGraph
# ---------------------------------------------------------------------------

def nos_nodo_data_exploration(
    estado: EstadoTextToInsight,
    use_data_exploration: bool = True,
) -> dict:
    """
    Nó Data Exploration: calcula estatísticas por coluna para as tabelas
    identificadas pelo retriever RAG.

    Roda após o retriever e antes do planejador, injetando as estatísticas
    no campo `contexto_data_exploration` do estado.
    """
    if not use_data_exploration:
        print("[DATA_EXPLORATION] Data exploration desativada via toggle.")
        return {"contexto_data_exploration": ""}

    contexto_rag = estado.get("contexto_rag_schema", "")
    db_path = estado.get("db_path", "").strip()

    if not contexto_rag or not db_path:
        print("[DATA_EXPLORATION] Sem contexto RAG ou db_path — pulando exploração.")
        return {}

    # Extrair nomes de tabelas do contexto do retriever
    table_names = _extract_table_names_from_rag_context(contexto_rag)
    if not table_names:
        print("[DATA_EXPLORATION] Nenhuma tabela encontrada no contexto RAG.")
        return {}

    print(f"[DATA_EXPLORATION] Explorando {len(table_names)} tabela(s): {table_names}")

    colunas_para_explorar = estado.get("colunas_para_explorar", None)
    exploration_results = explore_tables(db_path, table_names, colunas_para_explorar)

    # Formatar para injeção no prompt
    exploration_text = format_exploration_for_prompt(exploration_results)

    print(
        f"[DATA_EXPLORATION] Estatísticas computadas: "
        f"{len(exploration_text)} chars (~{len(exploration_text) // 4} tokens)"
    )

    return {"contexto_data_exploration": exploration_text}


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
    contexto_rag = estado.get("contexto_rag_schema", "")

    if not pergunta or not contexto_rag:
        return {"colunas_para_explorar": {}}

    prompt = PROMPT_EXPLORATION_SELECTOR.format(
        pergunta=pergunta,
        contexto_rag=contexto_rag,
    )

    print("[EXPLORATION_SELECTOR] Solicitando seleção de colunas ao LLM...")
    try:
        resposta_llm = llm.invoke(prompt)
        in_tokens, out_tokens, total_tokens = extrair_tokens(resposta_llm)
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
            "tokens_input": in_tokens,
            "tokens_output": out_tokens,
            "tokens_total": total_tokens,
        }
    except Exception as e:
        print(f"[EXPLORATION_SELECTOR] Erro durante seleção de colunas (LLM): {e}")
        return {
            "colunas_para_explorar": {},
            "tokens_input": 0,
            "tokens_output": 0,
            "tokens_total": 0,
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
    contexto_rag = estado.get("contexto_rag_schema", "")
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

    chroma_dir = Path(__file__).resolve().parent.parent / "retriever" / "chroma_db"
    chroma_client = chromadb.PersistentClient(path=str(chroma_dir))
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
        return _exploration_selector_rag(estado)

    if mode == "llm":
        return _exploration_selector_llm(estado, llm)

    # Fallback: modo desconhecido → desativado
    print(f"[EXPLORATION_SELECTOR] Modo desconhecido '{mode}', desativando seleção.")
    return {"colunas_para_explorar": {}}
