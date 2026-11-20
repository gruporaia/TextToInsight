"""
Funções de embedding multilíngues locais (ONNX) para o retriever e o seletor de colunas.

Usa apenas `onnxruntime`, `tokenizers` e `numpy` (já instalados como dependências
do ChromaDB), sem exigir PyTorch nem chamadas de API externas por padrão.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import tempfile
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import onnxruntime as ort
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings, Space
from chromadb.utils import embedding_functions
from tokenizers import Tokenizer

from ..cache import resolver_cache_dir

DEFAULT_EMBEDDING_MODEL = "multilingual-e5-small"

# Presets ONNX quantizados (int8) com revisão imutável (commit SHA) e digests SHA-256
# verificados em tempo de download e de leitura do cache local.
ONNX_MODEL_PRESETS: dict[str, dict[str, Any]] = {
    "multilingual-e5-small": {
        "repo_id": "Xenova/multilingual-e5-small",
        "revision": "761b726dd34fb83930e26aab4e9ac3899aa1fa78",
        "onnx_file": "onnx/model_quantized.onnx",
        "onnx_sha256": "f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193",
        "tokenizer_sha256": "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
        "max_length": 512,
        "use_e5_prefixes": True,
        "slug": "me5s",
        "rule_sim_threshold": 0.83,
    },
    "multilingual-e5-base": {
        "repo_id": "Xenova/multilingual-e5-base",
        "revision": "1ec9243030a27d1a115d5c340572074c125b58b2",
        "onnx_file": "onnx/model_quantized.onnx",
        "onnx_sha256": "df7a9a29309e3ad491e1783adf8baee710262cc06079c7cbab63c630277fac94",
        "tokenizer_sha256": "62c24cdc13d4c9952d63718d6c9fa4c287974249e16b7ade6d5a85e7bbb75626",
        "max_length": 512,
        "use_e5_prefixes": True,
        "slug": "me5b",
        "rule_sim_threshold": 0.83,
    },
}


def _model_cache_root() -> Path:
    """Diretório global de cache para pesos ONNX (compartilhado entre testes/execuções)."""
    env_override = os.getenv("TEXT_TO_INSIGHT_MODEL_CACHE_DIR")
    if env_override:
        return Path(env_override)
    return resolver_cache_dir(None) / "onnx_models"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _download_file(
    url: str,
    dest: Path,
    expected_sha256: str | None = None,
) -> None:
    if dest.is_symlink():
        raise ValueError(
            f"Recusando ler ou sobrescrever symlink no cache de modelos: '{dest}'."
        )

    if dest.exists() and dest.is_file() and dest.stat().st_size > 0:
        if expected_sha256:
            actual = _sha256_file(dest)
            if not hmac.compare_digest(actual.lower(), expected_sha256.lower()):
                raise ValueError(
                    f"Integridade SHA-256 inválida para arquivo em cache '{dest}': "
                    f"esperado {expected_sha256}, obtido {actual}. "
                    "Remova o arquivo corrompido/adulterado do cache antes de tentar novamente."
                )
        return

    if not url.startswith("https://"):
        raise ValueError(f"Apenas downloads HTTPS são permitidos para pesos de modelo: '{url}'.")

    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.parent.is_symlink():
        try:
            os.chmod(dest.parent, 0o700)
        except OSError:
            pass

    fd, tmp_name = tempfile.mkstemp(
        dir=str(dest.parent),
        prefix=f".{dest.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    req = urllib.request.Request(url, headers={"User-Agent": "text-to-insight-retriever"})
    hasher = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(req, timeout=120) as resp:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
                out.write(chunk)

        if expected_sha256:
            actual = hasher.hexdigest()
            if not hmac.compare_digest(actual.lower(), expected_sha256.lower()):
                raise ValueError(
                    f"Integridade SHA-256 inválida no download de '{url}': "
                    f"esperado {expected_sha256}, obtido {actual}."
                )

        if dest.is_symlink():
            raise ValueError(
                f"Recusando substituir symlink no cache de modelos: '{dest}'."
            )
        os.replace(tmp_path, dest)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


@lru_cache(maxsize=4)
def _load_onnx_e5_runtime(
    repo_id: str,
    revision: str,
    onnx_file: str,
    onnx_sha256: str,
    tokenizer_sha256: str,
    max_length: int,
) -> tuple[ort.InferenceSession, Tokenizer, set[str]]:
    model_dir = _model_cache_root() / repo_id.replace("/", "__")
    model_path = model_dir / Path(onnx_file).name
    tok_path = model_dir / "tokenizer.json"

    _download_file(
        f"https://huggingface.co/{repo_id}/resolve/{revision}/{onnx_file}",
        model_path,
        expected_sha256=onnx_sha256,
    )
    _download_file(
        f"https://huggingface.co/{repo_id}/resolve/{revision}/tokenizer.json",
        tok_path,
        expected_sha256=tokenizer_sha256,
    )

    tokenizer = Tokenizer.from_file(str(tok_path))
    tokenizer.enable_truncation(max_length=max_length)
    # XLM-RoBERTa / multilingual-e5 usa pad_id=1 (<pad>); fallback para 0 se ausente.
    pad_id = tokenizer.token_to_id("<pad>")
    if pad_id is None:
        pad_id = 0
        pad_token = "[PAD]"
    else:
        pad_token = "<pad>"
    tokenizer.enable_padding(pad_id=pad_id, pad_token=pad_token)

    sess_opts = ort.SessionOptions()
    sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = ort.InferenceSession(
        str(model_path),
        sess_options=sess_opts,
        providers=["CPUExecutionProvider"],
    )
    input_names = {inp.name for inp in session.get_inputs()}
    return session, tokenizer, input_names


@embedding_functions.register_embedding_function
class OnnxMultilingualE5EmbeddingFunction(EmbeddingFunction[Documents]):
    """
    EmbeddingFunction do ChromaDB para modelos ONNX multilingual-e5 (PT/EN).

    Aplica automaticamente o prefixo assimétrico exigido pelos modelos E5:
    - `"passage: "` em `__call__` (documentos indexados)
    - `"query: "` em `embed_query` (perguntas em runtime)
    """

    def __init__(
        self,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        batch_size: int = 16,
    ):
        if model_name not in ONNX_MODEL_PRESETS:
            valid = ", ".join(sorted(ONNX_MODEL_PRESETS))
            raise ValueError(
                f"Modelo ONNX desconhecido '{model_name}'. Opções válidas: {valid}"
            )
        preset = ONNX_MODEL_PRESETS[model_name]
        self.model_name = model_name
        self.repo_id: str = preset["repo_id"]
        self.revision: str = preset["revision"]
        self.onnx_file: str = preset["onnx_file"]
        self.onnx_sha256: str = preset["onnx_sha256"]
        self.tokenizer_sha256: str = preset["tokenizer_sha256"]
        self.max_length: int = preset["max_length"]
        self.use_e5_prefixes: bool = preset["use_e5_prefixes"]
        self.model_slug: str = preset["slug"]
        self.rule_sim_threshold: float = preset["rule_sim_threshold"]
        self.batch_size = batch_size

        # Garante que os pesos existem, têm integridade SHA-256 válida e a sessão ONNX está aquecida.
        self._session, self._tokenizer, self._input_names = _load_onnx_e5_runtime(
            self.repo_id,
            self.revision,
            self.onnx_file,
            self.onnx_sha256,
            self.tokenizer_sha256,
            self.max_length,
        )

    def _encode(self, texts: Documents, is_query: bool) -> Embeddings:
        if not texts:
            return []
        if self.use_e5_prefixes:
            prefix = "query: " if is_query else "passage: "
            prepared = [f"{prefix}{t}" for t in texts]
        else:
            prepared = list(texts)

        all_vecs: list[np.ndarray] = []
        for i in range(0, len(prepared), self.batch_size):
            batch = prepared[i : i + self.batch_size]
            encs = self._tokenizer.encode_batch(batch)
            input_ids = np.array([e.ids for e in encs], dtype=np.int64)
            attention_mask = np.array([e.attention_mask for e in encs], dtype=np.int64)

            feeds: dict[str, np.ndarray] = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
            }
            if "token_type_ids" in self._input_names:
                feeds["token_type_ids"] = np.zeros_like(input_ids, dtype=np.int64)

            hidden = self._session.run(None, feeds)[0]
            mask = np.expand_dims(attention_mask, -1).astype(np.float32)
            pooled = (hidden * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            normed = (pooled / np.clip(norms, 1e-12, None)).astype(np.float32)
            all_vecs.append(normed)

        merged = np.concatenate(all_vecs, axis=0)
        return [vec for vec in merged]

    def __call__(self, input: Documents) -> Embeddings:
        return self._encode(input, is_query=False)

    def embed_query(self, input: Documents) -> Embeddings:
        return self._encode(input, is_query=True)

    @staticmethod
    def name() -> str:
        return "text_to_insight_onnx_multilingual_e5"

    def default_space(self) -> Space:
        return "cosine"

    def supported_spaces(self) -> List[Space]:
        return ["cosine", "l2", "ip"]

    def get_config(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "batch_size": self.batch_size,
        }

    @staticmethod
    def build_from_config(
        config: Dict[str, Any],
    ) -> "OnnxMultilingualE5EmbeddingFunction":
        return OnnxMultilingualE5EmbeddingFunction(
            model_name=config.get("model_name", DEFAULT_EMBEDDING_MODEL),
            batch_size=int(config.get("batch_size", 16)),
        )


@lru_cache(maxsize=4)
def get_default_embedding_function(
    model_name: str | None = None,
) -> EmbeddingFunction[Documents]:
    """
    Retorna a EmbeddingFunction padrão (multilíngue E5 ONNX) em singleton.

    Aceita override por argumento ou pela variável `TEXT_TO_INSIGHT_EMBEDDING_MODEL`:
    - `"multilingual-e5-small"` (padrão)
    - `"multilingual-e5-base"`
    - `"minilm"` / `"default"` (fallback legado do ChromaDB)
    """
    chosen = (
        model_name
        or os.getenv("TEXT_TO_INSIGHT_EMBEDDING_MODEL")
        or DEFAULT_EMBEDDING_MODEL
    ).strip().lower()

    if chosen in ("minilm", "default", "all-minilm-l6-v2"):
        return embedding_functions.DefaultEmbeddingFunction()

    if chosen in ONNX_MODEL_PRESETS:
        try:
            return OnnxMultilingualE5EmbeddingFunction(model_name=chosen)
        except Exception as exc:
            print(
                f"[RAG] Aviso: falha ao carregar modelo multilíngue '{chosen}' ({exc}); "
                "usando DefaultEmbeddingFunction do ChromaDB como fallback."
            )
            return embedding_functions.DefaultEmbeddingFunction()

    raise ValueError(
        f"Modelo de embedding não suportado: '{chosen}'. "
        f"Use um de: {', '.join(sorted(ONNX_MODEL_PRESETS))}, 'minilm'."
    )


def embedding_model_id(ef: Any) -> str:
    """Identificador curto e determinístico do embedder para compor o nome da collection."""
    if ef is None:
        return "default"
    slug = getattr(ef, "model_slug", None)
    if slug:
        return str(slug)
    if isinstance(ef, embedding_functions.DefaultEmbeddingFunction):
        return "minilm"
    try:
        ef_name = ef.name() if callable(getattr(ef, "name", None)) else type(ef).__name__
        cfg = ef.get_config() if callable(getattr(ef, "get_config", None)) else {}
        raw = f"{ef_name}:{sorted(cfg.items()) if isinstance(cfg, dict) else cfg}"
    except Exception:
        raw = type(ef).__name__
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]
