import shutil, subprocess
import pytest
import os
from pathlib import Path
from text_to_insight.execucao_python import IMAGEM_DOCKER_PADRAO, executar_python_isolado

RUNTIME_TESTE = os.environ.get("T2I_DOCKER_RUNTIME")


def _docker_pronto(imagem=IMAGEM_DOCKER_PADRAO):
    if shutil.which("docker") is None:
        return False
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        return False
    return subprocess.run(["docker", "image", "inspect", imagem], capture_output=True).returncode == 0

pytestmark = pytest.mark.skipif(not _docker_pronto(), reason="Docker indisponível")

def test_docker_simples():
    resultado = executar_python_isolado('print("ola")', usar_docker=True, docker_runtime=RUNTIME_TESTE)
    assert resultado["ok"] is True
    assert "ola" in resultado["saida_terminal"]

def test_timeout_docker():
    resultado = executar_python_isolado('while True: pass', timeout_s=3, usar_docker=True, docker_runtime=RUNTIME_TESTE)
    assert resultado["ok"] is False
    assert "timeout" in resultado["erro_execucao"].lower()
    assert subprocess.run(["docker", "ps", "-q", "--filter", "name=t2i_"],
    capture_output=True, text=True).stdout.strip() == ""


def test_docker_script_errado():
    resultado = executar_python_isolado('raise ValueError("deu ruim")', usar_docker=True, docker_runtime=RUNTIME_TESTE)
    assert resultado["ok"] is False
    assert "deu ruim" in resultado["erro_execucao"]


def test_docker_arquivo_chega_script(tmp_path):
    origem = tmp_path / "dados dados.txt"
    origem.write_text("conteudo de teste", encoding="utf-8")
    script = 'print(open("entrada.txt", encoding="utf-8").read())'

    resultado = executar_python_isolado(
        script, arquivos_entrada={"entrada.txt": origem}, usar_docker=True, docker_runtime=RUNTIME_TESTE
    )

    assert resultado["ok"] is True, resultado["erro_execucao"]
    assert "conteudo de teste" in resultado["saida_terminal"]


def test_docker_copiar_arquivo_de_saida(tmp_path):
    script = 'open("saida.txt", "w", encoding="utf-8").write("resultado ok")'
    destino = tmp_path / "resultado.txt"

    resultado = executar_python_isolado(
        script, arquivos_saida={"saida.txt": destino}, usar_docker=True, docker_runtime=RUNTIME_TESTE
    )

    assert resultado["ok"] is True, resultado["erro_execucao"]
    assert destino.read_text(encoding="utf-8") == "resultado ok"


def test_docker_grafico_real_gera_png(tmp_path):
    from text_to_insight.nodes import graph_generator as gg

    csv = tmp_path / 'resultado "da" query.csv'
    csv.write_text("x,y\n1,2\n2,4\n3,6\n", encoding="utf-8")
    destino = tmp_path / "saida.png"
    script = gg._construir_script("plt.plot(df['x'], df['y'])")

    resultado = executar_python_isolado(
        script,
        arquivos_entrada={gg.NOME_CSV: csv},
        arquivos_saida={gg.NOME_GRAFICO: destino},
        timeout_s=60,
        usar_docker=True,
        docker_runtime=RUNTIME_TESTE,
    )

    assert resultado["ok"] is True, resultado["erro_execucao"]
    assert destino.read_bytes().startswith(b"\x89PNG")

def test_docker_sem_rede():
    script = (
        "import socket\n"
        'socket.create_connection(("1.1.1.1", 80), timeout=3)\n'
        'print("CONECTOU")\n'
    )
    resultado = executar_python_isolado(script, usar_docker=True, docker_runtime=RUNTIME_TESTE)

    assert resultado["ok"] is False
    assert "CONECTOU" not in resultado["saida_terminal"]


def test_docker_nao_le_arquivo_da_maquina(tmp_path):
    segredo = tmp_path / "segredo.env"
    segredo.write_text("SENHA=abc123", encoding="utf-8")
    script = f"print(open({str(segredo)!r}).read())"

    resultado = executar_python_isolado(script, usar_docker=True, docker_runtime=RUNTIME_TESTE)

    assert "abc123" not in resultado["saida_terminal"]
    assert "abc123" not in resultado["erro_execucao"]


def test_docker_nao_ve_variaveis_do_host(monkeypatch):
    monkeypatch.setenv("CHAVE_FALSA", "segredo123")
    script = 'import os; print(os.environ.get("CHAVE_FALSA"))'

    resultado = executar_python_isolado(script, usar_docker=True, docker_runtime=RUNTIME_TESTE)

    assert resultado["ok"] is True, resultado["erro_execucao"]
    assert "segredo123" not in resultado["saida_terminal"]


def test_docker_nao_escreve_fora_da_pasta():
    resultado = executar_python_isolado('open("/etc/hack", "w").write("x")', usar_docker=True, docker_runtime=RUNTIME_TESTE)
    assert resultado["ok"] is False


def test_docker_limite_de_memoria():
    resultado = executar_python_isolado("x = bytearray(2 * 1024**3)", usar_docker=True, docker_runtime=RUNTIME_TESTE)
    assert resultado["ok"] is False


def test_docker_runtime_inexistente():
    resultado = executar_python_isolado(
        'print("ola")', usar_docker=True, docker_runtime="runtime-que-nao-existe"
    )
    assert resultado["ok"] is False
    assert resultado["erro_execucao"] != ""