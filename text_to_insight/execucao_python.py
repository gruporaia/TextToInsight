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
import uuid
from pathlib import Path
from typing import Any

NOME_SCRIPT = "script.py"
IMAGEM_DOCKER_PADRAO = "text-to-insight-sandbox"
PASTA_CONTAINER = "/work"


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

def _comando_docker(pasta: Path, nome_container: str, imagem:str, runtime:str | None) -> list[str]:
    comando = [
        "docker", "run", "--rm",
        "--name", nome_container,
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp",
        "--memory", "512m", "--cpus", "1", "--pids-limit", "64",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "-e", "MPLCONFIGDIR=/tmp",
        "-v", f"{pasta}:{PASTA_CONTAINER}",
        "-w", PASTA_CONTAINER
    ]
    if os.name != "nt": 
        comando += ["--user", f"{os.getuid()}:{os.getgid()}"]
    if runtime != None:
        comando += ["--runtime", runtime]
    comando += [imagem, "python", NOME_SCRIPT]
    return comando



def executar_python_isolado(
        script: str,
        arquivos_entrada: dict[str, Path] | None = None,
        arquivos_saida: dict[str, Path] | None = None,  
        timeout_s: float = 30.0,
        usar_docker: bool = False,
        docker_runtime: str | None = None,
        docker_imagem: str = IMAGEM_DOCKER_PADRAO
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
        nome_container = f"t2i_{uuid.uuid4().hex[:12]}"
        if usar_docker:
            comando = _comando_docker(pasta, nome_container, docker_imagem, docker_runtime)
            env = None
        else:
            comando = [sys.executable, NOME_SCRIPT]
            env = _ambiente_minimo(pasta)
        try:
            processo = subprocess.run(
                comando,
                cwd=pasta,
                env = env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            if usar_docker == True:
                subprocess.run(["docker", "rm", "-f", nome_container], capture_output=True)
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