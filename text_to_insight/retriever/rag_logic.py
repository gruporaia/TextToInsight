import hashlib
import math
import os
import re
import unicodedata
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

import chromadb
import nltk
from nltk.corpus import stopwords
from nltk.stem import RSLPStemmer
from nltk.stem.snowball import SnowballStemmer

from ..cache import resolver_cache_dir
from .RAG_example import SCHEMA
from .embeddings import embedding_model_id, get_default_embedding_function

BASE_DIR = Path(__file__).resolve().parent

# Termos estruturais de DDL e markdown que inflam `doc_len` e causam falsos positivos
# no BM25 e nos embeddings quando não fazem parte do vocabulário de identificadores do schema.
_DDL_AND_MD_NOISE_WORDS: frozenset[str] = frozenset(
    {
        "tabela",
        "table",
        "integer",
        "int",
        "bigint",
        "smallint",
        "tinyint",
        "varchar",
        "char",
        "text",
        "uuid",
        "boolean",
        "bool",
        "timestamp",
        "datetime",
        "date",
        "time",
        "real",
        "float",
        "double",
        "numeric",
        "decimal",
        "json",
        "jsonb",
        "blob",
        "serial",
        "pk",
        "fk",
        "not",
        "null",
        "default",
        "primary",
        "foreign",
        "key",
        "keys",
        "cascade",
        "restrict",
        "set",
        "on",
        "update",
        "delete",
        "current_timestamp",
        "charfield",
        "textfield",
        "uuidfield",
        "booleanfield",
        "datefield",
        "datetimefield",
        "integerfield",
        "autofield",
        "foreignkey",
        "onetoonefield",
        "jsonfield",
        "filefield",
        "decimalfield",
        "positiveintegerfield",
        "coluna",
        "tipo",
        "obrig",
        "chave",
        "valores",
        "possiveis",
        "uso",
        "descricao",
        "rascunho",
        "livre",
        "identificacao",
        "proibido",
        "sim",
    }
)

# Palavras curtas que aparecem nas listas de stopwords PT/EN, mas carregam significado
# de domínio relevante em consultas em português (ex.: "fora do prazo", "São Paulo", "EU", "AM").
_PROTECTED_STOPWORDS: frozenset[str] = frozenset({"eu", "am", "fora", "sao"})


def _strip_accents(text: str) -> str:
    return (
        unicodedata.normalize("NFKD", text)
        .encode("ascii", "ignore")
        .decode("ascii")
    )


def _split_identifier(ident: str) -> str:
    """Separa identificadores em camelCase, PascalCase, snake_case e kebab-case."""
    expanded = re.sub(r"([a-z0-9À-ÿ])([A-Z])", r"\1 \2", ident)
    expanded = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", expanded)
    return expanded.replace("_", " ").replace("-", " ")


def _clean_ddl_for_search(table_name: str, ddl_block: str) -> tuple[str, list[str]]:
    """
    Constrói uma representação limpa do DDL apenas para indexação (vetor + BM25),
    removendo tipos SQL/constraints e preservando nome da tabela, colunas separadas
    e eventuais descrições inline `-- [...]` do enriquecimento de schema.
    """
    cols: list[str] = []
    inline_descs: list[str] = []
    for line in ddl_block.splitlines():
        line_s = line.strip()
        if line_s.startswith("Foreign keys:") or " -> " in line_s:
            continue
        match = re.match(r"^-\s+([^:]+):\s*(.*)$", line_s)
        if match:
            col_name = match.group(1).strip()
            rest = match.group(2)
            cols.append(col_name)
            desc_match = re.search(r"--\s*\[(.+?)\]", rest)
            if desc_match:
                inline_descs.append(
                    f"{_split_identifier(col_name)}: {desc_match.group(1).strip()}"
                )

    readable_table = _split_identifier(table_name)
    readable_cols = ", ".join(_split_identifier(c) for c in cols)
    parts = [f"Table {table_name} ({readable_table}). Columns: {readable_cols}."]
    if inline_descs:
        parts.append("Descriptions: " + "; ".join(inline_descs))
    return "\n".join(parts), cols


