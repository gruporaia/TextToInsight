"""
Onde a biblioteca guarda arquivos que sobrevivem entre execuções.

Tudo fica numa pasta de cache por usuário (via `platformdirs`, ex.:
`~/Library/Caches/text-to-insight` no macOS, `~/.cache/text-to-insight` no
Linux), nunca dentro da pasta do pacote instalado (que pode não ter permissão
de escrita) nem ao lado do banco do usuário. Quem usa a biblioteca pode
escolher outra pasta com `InsightEngine(cache_dir=...)`.

Conteúdo da pasta:
- `schemas/<hash>_enriched.txt`: schema enriquecido pelo LLM (enrich_rag),
  indexado pelo hash do texto do schema. Funciona para qualquer banco, e um
  schema que mudou gera um hash novo (o cache antigo simplesmente deixa de
  ser usado).
- `chroma_db/`: índice vetorial do ChromaDB usado pelo RAG e pelo seletor
  de colunas.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from platformdirs import user_cache_dir

NOME_APP = "text-to-insight"


def resolver_cache_dir(cache_dir: str | Path | None = None) -> Path:
    """Pasta de cache escolhida pelo chamador, ou a pasta padrão do usuário."""
    return Path(cache_dir) if cache_dir else Path(user_cache_dir(NOME_APP))


def caminho_schema_enriquecido(cache_dir: str | Path | None, contexto_schema: str) -> Path:
    """Arquivo de cache do schema enriquecido correspondente a `contexto_schema`."""
    hash_schema = hashlib.sha256(contexto_schema.encode("utf-8")).hexdigest()[:32]
    return resolver_cache_dir(cache_dir) / "schemas" / f"{hash_schema}_enriched.txt"


def ler_schema_enriquecido(cache_dir: str | Path | None, contexto_schema: str) -> str | None:
    """Devolve o schema enriquecido em cache para este schema, se existir."""
    caminho = caminho_schema_enriquecido(cache_dir, contexto_schema)
    try:
        return caminho.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None


def salvar_schema_enriquecido(cache_dir: str | Path | None, contexto_schema: str, enriquecido: str) -> Path | None:
    """Grava o schema enriquecido. Falha de escrita só desativa o cache, não a execução."""
    caminho = caminho_schema_enriquecido(cache_dir, contexto_schema)
    try:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(enriquecido, encoding="utf-8")
    except OSError as e:
        print(f"[CACHE] Não foi possível gravar o schema enriquecido em {caminho}: {e}")
        return None
    return caminho


def diretorio_chroma(cache_dir: str | Path | None = None) -> Path:
    """Pasta do ChromaDB persistente (RAG e seletor de colunas)."""
    return resolver_cache_dir(cache_dir) / "chroma_db"
