"""
Métricas para comparação de queries SQL.

Fornece:
- Similarity score entre duas queries (difflib-based)
- Comparação de resultados (exato match)
- Normalização de SQL para comparação
"""

import difflib
import re
from typing import Any


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


def results_exact_match(
    results_gold: list[dict[str, Any]],
    results_agent: list[dict[str, Any]],
) -> bool:
    """
    Compara se dois conjuntos de resultados são exatamente iguais.

    Compara:
    - Número de linhas
    - Valores de cada linha (insensível a ordem das colunas)

    Args:
        results_gold: Resultados da query ouro
        results_agent: Resultados da query do agente

    Returns:
        True se resultados são iguais
    """
    if len(results_gold) != len(results_agent):
        return False

    # Converter dicts para conjuntos de tuplas para comparação
    # (para serem agnósticos à ordem das colunas)
    def result_set(results: list[dict[str, Any]]) -> set:
        converted = []
        for row in results:
            # Converter valores para strings para lidar com tipos diferentes
            items = []
            for k in sorted(row.keys()):
                # Normalizar None/NULL
                v = row[k]
                if v is None:
                    v = "NULL"
                items.append((k, str(v)))
            converted.append(tuple(items))
        return set(converted)

    return result_set(results_gold) == result_set(results_agent)


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

    Returns:
        Dict com 12 chaves para CSV
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
    }
