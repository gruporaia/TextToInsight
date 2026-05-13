"""
Carregador de dados do Spider dataset.

Fornece funcionalidades para:
- Carregar exemplos de dev.json (pergunta, query_ouro, db_id)
- Fazer sampling reproducível com seed
- Filtrar por banco de dados específico
"""

import json
import random
from pathlib import Path
from typing import Any


def load_spider_dev_examples(data_dir: str = "data/spider_data/spider_data") -> list[dict[str, Any]]:
    """
    Carrega exemplos de dev.json do dataset Spider.

    Args:
        data_dir: Caminho para o diretório com dados do spider

    Returns:
        Lista de dicts com chaves: db_id, question, query

    Raises:
        FileNotFoundError: Se dev.json não existir
        json.JSONDecodeError: Se arquivo está malformado
    """
    dev_path = Path(data_dir) / "dev.json"

    if not dev_path.exists():
        raise FileNotFoundError(
            f"dev.json não encontrado em {dev_path}. "
            f"Certifique-se que está em data/spider_data/spider_data/"
        )

    with open(dev_path, "r") as f:
        examples = json.load(f)

    return examples


def sample_examples(
    examples: list[dict[str, Any]],
    sample_size: int | None = None,
    seed: int | None = None,
) -> list[dict[str, Any]]:
    """
    Faz sampling reproducível dos exemplos.

    Args:
        examples: Lista de exemplos
        sample_size: Quantos exemplos pegar (None = todos)
        seed: Seed para reproducibilidade

    Returns:
        Lista de exemplos selecionados
    """
    if seed is not None:
        random.seed(seed)

    if sample_size is None or sample_size >= len(examples):
        return examples

    return random.sample(examples, k=sample_size)


def filter_by_db_id(
    examples: list[dict[str, Any]],
    db_id: str,
) -> list[dict[str, Any]]:
    """
    Filtra exemplos por banco de dados.

    Args:
        examples: Lista de exemplos
        db_id: ID do banco (ex: concert_singer)

    Returns:
        Lista de exemplos do banco especificado
    """
    return [ex for ex in examples if ex["db_id"] == db_id]


def get_unique_db_ids(examples: list[dict[str, Any]]) -> list[str]:
    """
    Retorna lista de bancos únicos nos exemplos.

    Args:
        examples: Lista de exemplos

    Returns:
        Lista de db_ids únicos
    """
    return sorted(set(ex["db_id"] for ex in examples))
