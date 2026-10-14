"""Registry de provedores de LLM.

Cada provedor é registrado com um conjunto de palavras-chave. Um nome de
modelo é associado ao primeiro provedor cuja palavra-chave aparecer no nome
(comparação case-insensitive), preservando a lógica original de seleção.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

# Um builder recebe (model_name, api_key, **kwargs) e devolve uma instância de chat model.
ModelBuilder = Callable[..., Any]


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    keywords: tuple[str, ...]
    builder: ModelBuilder

    def matches(self, model_name: str) -> bool:
        lowered = model_name.lower()
        return any(keyword in lowered for keyword in self.keywords)


class ModelRegistry:
    """Mantém o mapeamento provedor -> builder, em ordem de registro."""

    def __init__(self) -> None:
        self._providers: dict[str, ProviderSpec] = {}

    def register(
        self,
        name: str,
        keywords: Iterable[str] | None = None,
        builder: ModelBuilder | None = None,
    ):
        """Registra um provedor.

        Pode ser usado diretamente (passando ``builder``) ou como decorator::

            @registry.register("gemini", keywords=["gemini"])
            def _build(model_name, api_key): ...
        """
        kws = tuple(k.lower() for k in (keywords or [name]))

        def _decorator(fn: ModelBuilder) -> ModelBuilder:
            if name in self._providers:
                raise ValueError(f"Provedor '{name}' já registrado.")
            self._providers[name] = ProviderSpec(name=name, keywords=kws, builder=fn)
            return fn

        if builder is not None:
            return _decorator(builder)
        return _decorator

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)

    def resolve(self, model_name: str) -> ProviderSpec:
        for spec in self._providers.values():
            if spec.matches(model_name):
                return spec
        keywords = " ou ".join(f"'{k}'" for spec in self._providers.values() for k in spec.keywords)
        raise ValueError(
            f"Modelo '{model_name}' não reconhecido. Use {keywords} no nome do modelo."
        )

    @property
    def providers(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def __contains__(self, name: object) -> bool:
        return name in self._providers


# Registry padrão, populado em ``providers.py``.
default_registry = ModelRegistry()
