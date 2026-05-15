"""
Métricas para comparação de queries SQL.

Fornece:
- Similarity score entre duas queries (difflib-based)
- Comparação de resultados no estilo Spider 2.0 (column-level matching)
- F1 score de resultados (column-level precision/recall)
- Normalização de SQL para comparação

A lógica de comparação segue a métrica oficial do Spider 2.0:
- Transpõe ambas as tabelas para obter vetores-coluna
- Para cada coluna do gold, verifica se alguma coluna do pred bate
- Usa tolerância de 1e-2 para comparações numéricas
- Trata NaN/None com pd.isna
"""

import difflib
import math
import re
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
_TOLERANCE = 1e-2


# ---------------------------------------------------------------------------
# Normalização de SQL
# ---------------------------------------------------------------------------
def normalize_sql(sql: str) -> str:
    """
    Normaliza SQL para comparação mais robusta.

    - Remove espaços extras
    - Converte para upper case
    - Remove comentários
    - Remove trailing semicolon

    Args:
        sql: SQL a normalizar

    Returns:
        SQL normalizado
    """
    # Remove comentários de linha
    sql = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)

    # Remove comentários de bloco
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)

    # Remove trailing semicolon
    sql = sql.rstrip("; \n\t")

    # Uppercase
    sql = sql.upper()

    # Remove espaços múltiplos
    sql = re.sub(r"\s+", " ", sql).strip()

    return sql


def sql_similarity_score(sql1: str, sql2: str) -> float:
    """
    Calcula similarity score entre dois SQLs usando SequenceMatcher.

    Args:
        sql1: Primeira query
        sql2: Segunda query

    Returns:
        Score de 0 a 1 (1 = idênticos)
    """
    norm1 = normalize_sql(sql1)
    norm2 = normalize_sql(sql2)

    # Se ambas vazias, considerar idênticas
    if not norm1 and not norm2:
        return 1.0

    # Se uma vazia e outra não, completamente diferentes
    if not norm1 or not norm2:
        return 0.0

    matcher = difflib.SequenceMatcher(None, norm1, norm2)
    return matcher.ratio()


# ---------------------------------------------------------------------------
# Helpers internos — lógica Spider 2.0
# ---------------------------------------------------------------------------
def _vectors_match(v1: list, v2: list, tol: float = _TOLERANCE, ignore_order: bool = False) -> bool:
    """
    Compara dois vetores (colunas transpostas) elemento a elemento,
    seguindo a lógica oficial do Spider 2.0.

    - Aceita tolerância absoluta ``tol`` para pares numéricos.
    - Trata ``pd.isna`` como iguais entre si.
    - Se ``ignore_order`` estiver ativado, ordena ambos os vetores antes
      de comparar.
    """
    if ignore_order:
        v1 = sorted(v1, key=lambda x: (x is None, str(x), isinstance(x, (int, float))))
        v2 = sorted(v2, key=lambda x: (x is None, str(x), isinstance(x, (int, float))))

    if len(v1) != len(v2):
        return False

    for a, b in zip(v1, v2):
        if pd.isna(a) and pd.isna(b):
            continue
        elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if not math.isclose(float(a), float(b), abs_tol=tol):
                return False
        elif a != b:
            return False
    return True


def _results_to_dataframe(results: list[dict[str, Any]]) -> pd.DataFrame:
    """Converte list[dict] (formato do SpiderQueryExecutor) para DataFrame."""
    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results)


# ---------------------------------------------------------------------------
# Comparação principal — Spider 2.0
# ---------------------------------------------------------------------------
def compare_pandas_table(
    pred: pd.DataFrame,
    gold: pd.DataFrame,
    condition_cols: list[int] | None = None,
    ignore_order: bool = False,
) -> int:
    """
    Compara pred vs gold seguindo a métrica oficial do Spider 2.0.

    Para cada coluna do gold (opcionalmente filtrada por ``condition_cols``),
    verifica se **alguma** coluna do pred é equivalente (dentro de tolerância
    numérica e tratando NaN).

    Args:
        pred: DataFrame com resultados do agente.
        gold: DataFrame com resultados esperados.
        condition_cols: Índices das colunas do gold a avaliar.
                        Se ``None`` ou vazio, avalia todas.
        ignore_order: Se True, ordena os valores de cada coluna antes
                      de comparar (útil quando não há ORDER BY).

    Returns:
        1 se todas as colunas do gold foram encontradas no pred, 0 caso contrário.
    """
    if condition_cols:
        gold_cols = gold.iloc[:, condition_cols]
    else:
        gold_cols = gold

    t_gold_list = gold_cols.transpose().values.tolist()
    t_pred_list = pred.transpose().values.tolist()

    for gold_vec in t_gold_list:
        if not any(_vectors_match(gold_vec, pred_vec, ignore_order=ignore_order)
                   for pred_vec in t_pred_list):
            return 0
    return 1