def _clean_markdown_table_doc(md_text: str) -> str:
    """
    Limpa trechos markdown de descrição de tabela/coluna para indexação:
    - Remove linhas `Referenciada por:` (listas longas de FKs que contaminam a busca);
    - Remove separadores e cabeçalhos de tabela markdown;
    - Converte linhas `| coluna | tipo | ... | descrição |` em `coluna (palavras): descrição`.
    """
    out_lines: list[str] = []
    for line in md_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^[-*]?\s*\*\*Referenciada por:\*\*", stripped, re.IGNORECASE):
            continue
        if re.match(r"^\|[\s\-:|]+\|$", stripped):
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not cells or cells[0].lower() in ("coluna", "column", "campo"):
                continue
            col_name = cells[0]
            desc = cells[-1] if len(cells) >= 2 else ""
            # Preserva "valores possíveis" se existir como coluna intermediária em tabelas de dicionário
            vals = cells[4] if len(cells) >= 7 and cells[4] else ""
            readable_col = _split_identifier(col_name)
            label = f"{col_name} ({readable_col})" if readable_col != col_name else col_name
            suffix = f" [{vals}]" if vals else ""
            if col_name and desc:
                out_lines.append(f"{label}: {desc}{suffix}")
            elif col_name:
                out_lines.append(f"{label}{suffix}")
        else:
            out_lines.append(stripped)
    return "\n".join(out_lines)


@lru_cache(maxsize=1)
def _get_nltk_resources() -> tuple[RSLPStemmer, SnowballStemmer, set[str]]:
    nltk_dir = resolver_cache_dir() / "nltk_data"
    nltk_dir.mkdir(parents=True, exist_ok=True)
    # Restringe permissões apenas dentro da pasta de cache do próprio pacote
    # (nunca altera diretórios pais compartilhados como `~/.cache`).
    for directory in (nltk_dir, nltk_dir.parent):
        if directory.is_symlink():
            continue
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
    nltk.data.path = [
        str(nltk_dir),
        *[p for p in nltk.data.path if p != str(nltk_dir) and Path(p).exists()],
    ]

    for pkg, path_key in (("stopwords", "corpora/stopwords"), ("rslp", "stemmers/rslp")):
        try:
            nltk.data.find(path_key, paths=[str(nltk_dir)])
        except LookupError:
            nltk.download(pkg, download_dir=str(nltk_dir), quiet=True)

    pt_stemmer = RSLPStemmer()
    en_stemmer = SnowballStemmer("english")
    sw_words = set(stopwords.words("portuguese")) | set(stopwords.words("english"))
    sw_normalized = {_strip_accents(w.lower()) for w in sw_words}
    sw_normalized.update(
        {"quais", "quanto", "quantos", "quanta", "quantas", "onde", "cada"}
    )
    sw_normalized.difference_update(_PROTECTED_STOPWORDS)
    return pt_stemmer, en_stemmer, sw_normalized


def _extract_bm25_words(
    text: str,
    stopwords_set: set[str],
    schema_vocab: set[str] | None = None,
    filter_ddl_noise: bool = True,
) -> list[tuple[str, str]]:
    """
    Extrai pares `(palavra_lower, palavra_ascii)` separando `camelCase` e `snake_case`,
    preservando identificadores compostos e protegendo o vocabulário real do schema.
    """
    if not text:
        return []
    vocab = schema_vocab or set()
    expanded = re.sub(r"([a-z0-9À-ÿ])([A-Z])", r"\1 \2", text)
    expanded = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", expanded)
    raw_words = re.findall(r"[a-zA-ZÀ-ÿ0-9]+", expanded.lower())

    words: list[tuple[str, str]] = []
    for w in raw_words:
        w_ascii = _strip_accents(w)
        if len(w_ascii) <= 1:
            continue
        if w_ascii in stopwords_set and w_ascii not in vocab:
            continue
        if filter_ddl_noise and w_ascii in _DDL_AND_MD_NOISE_WORDS and w_ascii not in vocab:
            continue
        words.append((w, w_ascii))

    # Preserva identificadores snake_case completos para match exato (ex.: `order_items`)
    for comp in re.findall(r"[a-z0-9]+(?:_[a-z0-9]+)+", _strip_accents(text.lower())):
        words.append((comp, comp))
    return words


