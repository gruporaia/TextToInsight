import pytest

@pytest.mark.real_api
@pytest.mark.timeout(60)
def test_provider_model_smoke_real_api(llm):
    resposta = llm.invoke("Responda apenas com: OK")
    conteudo = str(getattr(resposta, "content", "")).strip().upper()

    assert conteudo != ""
    assert "OK" in conteudo
