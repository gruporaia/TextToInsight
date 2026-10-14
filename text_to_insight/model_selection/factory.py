"""Factory que instancia chat models a partir do registry."""

from __future__ import annotations

from typing import Any

from .registry import ModelRegistry, default_registry


class ModelFactory:
    """Cria instâncias de LLM delegando ao provedor resolvido no registry."""

    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self.registry = registry if registry is not None else default_registry

    def create(self, model_name: str, api_key: str, **kwargs: Any) -> Any:
        """Instancia o modelo; ``kwargs`` são repassados ao builder do provedor
        (ex.: ``temperature``, ``max_tokens`` para a família OpenAI)."""
        spec = self.registry.resolve(model_name)
        return spec.builder(model_name, api_key, **kwargs)


def get_model(model_name: str, api_key: str, **kwargs: Any) -> Any:
    """Atalho compatível com a API antiga: usa o registry padrão."""
    return ModelFactory().create(model_name, api_key, **kwargs)