def _stems_for_word(
    w: str,
    w_ascii: str,
    pt_stemmer: RSLPStemmer,
    en_stemmer: SnowballStemmer,
) -> set[str]:
    """Retorna o conjunto deduplicado de radicais EN (Snowball) e PT (RSLP) para a palavra."""
    if w_ascii.isdigit() or "_" in w_ascii:
        return {w_ascii}
    stems: set[str] = set()
    en_s = en_stemmer.stem(w_ascii)
    if len(en_s) > 1:
        stems.add(en_s)
    pt_s = _strip_accents(pt_stemmer.stem(w))
    if len(pt_s) > 1:
        stems.add(pt_s)
    if w != w_ascii:
        pt_s_ascii = _strip_accents(pt_stemmer.stem(w_ascii))
        if len(pt_s_ascii) > 1:
            stems.add(pt_s_ascii)
    return stems or {w_ascii}


def _tokenize_bm25(text: str, schema_vocab: set[str] | None = None) -> list[str]:
    """
    Tokenizador bilíngue (PT + EN) deduplicado por ocorrência de palavra.
    Mantido como função pública para compatibilidade e inspeção; o índice interno
    `_BilingualBM25Index` usa contagem separada de exato vs. radical para evitar
    dupla contagem no score.
    """
    if not text:
        return []
    pt_stemmer, en_stemmer, sw = _get_nltk_resources()
    words = _extract_bm25_words(text, sw, schema_vocab=schema_vocab)
    tokens: list[str] = []
    for w, w_ascii in words:
        variants = {w_ascii} | _stems_for_word(w, w_ascii, pt_stemmer, en_stemmer)
        tokens.extend(sorted(variants))
    return tokens


