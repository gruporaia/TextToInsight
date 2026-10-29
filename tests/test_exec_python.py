from text_to_insight.execucao_python import executar_python_isolado

def test_script_simples():

    resultado = executar_python_isolado('print("ola")')
    assert resultado ["ok"] is True
    assert "ola" in resultado["saida_terminal"]
    assert resultado ["erro_execucao"] == ""


def test_script_errado():
    resultado = executar_python_isolado('raise ValueError("deu ruim")')
    assert resultado ["ok"] is False
    assert "deu ruim" in resultado["erro_execucao"] 
    assert resultado["saida_terminal"] == ""

def test_timeout():
    resultado = executar_python_isolado('while True: pass', timeout_s=2)
    assert resultado["ok"] is False
    assert "timeout" in resultado["erro_execucao"].lower()