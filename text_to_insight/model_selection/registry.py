"""Registry de provedores de LLM.

Suporta identificação no formato relativo ao provedor (<provedor>/<modelo>, ex.:
'openai/gpt-4o', 'google/gemini-2.5-flash') com fallback para busca de
palavras-chave em nomes simples (ex.: 'gpt' -> 'openai', 'gemini' -> 'google').
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

# Um builder recebe (model_name, api_key, **kwargs) e devolve uma instância de chat model.
ModelBuilder = Callable[..., Any]


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    keywords: tuple[str, ...]
    builder: ModelBuilder
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def matches_provider(self, provider_name: str) -> bool:
        """Verifica se o nome informado corresponde ao provedor ou a um de seus aliases."""
        lowered = provider_name.strip().lower()
        if lowered == self.name.lower():
            return True
        return any(lowered == alias.lower() for alias in self.aliases)

    def matches_keywords(self, model_name: str) -> bool:
        """Verifica se o nome do modelo contém alguma das palavras-chave do provedor."""
        lowered = model_name.lower()
        return any(keyword in lowered for keyword in self.keywords)

    def matches(self, model_name: str) -> bool:
        """Compatibilidade retroativa: atalho para matches_keywords."""
        return self.matches_keywords(model_name)


class ModelRegistry:
    """Mantém o mapeamento provedor -> builder, em ordem de registro."""

    def __init__(self) -> None:
        self._providers: dict[str, ProviderSpec] = {}

    def register(
        self,
        name: str,
        keywords: Iterable[str] | None = None,
        builder: ModelBuilder | None = None,
        aliases: Iterable[str] | None = None,
    ):
        """Registra um provedor.

        Pode ser usado diretamente (passando ``builder``) ou como decorator::

            @registry.register("google", keywords=["gemini", "google"], aliases=["gemini"])
            def _build(model_name, api_key): ...
        """
        kws = tuple(k.lower() for k in (keywords or [name]))
        alias_tuple = tuple(a.lower() for a in (aliases or []))

        def _decorator(fn: ModelBuilder) -> ModelBuilder:
            lowered_name = name.lower()
            if lowered_name in self._providers:
                raise ValueError(f"Provedor '{name}' já registrado.")
            self._providers[lowered_name] = ProviderSpec(
                name=name, keywords=kws, builder=fn, aliases=alias_tuple
            )
            return fn

        if builder is not None:
            return _decorator(builder)
        return _decorator

    def unregister(self, name: str) -> None:
        lowered = name.lower()
        if lowered in self._providers:
            del self._providers[lowered]
        else:
            for k, spec in list(self._providers.items()):
                if spec.name.lower() == lowered or any(a.lower() == lowered for a in spec.aliases):
                    del self._providers[k]

    def parse_model(self, identifier: str) -> tuple[ProviderSpec, str]:
        """Resolve o provedor e extrai o nome concreto do modelo.

        1. Se contiver '/', trata o prefixo como nome ou alias do provedor e
           extrai o nome do modelo sem o prefixo (ex.: 'openai/gpt-4o' ->
           spec 'openai', modelo 'gpt-4o').
        2. Caso contrário, faz fallback para palavras-chave simples
           (ex.: 'gpt' -> 'openai', 'gemini' -> 'google').
        """
        raw = identifier.strip()
        if "/" in raw:
            provider_prefix, concrete_model = raw.split("/", 1)
            provider_prefix = provider_prefix.strip()
            concrete_model = concrete_model.strip()
            if not concrete_model:
                raise ValueError(
                    f"Identificador '{identifier}' inválido: nome do modelo vazio após a barra."
                )
            for spec in self._providers.values():
                if spec.matches_provider(provider_prefix):
                    return spec, concrete_model

            known = ", ".join(f"'{p}'" for p in self.providers)
            raise ValueError(
                f"Provedor '{provider_prefix}' não reconhecido para o modelo '{identifier}'. "
                f"Provedores disponíveis: {known}."
            )

        # Fallback para nomes simples baseados em palavras-chave
        for spec in self._providers.values():
            if spec.matches_keywords(raw):
                return spec, raw

        keywords = " ou ".join(f"'{k}'" for spec in self._providers.values() for k in spec.keywords)
        raise ValueError(
            f"Modelo '{identifier}' não reconhecido. Use '<provedor>/<modelo>' (ex.: 'openai/gpt-4o', "
            f"'google/gemini-2.5-flash') ou inclua palavras-chave como {keywords} no nome do modelo."
        )

    def resolve(self, model_name: str) -> ProviderSpec:
        """Compatibilidade retroativa: resolve e devolve apenas o ProviderSpec."""
        spec, _ = self.parse_model(model_name)
        return spec

    @property
    def providers(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self._providers.values())

    def __contains__(self, name: object) -> bool:
        if not isinstance(name, str):
            return False
        lowered = name.lower()
        return any(
            spec.name.lower() == lowered or any(a.lower() == lowered for a in spec.aliases)
            for spec in self._providers.values()
        )


# Registry padrão, populado em ``providers.py``.
default_registry = ModelRegistry()
