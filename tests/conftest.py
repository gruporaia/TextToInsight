import pytest

@pytest.fixture(scope="module")
def vcr_config():
    """
    Configuração global do VCR para este módulo.
    Garante que as chaves de API sejam censuradas e não vazem nos arquivos .yaml em tests/cassettes
    """
    return {
        # Oculta a chave se ela for enviada no cabeçalho
        "filter_headers": ["x-goog-api-key", "authorization"],
        
        # Oculta a chave se ela for enviada na URL
        "filter_query_parameters": ["key"],
    }