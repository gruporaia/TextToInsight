import re
import math
import hashlib
import unicodedata
from collections import Counter
from pathlib import Path
import chromadb
from .RAG_example import SCHEMA
#o RAG em si vai ser configurado aqui

BASE_DIR = Path(__file__).resolve().parent


def _tokenize_bm25(text: str) -> list[str]:
    if not text:
        return []
    normalized = (
        unicodedata.normalize("NFKD", text.lower())
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    words = re.findall(r"[a-z0-9]+", normalized)
    compounds = [t for t in re.findall(r"[a-z0-9_]+", normalized) if "_" in t]
    return words + compounds


class RAGRetriever:
    def __init__(
        self,
        chroma_client: chromadb.Client = None,
        document_schema: dict = None,
        schema_info: list[dict] | dict = None,
        collection_name: str = None,
        use_bm25: bool = True,
        use_embedding: bool = True,
    ):
        #o chroma client deve ser resolvido no engine, criando uma instancia unica e compartilhada, para evitar overhead de conexões
        self.chroma_client = chroma_client or chromadb.PersistentClient(
            path=str(BASE_DIR / "chroma_db")
        )
        #a collection name agora é meio inútil, mas é bom manter a flexibilidade de ter mais de uma collection, caso seja necessário indexar outros tipos de documentos no futuro
        self.collection_name = collection_name or "schema_default"
        self.collection = self.chroma_client.get_or_create_collection(
            name=self.collection_name
        )
        self.use_bm25 = use_bm25
        self.use_embedding = use_embedding

        self._documents_by_id: dict[str, str] = {}
        self._metadatas_by_id: dict[str, dict] = {}
        self._bm25_ids: list[str] = []
        self._bm25_tf: list[Counter] = []
        self._bm25_doc_len: list[int] = []
        self._bm25_df: Counter = Counter()
        self._bm25_avgdl: float = 0.0

        self._add_documents(document_schema=document_schema, schema_info=schema_info)

    def _extract_entries(
        self,
        document_schema: dict | None,
        schema_info: list[dict] | dict | None,
    ) -> tuple[list[str], list[str], list[dict]]:
        entries: dict[str, tuple[str, dict]] = {}

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
                name_match = re.search(r"Tabela:\s+(\w+)", block)
                table_id = (
                    name_match.group(1) if name_match else f"table_{hash(block)}"
                )
                entries[table_id] = (
                    block,
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
                    doc_text = f"{titulo}: {descricao}"
                else:
                    doc_text = descricao or titulo

                if not doc_text:
                    continue

                tabelas_alvo = item.get("tabelas_alvo") or []
                colunas_alvo = item.get("colunas_alvo") or []
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
                entries[doc_id] = (doc_text, metadata)

        ids = list(entries.keys())
        documents = [entries[i][0] for i in ids]
        metadatas = [entries[i][1] for i in ids]
        return ids, documents, metadatas

    def _build_bm25_index(
        self, ids: list[str], documents: list[str], metadatas: list[dict]
    ):
        self._documents_by_id = dict(zip(ids, documents))
        self._metadatas_by_id = dict(
            zip(ids, [m if isinstance(m, dict) else {} for m in metadatas])
        )
        self._bm25_ids = list(ids)
        self._bm25_tf = []
        self._bm25_doc_len = []
        self._bm25_df = Counter()

        for doc in documents:
            tokens = _tokenize_bm25(doc)
            tf = Counter(tokens)
            self._bm25_tf.append(tf)
            self._bm25_doc_len.append(len(tokens))
            for token in tf.keys():
                self._bm25_df[token] += 1

        total_docs = len(documents)
        self._bm25_avgdl = (
            sum(self._bm25_doc_len) / total_docs if total_docs > 0 else 0.0
        )

    def _add_documents(
        self,
        document_schema: dict = None,
        schema_info: list[dict] | dict = None,
    ):
        ids, documents, metadatas = self._extract_entries(
            document_schema=document_schema, schema_info=schema_info
        )

        existing_count = self.collection.count()
        if existing_count > 0 and existing_count >= len(ids):
            print(f"[RAG] SCHEMA hash'{self.collection_name}' já existe, pulando...")
            if not ids:
                stored = self.collection.get(include=["documents", "metadatas"])
                ids = stored.get("ids") or []
                documents = stored.get("documents") or []
                metadatas = stored.get("metadatas") or [{} for _ in ids]
            self._build_bm25_index(ids, documents, metadatas)
            return

        print(f"[RAG] Nova SCHEMA detectado, indexando '{self.collection_name}'...")

        if ids:
            self.collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
            self._build_bm25_index(ids, documents, metadatas)

        print(f"[RAG] Indexing completo para {len(ids)} documentos (tabelas + regras).")

    def _retrieve_bm25(
        self, query: str, top_k: int = 5, k1: float = 1.5, b: float = 0.75
    ) -> list[str]:
        if not self._bm25_ids or self._bm25_avgdl == 0:
            return []

        query_tokens = _tokenize_bm25(query)
        if not query_tokens:
            return []

        n_docs = len(self._bm25_ids)
        scores: list[tuple[str, float]] = []

        for idx, doc_id in enumerate(self._bm25_ids):
            tf = self._bm25_tf[idx]
            doc_len = self._bm25_doc_len[idx]
            score = 0.0
            for token in query_tokens:
                f = tf.get(token, 0)
                if f == 0:
                    continue
                df = self._bm25_df.get(token, 0)
                idf = math.log(((n_docs - df + 0.5) / (df + 0.5)) + 1.0)
                denom = f + k1 * (1.0 - b + b * (doc_len / self._bm25_avgdl))
                score += idf * ((f * (k1 + 1.0)) / denom)
            if score > 0.0:
                scores.append((doc_id, score))

        scores.sort(key=lambda item: item[1], reverse=True)
        return [doc_id for doc_id, _ in scores[:top_k]]

    @staticmethod
    def _reciprocal_rank_fusion(
        dense_ids: list[str],
        bm25_ids: list[str],
        k: int = 60,
        top_k: int = 5,
    ) -> tuple[list[str], list[float]]:
        rrf_scores: dict[str, float] = {}

        for rank, doc_id in enumerate(dense_ids, start=1):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (1.0 / (k + rank))

        for rank, doc_id in enumerate(bm25_ids, start=1):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (1.0 / (k + rank))

        ranked = sorted(rrf_scores.items(), key=lambda item: item[1], reverse=True)[
            :top_k
        ]
        return [doc_id for doc_id, _ in ranked], [score for _, score in ranked]

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
        )

        chroma_count = self.collection.count()
        total_docs = max(chroma_count, len(self._bm25_ids))
        if total_docs == 0 or (not bm25_ativo and not embedding_ativo):
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

        n_results = min(top_k, total_docs)
        candidate_k = min(max(top_k * 2, 10), total_docs)

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

        bm25_ids: list[str] = []
        if bm25_ativo:
            bm25_ids = self._retrieve_bm25(query=query, top_k=candidate_k)

        if embedding_ativo and bm25_ativo:
            final_ids, final_scores = self._reciprocal_rank_fusion(
                dense_ids=dense_ids,
                bm25_ids=bm25_ids,
                top_k=n_results,
            )
        elif embedding_ativo:
            final_ids = dense_ids[:n_results]
            final_scores = [
                dense_distances_by_id.get(doc_id, 0.0) for doc_id in final_ids
            ]
        else:
            final_ids, final_scores = self._reciprocal_rank_fusion(
                dense_ids=[],
                bm25_ids=bm25_ids,
                top_k=n_results,
            )

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
    
#esse teste simples funcionou até que bem, a query esta em inglês pois o sample que estou usando aqui está em inglês, mas o ideal é testar com um sample em português depois
if __name__ == "__main__":
    schema = SCHEMA
    chroma_client = chromadb.PersistentClient(path=str(BASE_DIR / "chroma_db"))
    retriever = RAGRetriever(chroma_client=chroma_client, document_schema=schema)
    query = "Which columns do we have on the table orders?"
    results = retriever._query(query=query, top_k=3)
    print("Resultados da consulta:")
    print(results['documents'][0])
    for i, table_content in enumerate(results['documents'][0]):
        print(f"--- Result {i+1} ---")
        print(table_content)