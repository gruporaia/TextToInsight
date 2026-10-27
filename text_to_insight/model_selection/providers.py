"""Provedores de LLM registrados no registry padrão.

Para adicionar um novo provedor, basta registrar um builder aqui (ou em
qualquer módulo importado antes do uso) com ``default_registry.register``.
"""

from .registry import default_registry


@default_registry.register("google", keywords=["gemini", "google"], aliases=["gemini"])
def build_gemini(model_name: str, api_key: str, **kwargs):
    """Aceita parâmetros extras do ChatGoogleGenerativeAI, ex.: ``temperature``,
    ``max_tokens`` (alias de ``max_output_tokens``)."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key, **kwargs)


@default_registry.register("openai", keywords=["gpt", "openai"], aliases=["gpt"])
def build_openai(model_name: str, api_key: str, **kwargs):
    """Aceita parâmetros extras do ChatOpenAI, ex.: ``temperature``, ``max_tokens``."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=model_name, openai_api_key=api_key, **kwargs)
