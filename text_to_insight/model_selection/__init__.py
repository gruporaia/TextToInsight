"""Seleção de modelo/provedor LLM via Registry + Factory.

Uso::

    from text_to_insight.model_selection import get_model, ModelFactory
    llm = get_model("gemini-2.5-flash", api_key)
"""

from .registry import ModelRegistry, ProviderSpec, default_registry
from . import providers  # noqa: F401  (registra os provedores padrão)
from .factory import ModelFactory, get_model

__all__ = [
    "ModelRegistry",
    "ProviderSpec",
    "ModelFactory",
    "default_registry",
    "get_model",
]
