"""

Executar isoladamente scripts de python gerados pela biblioteca

O script roda num subprocesso dentro de uma pasta temporária e própria
A devolução do resultado continua seguindo o padrão de formato do executor

"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path
from typing import Any

NOME_SCRIPT = "script.py"


def _resultado(ok: bool, erro: str, saida: str) -> dict[str, Any]:
    """Monta dicionário de retorno"""
    return {"ok": ok, "erro_execucao": erro, "saida_terminal": saida}

def _ambiente_minimo(pasta: Path) -> dict[str, str]:
    """
    Monta do zero as variáveis de ambiente do processo filho (allowlist).
    Nada do ambiente do processo pai é herdado além do que está aqui.
    """
    ambiente = {
        "PATH": os.environ.get("PATH", ""),
        "MPLCONFIGDIR": str(pasta),
    }
    if os.name == "nt":
        # Pelo que sabemos, o Python no Windows pode falhar ao iniciar sem
        # SYSTEMROOT. Não testado em Windows.
        ambiente["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
    return ambiente

def executar_python_isolado(
        script: str,
        arquivos_entrada: dict[str, Path] | None = None,
        arquivos_saida: dict[str, Path] | None = None,  
        timeout_s: float = 30.0
        ) -> dict[str, Any]:
    """
    Executa `script` num subprocesso, com uma pasta temporária como
    diretório de trabalho. A pasta é apagada ao final.
    """
    if arquivos_entrada is None:
        arquivos_entrada = {}
    if arquivos_saida is None:
        arquivos_saida = {}
    with tempfile.TemporaryDirectory(prefix="t2i_exec_") as pasta_str:
        pasta = Path(pasta_str)
        (pasta / NOME_SCRIPT).write_text(script, encoding="utf-8")
        for nome, origem in arquivos_entrada.items():
            shutil.copy(origem, pasta / nome)
        try:
            processo = subprocess.run(
                [sys.executable, NOME_SCRIPT],
                cwd=pasta,
                env = _ambiente_minimo(pasta),
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return _resultado(False, f"Timeout: o script excedeu o limite de {timeout_s}s", "")

        stdout = processo.stdout.strip()
        stderr = processo.stderr.strip()

        if processo.returncode != 0:
            erro = stderr or stdout or f"Processo terminou com código {processo.returncode}."
            return _resultado(False, erro, stdout)
        for nome, destino in arquivos_saida.items():
            if((pasta / nome).exists() != True):
                return _resultado(False, f"O script não gerou o arquivo {nome} esperado", stdout)
            destino.parent.mkdir(parents = True, exist_ok = True)
            shutil.copy(pasta / nome, destino)
        return _resultado(True, "", stdout)