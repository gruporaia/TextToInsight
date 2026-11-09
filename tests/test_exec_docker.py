import shutil, subprocess
import pytest
from text_to_insight.execucao_python import IMAGEM_DOCKER_PADRAO, executar_python_isolado

def _docker_pronto(imagem=IMAGEM_DOCKER_PADRAO):
    if shutil.which("docker") is None:
        return False
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        return False
    return subprocess.run(["docker", "image", "inspect", imagem], capture_output=True).returncode == 0

pytestmark = pytest.mark.skipif(not _docker_pronto(), reason="Docker indisponível")

def test_docker_simples():
    resultado = executar_python_isolado('print("ola")', usar_docker=True)
    assert resultado["ok"] is True
    assert "ola" in resultado["saida_terminal"]