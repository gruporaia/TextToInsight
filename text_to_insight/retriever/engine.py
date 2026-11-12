import hashlib
from functools import lru_cache
from typing import List
from .graph_logic import SchemaGraph
from .rag_logic import RAGRetriever
from pathlib import Path
import chromadb

import json
try:
    from .docs_linker import carregar_documentos_md, extrair_trechos
except ImportError:
    from docs_linker import carregar_documentos_md, extrair_trechos
from ..cache import (
    diretorio_chroma,
    ler_trechos_regras,
    resolver_cache_dir,
    salvar_trechos_regras,
)

_default_extrair_trechos = extrair_trechos
#aqui vamos ter a integração da lógica do grafo com a lógica do RAG
#a ideia é que o RAG seja responsável por recuperar as informações relevantes para responder às perguntas, usando o grafo como guia para navegar pelas relações entre os dados

@lru_cache(maxsize=4)
def _get_cached_chroma_client(target_str: str):
    Path(target_str).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=target_str)

def get_chroma_client(cache_dir: str | Path | None = None):
    target = diretorio_chroma(cache_dir)
    return _get_cached_chroma_client(str(target.resolve()))

@lru_cache(maxsize=8)
def _get_cached_trechos(
    schema_hash: str,
    rules_string: str,
    schema_string: str,
    resolved_cache_dir_str: str,
) -> tuple[dict, ...]:
    if not rules_string:
        return ()
    em_cache = ler_trechos_regras(resolved_cache_dir_str, schema_hash)
    if em_cache is not None:
        return tuple(em_cache)
    extraidos = extrair_trechos(rules_string, schema_string)
    salvar_trechos_regras(resolved_cache_dir_str, schema_hash, extraidos)
    return tuple(extraidos)

@lru_cache(maxsize=8)
def _get_cached_schema_graph(
    schema_hash: str,
    schema_string: str,
    rules_string: str,
    resolved_cache_dir_str: str,
    trechos_override: tuple[str, ...] | None = None,
) -> SchemaGraph:
    if trechos_override is not None:
        trechos = [json.loads(t) for t in trechos_override]
    else:
        trechos = list(
            _get_cached_trechos(
                schema_hash, rules_string, schema_string, resolved_cache_dir_str
            )
        )
    return SchemaGraph(
        schema=schema_string,
        schema_info=trechos,
    )

@lru_cache(maxsize=8)
def _get_cached_rag_retriever(
    schema_hash: str,
    schema_string: str,
    rules_string: str,
    resolved_cache_dir_str: str,
    use_bm25: bool = True,
    use_embedding: bool = True,
    trechos_override: tuple[str, ...] | None = None,
) -> RAGRetriever:
    if trechos_override is not None:
        trechos = [json.loads(t) for t in trechos_override]
    else:
        trechos = list(
            _get_cached_trechos(
                schema_hash, rules_string, schema_string, resolved_cache_dir_str
            )
        )
    chroma_client = get_chroma_client(resolved_cache_dir_str)
    collection_name = f"schema_{schema_hash}"
    return RAGRetriever(
        chroma_client=chroma_client,
        document_schema={"contexto_schema": schema_string},
        schema_info=trechos,
        collection_name=collection_name,
        use_bm25=use_bm25,
        use_embedding=use_embedding,
    )

