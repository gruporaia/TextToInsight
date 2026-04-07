"""
Reporter de CSV para resultados de avaliação Spider.

Fornece:
- Inicializar CSV com header
- Salvar linhas de tentativas
- Gerar resumo final
"""

import csv
from datetime import datetime
from pathlib import Path
from typing import Any


class CSVReporter:
    """Gerenciador de CSV para rastreamento de tentativas."""

    HEADERS = [
        "id_exemplo",
        "tentativa_numero",
        "db_id",
        "pergunta_usuario",
        "query_ouro_spider",
        "query_agente_tentativa",
        "tempo_agente_ms",
        "veredito_critico",
        "feedback_critico_recebido",
        "erro_execucao",
        "resultado_exato_match",
        "similarity_score_sql",
    ]

    def __init__(self, filepath: str | Path):
        """
        Inicializa reporter.

        Args:
            filepath: Caminho para arquivo CSV
        """
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)

        # Inicializar CSV com headers
        with open(self.filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.HEADERS)
            writer.writeheader()

    def append_row(self, row: dict[str, Any]) -> None:
        """
        Adiciona uma linha ao CSV.

        Args:
            row: Dict com 12 chaves (id_exemplo, tentativa_numero, etc)

        Raises:
            ValueError: Se alguma chave obrigatória está faltando
        """
        # Validar chaves
        missing = set(self.HEADERS) - set(row.keys())
        if missing:
            raise ValueError(f"Chaves obrigatórias faltando: {missing}")

        with open(self.filepath, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.HEADERS)
            writer.writerow(row)

    def generate_summary(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Gera resumo estatístico dos resultados.

        Args:
            rows: Lista de linhas do CSV

        Returns:
            Dict com estatísticas
        """
        if not rows:
            return {
                "total_perguntas": 0,
                "total_tentativas": 0,
                "perguntas_aprovadas": 0,
                "taxa_aprovacao": 0.0,
                "taxa_1a_tentativa": 0.0,
                "tentativas_media": 0.0,
                "similarity_media": 0.0,
                "tempo_medio_ms": 0.0,
            }

        # Agrupar por id_exemplo
        by_exemplo = {}
        for row in rows:
            ex_id = row["id_exemplo"]
            if ex_id not in by_exemplo:
                by_exemplo[ex_id] = []
            by_exemplo[ex_id].append(row)

        total_perguntas = len(by_exemplo)
        perguntas_aprovadas = 0
        perguntas_1a_tentativa = 0
        total_tentativas = len(rows)
        similarities = []
        tempos = []

        for ex_id, tentativas in by_exemplo.items():
            # Última tentativa desta pergunta
            ultima = tentativas[-1]

            if ultima["veredito_critico"] == "aprovado":
                perguntas_aprovadas += 1

            if len(tentativas) == 1 and ultima["veredito_critico"] == "aprovado":
                perguntas_1a_tentativa += 1

            # Coletar similarity scores (de tentativas bem-sucedidas)
            for tent in tentativas:
                if tent["similarity_score_sql"]:
                    similarities.append(float(tent["similarity_score_sql"]))
                if tent["tempo_agente_ms"]:
                    tempos.append(float(tent["tempo_agente_ms"]))

        return {
            "total_perguntas": total_perguntas,
            "total_tentativas": total_tentativas,
            "perguntas_aprovadas": perguntas_aprovadas,
            "taxa_aprovacao": (
                perguntas_aprovadas / total_perguntas if total_perguntas > 0 else 0.0
            ),
            "taxa_1a_tentativa": (
                perguntas_1a_tentativa / total_perguntas if total_perguntas > 0 else 0.0
            ),
            "tentativas_media": total_tentativas / total_perguntas if total_perguntas > 0 else 0.0,
            "similarity_media": sum(similarities) / len(similarities) if similarities else 0.0,
            "tempo_medio_ms": sum(tempos) / len(tempos) if tempos else 0.0,
        }

    @staticmethod
    def generate_timestamped_filename(prefix: str = "spider_eval") -> str:
        """
        Gera nome de arquivo com timestamp.

        Args:
            prefix: Prefixo do arquivo

        Returns:
            Nome como: spider_eval_2025-04-06_14-30-45.csv
        """
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return f"{prefix}_{timestamp}.csv"
