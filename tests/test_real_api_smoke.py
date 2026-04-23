"""Smoke test opcional com API real para detectar drift de provider/modelo."""

import os
import pytest
from text_to_insight.model_selection import get_model
from dotenv import load_dotenv
load_dotenv()

@pytest.mark.real_api
@pytest.mark.timeout(60)
def test_provider_model_smoke_real_api():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        pytest.skip("GOOGLE_API_KEY não encontrada para teste real_api.")

    llm = get_model("gemini-2.5-flash", api_key)
    resposta = llm.invoke("Responda apenas com: OK")
    conteudo = str(getattr(resposta, "content", "")).strip().upper()

    assert conteudo != ""
    assert "OK" in conteudo
