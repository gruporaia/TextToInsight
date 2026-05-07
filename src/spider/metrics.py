"""
Métricas para comparação de queries SQL.

Fornece:
- Similarity score entre duas queries (difflib-based)
- Comparação de resultados (exato match)
- F1 score de resultados (row-level precision/recall)
- Normalização de SQL para comparação
"""

import difflib
import re
from collections import Counter
from typing import Any

import numpy as np


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


# def results_exact_match(
#     results_gold: list[dict[str, Any]],
#     results_agent: list[dict[str, Any]],
# ) -> bool:
#     """
#     Compara se dois conjuntos de resultados são exatamente iguais.

#     Compara:
#     - Número de linhas
#     - Valores de cada linha (insensível a ordem das colunas)

#     Args:
#         results_gold: Resultados da query ouro
#         results_agent: Resultados da query do agente

#     Returns:
#         True se resultados são iguais
#     """
#     if len(results_gold) != len(results_agent):
#         return False

#     # Converter dicts para conjuntos de tuplas para comparação
#     # (para serem agnósticos à ordem das colunas)
#     def result_set(results: list[dict[str, Any]]) -> set:
#         converted = []
#         for row in results:
#             # Converter valores para strings para lidar com tipos diferentes
#             items = []
#             for k in sorted(row.keys()):
#                 # Normalizar None/NULL
#                 v = row[k]
#                 if v is None:
#                     v = "NULL"
#                 items.append((k, str(v)))
#             converted.append(tuple(items))
#         return set(converted)

#     return result_set(results_gold) == result_set(results_agent)

def results_exact_match(
    results_gold: list[dict[str, Any]],
    results_agent: list[dict[str, Any]],
) -> bool:
    """
    Compara se dois conjuntos de resultados são iguais baseando-se APENAS nos valores.
    Ignora os nomes das colunas e a ordem das linhas.
    """
    # Se não têm o mesmo número de linhas, já é False
    if len(results_gold) != len(results_agent):
        return False
        
    # Se as duas listas vierem vazias (0 linhas), é True
    if not results_gold:
        return True

    def extract_values_to_numpy(results: list[dict[str, Any]]) -> np.ndarray:
        matrix = []
        for row in results:
            # Pega APENAS os valores, ignora as chaves
            # Converte tudo para string (evita falsos negativos entre 0 inteiro e 0.0 float)
            row_values = [str(v) if v is not None else "NULL" for v in row.values()]
            matrix.append(row_values)
            
        # Converte a matriz nativa do Python para um Array NumPy
        arr = np.array(matrix)
        
        # Como as queries podem retornar as linhas em ordens diferentes (se não houver ORDER BY),
        # precisamos ordenar as linhas do array numpy lexograficamente para uma comparação justa.
        # np.lexsort ordena pelas colunas, da última para a primeira, então passamos transposto e invertido
        sorted_indices = np.lexsort(arr.T[::-1])
        return arr[sorted_indices]

    # Extrai, processa e ordena os arrays
    gold_array = extract_values_to_numpy(results_gold)
    agent_array = extract_values_to_numpy(results_agent)

    # np.array_equal compara a estrutura (dimensões) e o conteúdo.
    # Usamos bool() para garantir que retorne um booleano nativo do Python e não um np.bool_
    return bool(np.array_equal(gold_array, agent_array))


def results_f1_score(
    results_gold: list[dict[str, Any]],
    results_agent: list[dict[str, Any]],
) -> dict[str, float]:
    """
    Calcula Precision, Recall e F1 row-level entre resultados gold e agent.

    Cada linha é convertida em uma tupla canônica (valores ordenados, como string)
    e tratada como membro de um multiset (Counter). Isso permite medir parcialmente
    quantas linhas o agente acertou, mesmo que não tenha acertado todas.

    - Precision: das linhas que o agente retornou, quantas estão no gold?
    - Recall:    das linhas do gold, quantas o agente retornou?
    - F1:        média harmônica de precision e recall.

    Args:
        results_gold: Resultados da query ouro
        results_agent: Resultados da query do agente

    Returns:
        Dict com chaves: precision, recall, f1 (floats de 0 a 1)
    """
    def _row_to_canonical(row: dict[str, Any]) -> tuple:
        """Converte uma linha em tupla canônica de valores (ordenados, stringificados)."""
        values = [str(v) if v is not None else "NULL" for v in row.values()]
        return tuple(sorted(values))

    # Ambos vazios → match perfeito
    if not results_gold and not results_agent:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}

    # Um vazio e outro não
    if not results_gold:
        return {"precision": 0.0, "recall": 1.0, "f1": 0.0}
    if not results_agent:
        return {"precision": 1.0, "recall": 0.0, "f1": 0.0}

    gold_bag = Counter(_row_to_canonical(r) for r in results_gold)
    agent_bag = Counter(_row_to_canonical(r) for r in results_agent)

    # Interseção: min(count_gold, count_agent) para cada tupla
    true_positives = sum((gold_bag & agent_bag).values())
    total_agent = sum(agent_bag.values())
    total_gold = sum(gold_bag.values())

    precision = true_positives / total_agent if total_agent > 0 else 0.0
    recall = true_positives / total_gold if total_gold > 0 else 0.0

    if precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * (precision * recall) / (precision + recall)

    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


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