class SchemaGraphRAG:
    def __init__(
        self,
        schema: dict = None,
        rules: List[dict] = None,
        docs_dir: str | Path | None = None,
        cache_dir: str | Path | None = None,
        use_bm25: bool = True,
        use_embedding: bool = True,
        steiner_apenas_tabelas: bool = True,
    ):
        self.use_bm25 = use_bm25
        self.use_embedding = use_embedding
        self.steiner_apenas_tabelas = steiner_apenas_tabelas
        #definição do grafo após o get do schema
        if schema:
            regras_combinadas = [dict(r) for r in rules if isinstance(r, dict)] if rules else []
            if docs_dir:
                regras_combinadas.extend(carregar_documentos_md(docs_dir))

            rules_string = (
                "\n\n".join(
                    rule["contexto_regras"].strip()
                    for rule in regras_combinadas
                    if "contexto_regras" in rule and rule["contexto_regras"].strip()
                )
                if regras_combinadas
                else ""
            )
            schema_string = schema.get("contexto_schema", "")

            resolved_cache_dir_str = str(resolver_cache_dir(cache_dir).resolve())
            trechos_override = None
            if extrair_trechos is not _default_extrair_trechos and rules_string:
                extraidos = extrair_trechos(rules_string, schema_string)
                trechos_override = tuple(
                    json.dumps(t, ensure_ascii=False, sort_keys=True) for t in extraidos
                )
                schema_hash = hashlib.md5(
                    f"{schema_string}\n---CUSTOM_TRECHOS---\n{''.join(trechos_override)}".encode("utf-8")
                ).hexdigest()
            else:
                schema_hash = hashlib.md5(
                    f"{schema_string}\n---RULES---\n{rules_string}".encode("utf-8")
                ).hexdigest()

            self.schema_graph = _get_cached_schema_graph(
                schema_hash=schema_hash,
                schema_string=schema_string,
                rules_string=rules_string,
                resolved_cache_dir_str=resolved_cache_dir_str,
                trechos_override=trechos_override,
            )
            self.rag = _get_cached_rag_retriever(
                schema_hash=schema_hash,
                schema_string=schema_string,
                rules_string=rules_string,
                resolved_cache_dir_str=resolved_cache_dir_str,
                use_bm25=use_bm25,
                use_embedding=use_embedding,
                trechos_override=trechos_override,
            )

    def retrieve(self, query: str, top_k: int = 5):
        #a query no no schema vai rolar aqui, essa função deve:
        # 1. acessar as tables e colunas relevantes
        # 2. usar as relações do grafo para navegar entre as tabelas e colunas
        # 3. recuperar os dados relevantes para responder à pergunta com as relações necessárias para ligar os dados

        #o importante aqui é encontrar o caminho mais curto que liga as tabelas retornadas pelo RAG com base no grafo
        #que foi produzido com o schema fornecido
        retrieved_tables = self.rag._query(
            query,
            top_k=top_k,
            use_bm25=self.use_bm25,
            use_embedding=self.use_embedding,
        )
        initial_ids = list(retrieved_tables["ids"][0]) if retrieved_tables.get("ids") else []
        initial_docs = list(retrieved_tables["documents"][0]) if retrieved_tables.get("documents") else []
        docs_por_id = dict(zip(initial_ids, initial_docs))

        relations, missing_tables = self.schema_graph._get_relations(
            initial_ids,
            steiner_apenas_tabelas=self.steiner_apenas_tabelas,
        )
        if missing_tables:
            print(f"[SchemaGraphRAG] Warning: The following tables were retrieved by RAG but are missing in the graph: {missing_tables}")

        nos_relacionados = set()
        for rel in relations:
            nos_relacionados.add(rel[0])
            nos_relacionados.add(rel[1])

        for n in sorted(nos_relacionados - set(initial_ids)):
            schema_txt = self.schema_graph.get_schema(n)
            if schema_txt:
                docs_por_id[n] = schema_txt

        def _eh_regra(node_id: str) -> bool:
            if node_id in self.schema_graph.rule_schemas:
                return True
            if node_id in self.schema_graph.graph:
                return self.schema_graph.graph.nodes[node_id].get("tipo") == "regra"
            return False

        def _eh_tabela(node_id: str) -> bool:
            if node_id in self.schema_graph.table_schemas:
                return True
            if node_id in self.schema_graph.graph:
                return self.schema_graph.graph.nodes[node_id].get("tipo") == "tabela"
            return not _eh_regra(node_id)

        def _alvos_da_regra(rule_id: str) -> list[str]:
            alvos = []
            if rule_id in self.schema_graph.graph:
                for t in self.schema_graph.graph.nodes[rule_id].get("tabelas_alvo", []) or []:
                    if t not in alvos and _eh_tabela(t):
                        alvos.append(t)
            for rel in relations:
                if rel[0] == rule_id and rel[2] == "aplica_se" and _eh_tabela(rel[1]):
                    if rel[1] not in alvos:
                        alvos.append(rel[1])
            return alvos

        # 1. Coleta regras e tabelas preservando relevância e determinismo
        regras_ativas: list[str] = []
        tabelas_ordenadas: list[str] = []

        for node_id in initial_ids:
            if _eh_regra(node_id):
                if node_id not in regras_ativas:
                    regras_ativas.append(node_id)
                for t_alvo in _alvos_da_regra(node_id):
                    if t_alvo in docs_por_id and t_alvo not in tabelas_ordenadas:
                        tabelas_ordenadas.append(t_alvo)
            elif _eh_tabela(node_id):
                if node_id in docs_por_id and node_id not in tabelas_ordenadas:
                    tabelas_ordenadas.append(node_id)

        for node_id in sorted(nos_relacionados):
            if _eh_regra(node_id) and node_id in docs_por_id and node_id not in regras_ativas:
                regras_ativas.append(node_id)
            elif _eh_tabela(node_id) and node_id in docs_por_id and node_id not in tabelas_ordenadas:
                tabelas_ordenadas.append(node_id)

        # 2. Expansão Tabela -> Regra: recupera todas as regras associadas às tabelas retornadas
        relacoes_set = set(relations)
        for rule_id in self.schema_graph.get_rules_for_tables(tabelas_ordenadas):
            if rule_id not in docs_por_id:
                schema_txt = self.schema_graph.get_schema(rule_id)
                if schema_txt:
                    docs_por_id[rule_id] = schema_txt
            if rule_id in docs_por_id and rule_id not in regras_ativas:
                regras_ativas.append(rule_id)
            for t_alvo in _alvos_da_regra(rule_id):
                if t_alvo in tabelas_ordenadas:
                    rel_item = (rule_id, t_alvo, "aplica_se", t_alvo)
                    if rel_item not in relacoes_set:
                        relacoes_set.add(rel_item)
                        relations.append(rel_item)

        # 3. Matching Regra -> Tabela: cada regra é associada às tabelas às quais se refere
        regras_por_tabela: dict[str, list[dict]] = {t: [] for t in tabelas_ordenadas}
        regras_sem_tabela: list[dict] = []

        for rule_id in regras_ativas:
            rule_doc = docs_por_id.get(rule_id) or self.schema_graph.get_schema(rule_id) or ""
            alvos = [t for t in _alvos_da_regra(rule_id) if t in regras_por_tabela]
            if alvos:
                for t in alvos:
                    regras_por_tabela[t].append({"id": rule_id, "documento": rule_doc})
            else:
                regras_sem_tabela.append({"id": rule_id, "documento": rule_doc})

        # 3. Ordena ids e documents colocando a regra imediatamente antes da tabela a que se refere
        ordered_ids: list[str] = []
        ordered_docs: list[str] = []

        for r_info in regras_sem_tabela:
            if r_info["id"] not in ordered_ids:
                ordered_ids.append(r_info["id"])
                ordered_docs.append(r_info["documento"])

        for tabela in tabelas_ordenadas:
            for r_info in regras_por_tabela.get(tabela, []):
                if r_info["id"] not in ordered_ids:
                    ordered_ids.append(r_info["id"])
                    ordered_docs.append(r_info["documento"])
            if tabela not in ordered_ids and tabela in docs_por_id:
                ordered_ids.append(tabela)
                ordered_docs.append(docs_por_id[tabela])

        retrieved_tables["ids"] = [ordered_ids]
        retrieved_tables["documents"] = [ordered_docs]
        retrieved_tables["tabelas_ordenadas"] = tabelas_ordenadas
        retrieved_tables["tabelas_documentos"] = {t: docs_por_id[t] for t in tabelas_ordenadas if t in docs_por_id}
        retrieved_tables["regras_por_tabela"] = regras_por_tabela
        retrieved_tables["regras_sem_tabela"] = regras_sem_tabela
        return retrieved_tables, relations
    
if __name__ == '__main__':
    from .RAG_example import SCHEMA
    schema_graph_rag = SchemaGraphRAG(schema = SCHEMA)
    retrieved_tables, relations = schema_graph_rag.retrieve("What are the total sales for each customer?")
    print("Retrieved Tables:", retrieved_tables['ids'])
    print("Relations:", relations)