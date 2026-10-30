from text_to_insight.execucao_python import executar_python_isolado
from pathlib import Path


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

def test_arquivo_chega_script(tmp_path):
    origem = tmp_path / "dados dados.txt"
    origem.write_text("conteudo de teste", encoding="utf-8")

    script = 'print(open("entrada.txt", encoding="utf-8").read())'
    resultado = executar_python_isolado(
        script, arquivos_entrada={"entrada.txt": origem}
    )

    assert resultado["ok"] is True
    assert "conteudo de teste" in resultado["saida_terminal"]

def test_copiar_arquivo_de_saida(tmp_path):
    script = 'open("saida.txt", "w", encoding="utf-8").write("resultado ok")'
    destino = tmp_path / "resultado.txt"
    resultado = executar_python_isolado(script, arquivos_saida={"saida.txt": destino})
    assert resultado["ok"] is True
    assert destino.exists() is True
    destino.read_text(encoding="utf-8") == resultado["ok"]



def test_saida_esperada_nao_gerada(tmp_path):
    script = 'pass'
    resultado = executar_python_isolado(script, arquivos_saida={"grafico.png": tmp_path / "g.png"})
    assert resultado["ok"] is False
    assert "grafico.png" in resultado["erro_execucao"]

def test_pasta_tmp_some():
    script = 'import os; print(os.getcwd())'
    resultado = executar_python_isolado(script)
    pasta_usada = Path(resultado["saida_terminal"])
    assert resultado["ok"] is True
    assert pasta_usada.exists() is False