class _BilingualBM25Index:
    """
    Índice BM25 bilíngue (Português + Inglês) com pontuação exato-vs-radical sem dupla contagem.

    Para cada palavra do documento:
    - Registra 1 ocorrência em `exact_tf` (forma normalizada sem acento);
    - Registra 1 ocorrência para cada radical distinto em `stem_tf` (Snowball EN + RSLP PT);
    - `doc_len` conta palavras reais (nunca inflado por múltiplas variantes de stemming).

    Na consulta, cada palavra única recebe `max(score_exato, 0.85 * melhor_score_radical)`,
    impedindo que uma palavra pontue 2x-4x por ter múltiplos radicais.
    """

    def __init__(self, stem_weight: float = 0.85):
        self.stem_weight = stem_weight
        self.ids: list[str] = []
        self._exact_tf: list[Counter] = []
        self._stem_tf: list[Counter] = []
        self._doc_len: list[int] = []
        self._exact_df: Counter = Counter()
        self._stem_df: Counter = Counter()
        self._avgdl: float = 0.0
        self._schema_vocab: set[str] = set()

    def build(
        self,
        ids: list[str],
        texts: list[str],
        title_boosts: list[str] | None = None,
        schema_vocab: set[str] | None = None,
        filter_ddl_noise: bool = True,
    ) -> None:
        pt_stemmer, en_stemmer, sw = _get_nltk_resources()
        self._schema_vocab = set(schema_vocab or set())
        self.ids = list(ids)
        self._exact_tf = []
        self._stem_tf = []
        self._doc_len = []
        self._exact_df = Counter()
        self._stem_df = Counter()

        for idx, text in enumerate(texts):
            words = _extract_bm25_words(
                text,
                sw,
                schema_vocab=self._schema_vocab,
                filter_ddl_noise=filter_ddl_noise,
            )
            if title_boosts and title_boosts[idx]:
                title_words = _extract_bm25_words(
                    title_boosts[idx],
                    sw,
                    schema_vocab=self._schema_vocab,
                    filter_ddl_noise=False,
                )
                words = title_words * 2 + words

            e_tf: Counter = Counter()
            s_tf: Counter = Counter()
            for w, w_ascii in words:
                e_tf[w_ascii] += 1
                for st in _stems_for_word(w, w_ascii, pt_stemmer, en_stemmer):
                    s_tf[st] += 1

            self._exact_tf.append(e_tf)
            self._stem_tf.append(s_tf)
            self._doc_len.append(len(words))
            for tok in e_tf:
                self._exact_df[tok] += 1
            for st in s_tf:
                self._stem_df[st] += 1

        total_docs = len(self.ids)
        self._avgdl = sum(self._doc_len) / total_docs if total_docs > 0 else 0.0

    def _bm25_term(
        self,
        tf: int,
        df: int,
        doc_len: int,
        n_docs: int,
        k1: float,
        b: float,
    ) -> float:
        if tf == 0 or df == 0:
            return 0.0
        idf = math.log(((n_docs - df + 0.5) / (df + 0.5)) + 1.0)
        denom = tf + k1 * (1.0 - b + b * (doc_len / self._avgdl))
        return idf * ((tf * (k1 + 1.0)) / denom)

    def retrieve(
        self,
        query: str,
        top_k: int = 50,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> list[tuple[str, float]]:
        if not self.ids or self._avgdl == 0.0:
            return []
        pt_stemmer, en_stemmer, sw = _get_nltk_resources()
        q_words = _extract_bm25_words(
            query,
            sw,
            schema_vocab=self._schema_vocab,
            filter_ddl_noise=False,
        )
        if not q_words:
            return []

        q_counts = Counter(q_words)
        q_stems = {
            pair: _stems_for_word(pair[0], pair[1], pt_stemmer, en_stemmer)
            for pair in q_counts
        }
        n_docs = len(self.ids)
        scores: list[tuple[str, float]] = []

        for idx, doc_id in enumerate(self.ids):
            e_tf = self._exact_tf[idx]
            s_tf = self._stem_tf[idx]
            dl = self._doc_len[idx]
            if dl == 0:
                continue
            total = 0.0
            for pair, q_mult in q_counts.items():
                _, w_ascii = pair
                best = self._bm25_term(
                    e_tf.get(w_ascii, 0),
                    self._exact_df.get(w_ascii, 0),
                    dl,
                    n_docs,
                    k1,
                    b,
                )
                for st in q_stems[pair]:
                    st_score = self.stem_weight * self._bm25_term(
                        s_tf.get(st, 0),
                        self._stem_df.get(st, 0),
                        dl,
                        n_docs,
                        k1,
                        b,
                    )
                    if st_score > best:
                        best = st_score
                total += best * q_mult
            if total > 0.0:
                scores.append((doc_id, total))

        scores.sort(key=lambda item: item[1], reverse=True)
        return scores[:top_k]


class RAGRetriever:
    def __init__(
        self,
        chroma_client: chromadb.Client = None,
        document_schema: dict = None,
        schema_info: list[dict] | dict = None,
        collection_name: str = None,
        use_bm25: bool = True,
        use_embedding: bool = True,
        embedding_function: Any = None,
        embedding_model: str | None = None,
        w_dense: float = 0.55,
        w_bm25: float = 0.45,
    ):
        self.use_bm25 = use_bm25
        self.use_embedding = use_embedding
        self.w_dense = w_dense
        self.w_bm25 = w_bm25

        self.embedding_function = (
            embedding_function
            if embedding_function is not None
            else (
                get_default_embedding_function(embedding_model)
                if use_embedding
                else None
            )
        )
        self.model_id = (
            embedding_model_id(self.embedding_function)
            if self.embedding_function is not None
            else "none"
        )
        self._rule_sim_threshold: float = float(
            getattr(self.embedding_function, "rule_sim_threshold", 0.35)
        )

        # O chroma client deve ser resolvido no engine (singleton compartilhado).
        self.chroma_client = chroma_client or chromadb.PersistentClient(
            path=str(BASE_DIR / "chroma_db")
        )
        self.collection_name = collection_name or f"schema_default_{self.model_id}"
        self.collection = None
        if self.use_embedding:
            self.collection = self.chroma_client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=self.embedding_function,
                metadata={"hnsw:space": "cosine"},
            )

        self._documents_by_id: dict[str, str] = {}
        self._metadatas_by_id: dict[str, dict] = {}
        self._bm25_tables = _BilingualBM25Index()
        self._bm25_rules = _BilingualBM25Index()
        self._bm25_ids: list[str] = []

        self._add_documents(document_schema=document_schema, schema_info=schema_info)

    def _extract_entries(
        self,
        document_schema: dict | None,
        schema_info: list[dict] | dict | None,
    ) -> tuple[list[str], list[str], list[str], list[str], list[dict], set[str]]:
        """
        Retorna:
        - ids: identificadores únicos dos documentos;
        - raw_documents: texto original (DDL / regra) devolvido ao LLM;
        - search_documents: texto limpo usado para embeddings e BM25;
        - title_boosts: nomes/títulos com peso extra no BM25;
        - metadatas: metadados de cada documento;
        - schema_vocab: subtokens dos nomes reais de tabelas/colunas do banco.
        """
        entries: dict[str, tuple[str, str, str, dict]] = {}
        schema_vocab: set[str] = set()

        if document_schema:
            schema_string = (
                document_schema.get("contexto_schema", "")
                if isinstance(document_schema, dict)
                else str(document_schema)
            )
            regex_pattern = r"(Tabela: [\s\S]*?)(?=\nTabela: |$)"
            table_chunks = [
                block.strip() for block in re.findall(regex_pattern, schema_string)
            ]
            for block in table_chunks:
                name_match = re.search(r"^Tabela:[ \t]*(.+?)[ \t]*$", block, re.MULTILINE)
                table_id = (
                    name_match.group(1).strip()
                    if name_match
                    else f"table_{hash(block)}"
                )
                search_text, cols = _clean_ddl_for_search(table_id, block)
                for ident in [table_id, *cols]:
                    for tok in re.findall(
                        r"[a-z0-9]+", _strip_accents(_split_identifier(ident).lower())
                    ):
                        if len(tok) > 1:
                            schema_vocab.add(tok)

                title_boost = f"{table_id} {_split_identifier(table_id)}"
                entries[table_id] = (
                    block,
                    search_text,
                    title_boost,
                    {
                        "tipo": "tabela",
                        "titulo": table_id,
                        "tabelas_alvo": table_id,
                        "colunas_alvo": "",
                        "origem_documento": "schema",
                        "precisa_llm": False,
                    },
                )

        if schema_info:
            info_list = (
                [schema_info] if isinstance(schema_info, dict) else list(schema_info)
            )
            for item in info_list:
                if not isinstance(item, dict):
                    continue
                tipo = str(item.get("tipo", "regra"))
                titulo = str(item.get("titulo", "")).strip()
                descricao = str(item.get("descricao", "")).strip()
                doc_id = str(
                    item.get("id")
                    or f"{tipo}:{hashlib.md5(f'{titulo}:{descricao}'.encode('utf-8')).hexdigest()[:12]}"
                )

                if titulo and descricao and not descricao.lower().startswith(titulo.lower()):
                    raw_text = f"{titulo}: {descricao}"
                else:
                    raw_text = descricao or titulo

                if not raw_text:
                    continue

                tabelas_alvo = item.get("tabelas_alvo") or []
                colunas_alvo = item.get("colunas_alvo") or []

                if tipo in ("tabela", "coluna"):
                    cleaned_md = _clean_markdown_table_doc(descricao or raw_text)
                    alvo_nome = (
                        tabelas_alvo[0]
                        if isinstance(tabelas_alvo, list) and tabelas_alvo
                        else titulo
                    )
                    search_text = (
                        f"Table {alvo_nome} ({_split_identifier(alvo_nome)}).\n{cleaned_md}"
                        if alvo_nome
                        else cleaned_md
                    )
                    title_boost = (
                        f"{alvo_nome} {_split_identifier(alvo_nome)}"
                        if alvo_nome
                        else titulo
                    )
                else:
                    search_text = raw_text
                    title_boost = titulo

                metadata = {
                    "tipo": tipo,
                    "titulo": titulo,
                    "tabelas_alvo": (
                        ",".join(tabelas_alvo)
                        if isinstance(tabelas_alvo, list)
                        else str(tabelas_alvo)
                    ),
                    "colunas_alvo": (
                        ",".join(colunas_alvo)
                        if isinstance(colunas_alvo, list)
                        else str(colunas_alvo)
                    ),
                    "origem_documento": str(item.get("origem_documento", "")),
                    "precisa_llm": bool(item.get("precisa_llm", False)),
                }
                entries[doc_id] = (raw_text, search_text, title_boost, metadata)

        ids = list(entries.keys())
        raw_documents = [entries[i][0] for i in ids]
        search_documents = [entries[i][1] for i in ids]
        title_boosts = [entries[i][2] for i in ids]
        metadatas = [entries[i][3] for i in ids]
        return ids, raw_documents, search_documents, title_boosts, metadatas, schema_vocab

    def _build_bm25_index(
        self,
        ids: list[str],
        raw_documents: list[str],
        search_documents: list[str],
        title_boosts: list[str],
        metadatas: list[dict],
        schema_vocab: set[str],
    ) -> None:
        self._documents_by_id = dict(zip(ids, raw_documents))
        self._metadatas_by_id = dict(
            zip(ids, [m if isinstance(m, dict) else {} for m in metadatas])
        )
        self._bm25_ids = list(ids)

        tbl_ids: list[str] = []
        tbl_texts: list[str] = []
        tbl_titles: list[str] = []
        rule_ids: list[str] = []
        rule_texts: list[str] = []
        rule_titles: list[str] = []

        for doc_id, s_doc, title, meta in zip(
            ids, search_documents, title_boosts, metadatas
        ):
            tipo = meta.get("tipo") if isinstance(meta, dict) else None
            if doc_id.startswith("rule:") or tipo == "regra":
                rule_ids.append(doc_id)
                rule_texts.append(s_doc)
                rule_titles.append(title)
            else:
                tbl_ids.append(doc_id)
                tbl_texts.append(s_doc)
                tbl_titles.append(title)

        self._bm25_tables = _BilingualBM25Index()
        if tbl_ids:
            self._bm25_tables.build(
                ids=tbl_ids,
                texts=tbl_texts,
                title_boosts=tbl_titles,
                schema_vocab=schema_vocab,
                filter_ddl_noise=True,
            )

        self._bm25_rules = _BilingualBM25Index()
        if rule_ids:
            self._bm25_rules.build(
                ids=rule_ids,
                texts=rule_texts,
                title_boosts=rule_titles,
                schema_vocab=schema_vocab,
                filter_ddl_noise=False,
            )

    def _add_documents(
        self,
        document_schema: dict = None,
        schema_info: list[dict] | dict = None,
    ) -> None:
        (
            ids,
            raw_documents,
            search_documents,
            title_boosts,
            metadatas,
            schema_vocab,
        ) = self._extract_entries(
            document_schema=document_schema, schema_info=schema_info
        )

        if not self.use_embedding or self.collection is None:
            if ids:
                self._build_bm25_index(
                    ids, raw_documents, search_documents, title_boosts, metadatas, schema_vocab
                )
            return

        existing_count = self.collection.count()
        if existing_count > 0 and existing_count == len(ids):
            print(f"[RAG] SCHEMA hash'{self.collection_name}' já existe, pulando...")
            if not ids:
                stored = self.collection.get(include=["documents", "metadatas"])
                ids = stored.get("ids") or []
                raw_documents = stored.get("documents") or []
                search_documents = raw_documents
                title_boosts = ["" for _ in ids]
                metadatas = stored.get("metadatas") or [{} for _ in ids]
            self._build_bm25_index(
                ids, raw_documents, search_documents, title_boosts, metadatas, schema_vocab
            )
            return

        print(f"[RAG] Nova SCHEMA detectado, indexando '{self.collection_name}'...")

        if existing_count > 0 and ids:
            existing_ids = set(self.collection.get(include=[])["ids"] or [])
            stale_ids = list(existing_ids - set(ids))
            if stale_ids:
                self.collection.delete(ids=stale_ids)

        if ids:
            # Indexa o texto limpo no ChromaDB; `_documents_by_id` mantém o DDL/texto original.
            self.collection.upsert(
                ids=ids, documents=search_documents, metadatas=metadatas
            )
            self._build_bm25_index(
                ids, raw_documents, search_documents, title_boosts, metadatas, schema_vocab
            )

        print(f"[RAG] Indexing completo para {len(ids)} documentos (tabelas + regras).")

    def _retrieve_bm25(
        self, query: str, top_k: int = 5, k1: float = 1.5, b: float = 0.75
    ) -> list[str]:
        tbl_hits = self._bm25_tables.retrieve(query, top_k=top_k, k1=k1, b=b)
        rule_hits = self._bm25_rules.retrieve(query, top_k=top_k, k1=k1, b=b)
        combined = sorted(tbl_hits + rule_hits, key=lambda item: item[1], reverse=True)
        return [doc_id for doc_id, _ in combined[:top_k]]

    @staticmethod
    def _reciprocal_rank_fusion(
        dense_ids: list[str],
        bm25_ids: list[str],
        k: int = 60,
        top_k: int = 5,
        w_dense: float = 0.55,
        w_bm25: float = 0.45,
    ) -> tuple[list[str], list[float]]:
        rrf_scores: dict[str, float] = {}

        for rank, doc_id in enumerate(dense_ids, start=1):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (w_dense / (k + rank))

        for rank, doc_id in enumerate(bm25_ids, start=1):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (w_bm25 / (k + rank))

        ranked = sorted(rrf_scores.items(), key=lambda item: item[1], reverse=True)[
            :top_k
        ]
        return [doc_id for doc_id, _ in ranked], [score for _, score in ranked]

    def _canonical_table(self, doc_id: str) -> str | None:
        meta = self._metadatas_by_id.get(doc_id) or {}
        tipo = meta.get("tipo")
        if tipo in ("tabela", "coluna"):
            alvos = [
                t.strip()
                for t in str(meta.get("tabelas_alvo", "")).split(",")
                if t.strip()
            ]
            if alvos:
                alvo = alvos[0]
                return alvo if alvo in self._documents_by_id else doc_id
        if doc_id.startswith("tbl:"):
            alvo = doc_id[4:].split("#")[0]
            return alvo if alvo in self._documents_by_id else doc_id
        if doc_id.startswith("col:"):
            alvo = doc_id[4:].split(".")[0]
            return alvo if alvo in self._documents_by_id else doc_id
        if not doc_id.startswith("rule:") and tipo != "regra":
            return doc_id
        return None

    def _retrieve(
        self,
        query: str,
        top_k: int = 5,
        use_bm25: bool | None = None,
        use_embedding: bool | None = None,
    ):
        bm25_ativo = self.use_bm25 if use_bm25 is None else use_bm25
        embedding_ativo = (
            self.use_embedding if use_embedding is None else use_embedding
        ) and self.collection is not None

        chroma_count = self.collection.count() if embedding_ativo else 0
        total_docs = max(chroma_count, len(self._bm25_ids))
        if total_docs == 0 or (not bm25_ativo and not embedding_ativo):
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

        n_results = min(top_k, total_docs)
        candidate_k = min(max(top_k * 8, 60), total_docs)

        dense_ids: list[str] = []
        dense_distances_by_id: dict[str, float] = {}
        if embedding_ativo and chroma_count > 0:
            dense_results = self.collection.query(
                query_texts=[query],
                n_results=min(candidate_k, chroma_count),
            )
            dense_ids = (
                dense_results["ids"][0]
                if dense_results and dense_results.get("ids")
                else []
            )
            if (
                dense_results
                and dense_results.get("distances")
                and dense_results["distances"][0]
            ):
                for idx, doc_id in enumerate(dense_ids):
                    dense_distances_by_id[doc_id] = dense_results["distances"][0][idx]

            if dense_results and dense_results.get("documents"):
                for idx, doc_id in enumerate(dense_ids):
                    if doc_id not in self._documents_by_id:
                        self._documents_by_id[doc_id] = dense_results["documents"][0][idx]
                    if (
                        dense_results.get("metadatas")
                        and dense_results["metadatas"][0]
                        and doc_id not in self._metadatas_by_id
                    ):
                        self._metadatas_by_id[doc_id] = (
                            dense_results["metadatas"][0][idx] or {}
                        )

        dense_tbls: list[str] = []
        dense_rules: list[str] = []
        dense_rule_sims: dict[str, float] = {}
        for doc_id in dense_ids:
            tbl = self._canonical_table(doc_id)
            if tbl:
                if tbl not in dense_tbls:
                    dense_tbls.append(tbl)
            elif doc_id not in dense_rules:
                dense_rules.append(doc_id)
                dist = dense_distances_by_id.get(doc_id)
                if dist is not None:
                    dense_rule_sims[doc_id] = 1.0 - float(dist)

        bm25_tbls: list[str] = []
        bm25_rules: list[str] = []
        if bm25_ativo:
            for doc_id, _ in self._bm25_tables.retrieve(query=query, top_k=candidate_k):
                tbl = self._canonical_table(doc_id)
                if tbl and tbl not in bm25_tbls:
                    bm25_tbls.append(tbl)
            bm25_rules = [
                doc_id
                for doc_id, _ in self._bm25_rules.retrieve(query=query, top_k=10)
            ]

        # Filtra regras densas por limiar de similaridade cosseno (permite regras
        # semânticas mesmo sem overlap léxico no BM25, evitando ruído de baixa confiança).
        qualified_dense_rules = [
            r
            for r in dense_rules[:10]
            if dense_rule_sims.get(r, 0.0) >= self._rule_sim_threshold
        ]

        if embedding_ativo and bm25_ativo:
            fused_tbls, tbl_scores = self._reciprocal_rank_fusion(
                dense_ids=dense_tbls,
                bm25_ids=bm25_tbls,
                top_k=n_results,
                w_dense=self.w_dense,
                w_bm25=self.w_bm25,
            )
            active_rules, _ = self._reciprocal_rank_fusion(
                dense_ids=qualified_dense_rules,
                bm25_ids=bm25_rules[:5],
                top_k=3,
                w_dense=self.w_dense,
                w_bm25=self.w_bm25,
            )
        elif embedding_ativo:
            fused_tbls = dense_tbls[:n_results]
            tbl_scores = [
                dense_distances_by_id.get(doc_id, 0.0) for doc_id in fused_tbls
            ]
            active_rules = (qualified_dense_rules or dense_rules)[:2]
        else:
            fused_tbls, tbl_scores = self._reciprocal_rank_fusion(
                dense_ids=[],
                bm25_ids=bm25_tbls,
                top_k=n_results,
                w_dense=0.0,
                w_bm25=1.0,
            )
            active_rules = bm25_rules[:3]

        final_ids = list(fused_tbls[:n_results])
        final_scores = list(tbl_scores[: len(final_ids)])
        for rule_id in active_rules:
            if rule_id not in final_ids:
                final_ids.append(rule_id)
                final_scores.append(0.0)

        final_documents = [self._documents_by_id.get(doc_id, "") for doc_id in final_ids]
        final_metadatas = [self._metadatas_by_id.get(doc_id, {}) for doc_id in final_ids]

        return {
            "ids": [final_ids],
            "documents": [final_documents],
            "metadatas": [final_metadatas],
            "distances": [final_scores],
        }

    def _query(
        self,
        query: str,
        top_k: int = 5,
        use_bm25: bool | None = None,
        use_embedding: bool | None = None,
    ):
        return self._retrieve(
            query=query,
            top_k=top_k,
            use_bm25=use_bm25,
            use_embedding=use_embedding,
        )


if __name__ == "__main__":
    schema = SCHEMA
    chroma_client = chromadb.PersistentClient(path=str(BASE_DIR / "chroma_db"))
    retriever = RAGRetriever(chroma_client=chroma_client, document_schema=schema)
    query = "Which columns do we have on the table orders?"
    results = retriever._query(query=query, top_k=3)
    print("Resultados da consulta:")
    print(results["documents"][0])
    for i, table_content in enumerate(results["documents"][0]):
        print(f"--- Result {i+1} ---")
        print(table_content)
