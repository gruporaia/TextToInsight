from text_to_insight.execucao_python import executar_python_isolado

def test_docker_ausente(monkeypatch):
    monkeypatch.setenv("PATH", "/caminho/nao/existente")
    resultado = executar_python_isolado('print("rodou")', usar_docker=True)
    assert resultado["ok"] is False
    assert "docker" in resultado["erro_execucao"].lower()
    assert "rodou" not in resultado["saida_terminal"]

    