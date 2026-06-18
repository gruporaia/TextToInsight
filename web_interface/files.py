"""Servir com seguranca os artefatos gerados pelo engine (CSV e graficos).

O dict normalizado traz caminhos absolutos para `csv.path` (em `results/`) e
`chart.path` (em `graphs/`). Em vez de confiar no caminho vindo do cliente, o
servidor resolve SO pelo basename dentro do diretorio permitido -- elimina path
traversal. Os diretorios espelham os definidos nos nos do engine:
`text_to_insight/nodes/csv_saver.py` (RESULTS_DIR) e
`text_to_insight/nodes/graph_generator.py` (GRAPHS_DIR).
"""

from __future__ import annotations

from pathlib import Path

# Raiz do projeto = pai do pacote `text_to_insight` (mesma convencao dos nos).
_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = _ROOT / "results"
GRAPHS_DIR = _ROOT / "graphs"

_DIRS = {"csv": RESULTS_DIR, "chart": GRAPHS_DIR}


def resolve(kind: str, name: str) -> Path | None:
    """Resolve um artefato pelo basename dentro do diretorio do `kind`.

    Devolve o Path se existir e for um arquivo sob o diretorio permitido; senao
    None (rota responde 404). `Path(name).name` descarta qualquer componente de
    diretorio, entao "../../etc/passwd" vira "passwd".
    """
    base = _DIRS.get(kind)
    if base is None:
        return None
    alvo = (base / Path(name).name).resolve()
    try:
        alvo.relative_to(base.resolve())
    except ValueError:
        return None
    if not alvo.is_file():
        return None
    return alvo