def compare_multi_pandas_table(
    pred: pd.DataFrame,
    multi_gold: list[pd.DataFrame],
    multi_condition_cols: list | None = None,
    multi_ignore_order: bool = False,
) -> int:
    """
    Compara pred contra *múltiplas* respostas ouro válidas (Spider 2.0).

    Retorna 1 se o pred bater com **pelo menos uma** das respostas ouro.

    Args:
        pred: DataFrame com resultados do agente.
        multi_gold: Lista de DataFrames de respostas ouro.
        multi_condition_cols: Lista de listas de índices, uma por gold.
        multi_ignore_order: Se True, aplica ignore_order em todas.

    Returns:
        1 se match com alguma resposta ouro, 0 caso contrário.
    """
    if (
        multi_condition_cols is None
        or multi_condition_cols == []
        or multi_condition_cols == [[]]
        or multi_condition_cols == [None]
    ):
        multi_condition_cols = [[] for _ in range(len(multi_gold))]
    elif len(multi_gold) > 1 and not all(isinstance(s, list) for s in multi_condition_cols):
        multi_condition_cols = [multi_condition_cols for _ in range(len(multi_gold))]

    for i, gold in enumerate(multi_gold):
        if compare_pandas_table(pred, gold, multi_condition_cols[i], multi_ignore_order):
            return 1
    return 0


# ---------------------------------------------------------------------------
# Funções de interface pública (mantêm assinatura list[dict])
# ---------------------------------------------------------------------------
def results_exact_match(
    results_gold: list[dict[str, Any]],
    results_agent: list[dict[str, Any]],
    ignore_order: bool = True,
) -> bool:
    """
    Compara se dois conjuntos de resultados são equivalentes usando
    a métrica oficial do Spider 2.0 (column-level matching).

    Ignora nomes de colunas — apenas os *valores* das colunas importam.
    Usa tolerância de 1e-2 para números e trata None/NaN como iguais.

    Args:
        results_gold: Resultados da query ouro (list[dict]).
        results_agent: Resultados da query do agente (list[dict]).
        ignore_order: Se True (padrão), a ordem das linhas é ignorada.

    Returns:
        True se todas as colunas do gold foram encontradas no pred.
    """
    # Ambos vazios
    if not results_gold and not results_agent:
        return True

    # Um vazio e outro não
    if not results_gold or not results_agent:
        return False

    gold_df = _results_to_dataframe(results_gold)
    pred_df = _results_to_dataframe(results_agent)

    # Número de linhas diferente → impossível match
    if len(gold_df) != len(pred_df):
        return False

    return compare_pandas_table(pred_df, gold_df, ignore_order=ignore_order) == 1


