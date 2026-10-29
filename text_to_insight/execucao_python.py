"""

Executar isoladamente scripts de python gerados pela biblioteca

O script roda num subprocesso dentro de uma pasta temporária e própria
A devolução do resultado continua seguindo o padrão de formato do executor

"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

NOME_SCRIPT = "script.py"


def _resultado(ok: bool, erro: str, saida: str) -> dict[str, Any]:
    """Monta dicionário de retorno"""
    return {"ok": ok, "erro_execucao": erro, "saida_terminal": saida}


def executar_python_isolado(script: str, timeout_s: float = 30.0) -> dict[str, Any]:
    """
    Executa `script` num subprocesso, com uma pasta temporária como
    diretório de trabalho. A pasta é apagada ao final.
    """
    with tempfile.TemporaryDirectory(prefix="t2i_exec_") as pasta_str:
        pasta = Path(pasta_str)
        (pasta / NOME_SCRIPT).write_text(script, encoding="utf-8")
        try:
            processo = subprocess.run(
                [sys.executable, NOME_SCRIPT],
                cwd=pasta,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return _resultado(False, f"Timeout: o script excedeu o limite {timeout_s}s", "")

    stdout = processo.stdout.strip()
    stderr = processo.stderr.strip()

    if processo.returncode != 0:
        erro = stderr or stdout or f"Processo terminou com código {processo.returncode}."
        return _resultado(False, erro, stdout)
    return _resultado(True, "", stdout)