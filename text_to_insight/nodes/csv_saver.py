"""
Nó Salvar CSV do grafo de agentes Text-to-Insight.

Responsabilidade única: salvar o resultado da query em um CSV em local padrão
e armazenar o caminho no estado para uso posterior (ex: geração de gráficos).
"""

import csv
from datetime import datetime
from pathlib import Path

from ..state import EstadoTextToInsight

# Diretório padrão para salvar os resultados CSV
RESULTS_DIR = Path(__file__).parent.parent.parent / "results"


def nos_nodo_salvar_csv(estado: EstadoTextToInsight) -> dict:
    """
    Nó Salvar CSV: exporta linhas_resultado_completo para um arquivo CSV
    e registra o caminho no estado.
    """
    linhas = estado.get("linhas_resultado_completo", []) or []

    if not linhas:
        print("[SALVAR_CSV] Nenhuma linha para exportar — pulando.")
        return {"caminho_csv_resultado": ""}

    RESULTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    csv_path = RESULTS_DIR / f"query_{timestamp}.csv"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(linhas[0].keys()))
        writer.writeheader()
        writer.writerows(linhas)

    caminho_absoluto = str(csv_path.resolve())
    total = len(linhas)
    print(f"[SALVAR_CSV] {total} linhas salvas em: {caminho_absoluto}")

    return {"caminho_csv_resultado": caminho_absoluto}