def results_f1_score(
    results_gold: list[dict[str, Any]],
    results_agent: list[dict[str, Any]],
    ignore_order: bool = True,
) -> dict[str, float]:
    """
    Calcula Precision, Recall e F1 a nível de coluna, seguindo a lógica
    do Spider 2.0.

    Para cada coluna do gold, verifica se alguma coluna do pred é
    equivalente (tolerância numérica de 1e-2, NaN-aware).

    - Precision: das colunas que o agente retornou, quantas batem com
      alguma coluna do gold?
    - Recall: das colunas do gold, quantas foram cobertas pelo agente?
    - F1: média harmônica de precision e recall.

    Quando o número de linhas difere, as linhas excedentes são tratadas
    como colunas não-matching, penalizando precision ou recall conforme
    o caso.

    Args:
        results_gold: Resultados da query ouro (list[dict]).
        results_agent: Resultados da query do agente (list[dict]).
        ignore_order: Se True (padrão), a ordem das linhas é ignorada.

    Returns:
        Dict com chaves: precision, recall, f1 (floats de 0 a 1).
    """
    # Ambos vazios → match perfeito
    if not results_gold and not results_agent:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}

    # Um vazio e outro não
    if not results_gold:
        return {"precision": 0.0, "recall": 1.0, "f1": 0.0}
    if not results_agent:
        return {"precision": 1.0, "recall": 0.0, "f1": 0.0}

    gold_df = _results_to_dataframe(results_gold)
    pred_df = _results_to_dataframe(results_agent)

    # Número de linhas diferente — padroniza para o mesmo tamanho usando NaN
    # para viabilizar a comparação coluna-a-coluna. As colunas onde os NaNs
    # extras forem injetados não vão bater, o que penaliza corretamente.
    max_rows = max(len(gold_df), len(pred_df))
    if len(gold_df) < max_rows:
        padding = pd.DataFrame(
            [[None] * gold_df.shape[1]] * (max_rows - len(gold_df)),
            columns=gold_df.columns,
        )
        gold_df = pd.concat([gold_df, padding], ignore_index=True)
    if len(pred_df) < max_rows:
        padding = pd.DataFrame(
            [[None] * pred_df.shape[1]] * (max_rows - len(pred_df)),
            columns=pred_df.columns,
        )
        pred_df = pd.concat([pred_df, padding], ignore_index=True)

    t_gold_list = gold_df.transpose().values.tolist()
    t_pred_list = pred_df.transpose().values.tolist()

    total_gold = len(t_gold_list)
    total_pred = len(t_pred_list)

    # Recall: quantas colunas do gold batem com alguma coluna do pred?
    gold_matched = sum(
        1 for g in t_gold_list
        if any(_vectors_match(g, p, ignore_order=ignore_order) for p in t_pred_list)
    )

    # Precision: quantas colunas do pred batem com alguma coluna do gold?
    pred_matched = sum(
        1 for p in t_pred_list
        if any(_vectors_match(p, g, ignore_order=ignore_order) for g in t_gold_list)
    )

    recall = gold_matched / total_gold if total_gold > 0 else 0.0
    precision = pred_matched / total_pred if total_pred > 0 else 0.0

    if precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * (precision * recall) / (precision + recall)

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


# ---------------------------------------------------------------------------
# Construtor de linha para CSV de avaliação
# ---------------------------------------------------------------------------
def build_comparison_row(
    id_exemplo: int,
    tentativa_numero: int,
    db_id: str,
    pergunta: str,
    query_ouro: str,
    query_agente: str,
    tempo_agente_ms: float,
    veredito_critico: str,
    feedback_critico: str,
    erro_execucao: str,
    resultado_exato_match: bool | None,
    similarity_score: float,
    resultado_f1: float = 0.0,
    resultado_precision: float = 0.0,
    resultado_recall: float = 0.0,
) -> dict[str, Any]:
    """
    Constrói uma linha para o CSV de avaliação.

    Args:
        id_exemplo: ID sequencial da pergunta
        tentativa_numero: Qual tentativa (1, 2, 3...)
        db_id: Banco de dados
        pergunta: Pergunta em linguagem natural
        query_ouro: Query padrão do spider
        query_agente: Query gerada pelo agente NESTA tentativa
        tempo_agente_ms: Tempo de execução em ms
        veredito_critico: "aprovado" / "reprovado" / "erro"
        feedback_critico: Feedback recebido (ou "Aprovado" se aprovado)
        erro_execucao: Mensagem de erro (vazio se OK)
        resultado_exato_match: True/False se resultado foi exato (None se erro)
        similarity_score: Score 0-1
        resultado_f1: F1 score row-level (0-1)
        resultado_precision: Precision row-level (0-1)
        resultado_recall: Recall row-level (0-1)

    Returns:
        Dict com 15 chaves para CSV
    """
    return {
        "id_exemplo": id_exemplo,
        "tentativa_numero": tentativa_numero,
        "db_id": db_id,
        "pergunta_usuario": pergunta,
        "query_ouro_spider": query_ouro,
        "query_agente_tentativa": query_agente,
        "tempo_agente_ms": round(tempo_agente_ms, 2),
        "veredito_critico": veredito_critico,
        "feedback_critico_recebido": feedback_critico,
        "erro_execucao": erro_execucao,
        "resultado_exato_match": resultado_exato_match if resultado_exato_match is not None else "",
        "similarity_score_sql": round(similarity_score, 4),
        "resultado_f1": resultado_f1,
        "resultado_precision": resultado_precision,
        "resultado_recall": resultado_recall,
    }

