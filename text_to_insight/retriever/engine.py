import hashlib
import re
from functools import lru_cache
from typing import Any, List
from .embeddings import embedding_model_id, get_default_embedding_function
from .graph_logic import SchemaGraph
from .rag_logic import RAGRetriever
from .reranker import (
    DEFAULT_PRUNER_MODEL,
    GraphAwareFKBooster,
    LLMPruner,
    _empty_token_update,
)
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
    embedding_model: str | None = None,
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
    ef = get_default_embedding_function(embedding_model) if use_embedding else None
    model_id = embedding_model_id(ef) if ef is not None else "none"
    collection_name = f"schema_{model_id}_{schema_hash}"
    return RAGRetriever(
        chroma_client=chroma_client,
        document_schema={"contexto_schema": schema_string},
        schema_info=trechos,
        collection_name=collection_name,
        use_bm25=use_bm25,
        use_embedding=use_embedding,
        embedding_function=ef,
        embedding_model=embedding_model,
    )

_MD_SEP_RE = re.compile(r"^[\|\s:\-]+$")
_USOS_PADRAO = {"", "livre", "public", "público", "publico", "ok", "-"}


def _texto_bruto_trecho(schema_graph: SchemaGraph, node_id: str) -> str:
    """Retorna a descrição bruta de um nó `tbl:`/`col:` sem o prefixo `titulo:`."""
    grafo = getattr(schema_graph, "graph", None)
    if grafo is not None and node_id in grafo:
        desc = grafo.nodes[node_id].get("descricao")
        if isinstance(desc, str) and desc.strip():
            return desc.strip()
    return (getattr(schema_graph, "rule_schemas", {}).get(node_id) or "").strip()


def _partes_ordenadas_doc(schema_graph: SchemaGraph, prefixo_exato: str) -> list[str]:
    """Coleta `prefixo_exato` e eventuais partes `prefixo_exato#2`, `#3`, ... em ordem."""
    rule_schemas = getattr(schema_graph, "rule_schemas", {})
    encontrados: list[tuple[int, str]] = []
    if prefixo_exato in rule_schemas:
        encontrados.append((1, prefixo_exato))
    pref_hash = f"{prefixo_exato}#"
    for key in rule_schemas:
        if key.startswith(pref_hash):
            suf = key[len(pref_hash):]
            num = int(suf) if suf.isdigit() else 999
            encontrados.append((num, key))
    encontrados.sort(key=lambda item: item[0])
    return [_texto_bruto_trecho(schema_graph, k) for _, k in encontrados if _texto_bruto_trecho(schema_graph, k)]


def _parse_markdown_table_cols(texto: str) -> dict[str, str]:
    """
    Extrai anotações compactas por coluna (`descrição | valores: ... | uso: ...`)
    a partir de tabelas Markdown em trechos `tbl:<tabela>`, descartando colunas
    redundantes com o DDL (tipo, obrigatoriedade, chave).
    """
    notas: dict[str, str] = {}
    linhas = texto.splitlines()
    idx_col: int | None = None
    idx_vals: int | None = None
    idx_uso: int | None = None
    idx_desc: int | None = None

    for linha in linhas:
        s = linha.strip()
        if not s.startswith("|"):
            continue
        if _MD_SEP_RE.match(s):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 2:
            continue

        # Detecta cabeçalho da tabela Markdown
        if idx_col is None:
            low_cells = [c.lower() for c in cells]
            eh_header = any(
                k in low_cells[0]
                for k in ("coluna", "column", "campo", "field", "nome", "name")
            ) or any(
                any(k in cell for k in ("descri", "valor", "value", "tipo", "type"))
                for cell in low_cells[1:]
            )
            if eh_header:
                idx_col = 0
                for i, h in enumerate(low_cells):
                    if idx_vals is None and any(k in h for k in ("valor", "value", "enum", "opç", "opc", "domín", "domin")):
                        idx_vals = i
                    elif idx_uso is None and any(k in h for k in ("uso", "usage", "acesso", "restri", "polít", "polit")):
                        idx_uso = i
                    elif any(k in h for k in ("descri", "significado", "obs", "nota", "coment")):
                        idx_desc = i
                if idx_desc is None:
                    idx_desc = len(cells) - 1
                continue
            # Tabela sem cabeçalho explícito: assume coluna 0 = nome, última = descrição
            idx_col = 0
            idx_desc = len(cells) - 1

        if idx_col >= len(cells):
            continue
        col_nome = cells[idx_col].strip().strip("`").strip()
        if not col_nome or " " in col_nome:
            continue

        desc = cells[idx_desc].strip() if (idx_desc is not None and idx_desc < len(cells)) else ""
        vals = cells[idx_vals].strip() if (idx_vals is not None and idx_vals < len(cells)) else ""
        uso = cells[idx_uso].strip() if (idx_uso is not None and idx_uso < len(cells)) else ""

        partes: list[str] = []
        if desc:
            partes.append(desc)
        if vals:
            partes.append(f"valores: {vals}")
        if uso and uso.lower() not in _USOS_PADRAO:
            partes.append(f"uso: {uso}")
        if partes:
            notas[col_nome] = " | ".join(partes)

    return notas


def _extrair_doc_tabela_compacta(
    schema_graph: SchemaGraph,
    tabela: str,
) -> tuple[str, dict[str, str]]:
    """
    Reúne resumo narrativo curto da tabela e anotações compactas por coluna
    a partir dos trechos `tbl:<tabela>` (incluindo multi-part `#2`, `#3`) e `col:<tabela>.<col>`.
    """
    partes_tbl = _partes_ordenadas_doc(schema_graph, f"tbl:{tabela}")
    texto_tbl = "\n".join(partes_tbl)

    resumo = ""
    if texto_tbl:
        for ln in texto_tbl.splitlines():
            ln_s = ln.strip()
            if not ln_s:
                continue
            if ln_s.startswith("|"):
                break
            if ln_s.startswith(("- **Volume", "- **Aponta", "- **Referenciada", "- **Restrições", "- **Restricoes")):
                continue
            ln_s = re.sub(rf"^{re.escape(tabela)}(?:\s*\(parte \d+\))?:\s*", "", ln_s)
            if ln_s:
                resumo = ln_s
                break

    notas_cols = _parse_markdown_table_cols(texto_tbl) if texto_tbl else {}

    # Também incorpora trechos individuais `col:<tabela>.<coluna>` se existirem
    rule_schemas = getattr(schema_graph, "rule_schemas", {})
    pref_col = f"col:{tabela}."
    cols_vistas: set[str] = set()
    for key in sorted(rule_schemas):
        if key.startswith(pref_col):
            col_nome = key[len(pref_col):].split("#")[0]
            if col_nome in cols_vistas:
                continue
            cols_vistas.add(col_nome)
            doc_col = " ".join(_partes_ordenadas_doc(schema_graph, f"{pref_col}{col_nome}")).strip()
            if doc_col:
                doc_col_limpo = " ".join(doc_col.split())
                if col_nome in notas_cols:
                    notas_cols[col_nome] = f"{notas_cols[col_nome]} | {doc_col_limpo}"
                else:
                    notas_cols[col_nome] = doc_col_limpo

    return resumo, notas_cols


def enriquecer_ddl_com_dicionario(
    schema_graph: SchemaGraph,
    tabela: str,
    ddl: str,
) -> str:
    """
    Injeta inline no DDL de uma tabela selecionada o resumo curto (`  Descrição: ...`)
    e as descrições/enums/restrições de uso por coluna (`-- [...]`) vindos de
    `tbl:<tabela>` e `col:<tabela>.<col>`, preservando 100% da compatibilidade com
    o parser do `data_exploration`.
    """
    if not ddl:
        return ddl
    resumo, notas_cols = _extrair_doc_tabela_compacta(schema_graph, tabela)
    if not resumo and not notas_cols:
        return ddl

    notas_lower = {k.lower(): v for k, v in notas_cols.items()}
    linhas_out: list[str] = []
    em_fk = False

    for linha in ddl.splitlines():
        s = linha.strip()
        if s.startswith("Tabela:"):
            linhas_out.append(linha)
            if resumo:
                linhas_out.append(f"  Descrição: {resumo}")
            continue
        if s.startswith("Foreign keys:"):
            em_fk = True
            linhas_out.append(linha)
            continue
        if not em_fk and s.startswith("- ") and ":" in s and " -- [" not in linha:
            col_nome = s[2:].split(":", 1)[0].strip()
            nota = notas_cols.get(col_nome) or notas_lower.get(col_nome.lower())
            if nota:
                linhas_out.append(f"{linha} -- [{nota}]")
                continue
        linhas_out.append(linha)

    return "\n".join(linhas_out)


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
        nao_expandir_regras_tabelas: bool = False,
        incluir_doc_tabelas: bool = True,
        embedding_model: str | None = None,
        embedding_function: Any = None,
        max_tabelas_por_regra: int | None = 2,
        use_fk_boost: bool = True,
        fk_pool_k: int = 20,
        fk_num_ancoras: int = 3,
        fk_alpha: float = 0.25,
        fk_w_pai: float = 1.0,
        fk_w_filho: float = 0.30,
        use_llm_pruner: bool = True,
        pruner_llm: Any = None,
        pruner_model: str = DEFAULT_PRUNER_MODEL,
        pruner_api_key: str | None = None,
        pruner_fallback_api_key: str | None = None,
        pruner_fallback_model_name: str | None = None,
        pruner_pool_k: int = 12,
        pruner_reasoning_effort: str | None = None,
    ):
        self.use_bm25 = use_bm25
        self.use_embedding = use_embedding
        self.steiner_apenas_tabelas = steiner_apenas_tabelas
        self.nao_expandir_regras_tabelas = nao_expandir_regras_tabelas
        self.incluir_doc_tabelas = incluir_doc_tabelas
        self.embedding_model = embedding_model
        self.max_tabelas_por_regra = max_tabelas_por_regra
        self.use_fk_boost = use_fk_boost
        self.fk_pool_k = fk_pool_k
        self.fk_num_ancoras = fk_num_ancoras
        self.fk_alpha = fk_alpha
        self.fk_w_pai = fk_w_pai
        self.fk_w_filho = fk_w_filho
        self.use_llm_pruner = use_llm_pruner
        self.pruner_model = pruner_model
        self.pruner_pool_k = pruner_pool_k
        self._fk_booster: GraphAwareFKBooster | None = None
        self._pruner: LLMPruner | None = None
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
            if embedding_function is not None:
                trechos = (
                    [json.loads(t) for t in trechos_override]
                    if trechos_override is not None
                    else list(
                        _get_cached_trechos(
                            schema_hash, rules_string, schema_string, resolved_cache_dir_str
                        )
                    )
                )
                chroma_client = get_chroma_client(resolved_cache_dir_str)
                model_id = embedding_model_id(embedding_function)
                self.rag = RAGRetriever(
                    chroma_client=chroma_client,
                    document_schema={"contexto_schema": schema_string},
                    schema_info=trechos,
                    collection_name=f"schema_{model_id}_{schema_hash}",
                    use_bm25=use_bm25,
                    use_embedding=use_embedding,
                    embedding_function=embedding_function,
                )
            else:
                self.rag = _get_cached_rag_retriever(
                    schema_hash=schema_hash,
                    schema_string=schema_string,
                    rules_string=rules_string,
                    resolved_cache_dir_str=resolved_cache_dir_str,
                    use_bm25=use_bm25,
                    use_embedding=use_embedding,
                    trechos_override=trechos_override,
                    embedding_model=embedding_model,
                )

            self._fk_booster = GraphAwareFKBooster(self.schema_graph)
            self._pruner = LLMPruner(
                schema_graph=self.schema_graph,
                llm=pruner_llm,
                model_name=pruner_model,
                api_key=pruner_api_key,
                fallback_api_key=pruner_fallback_api_key,
                fallback_model_name=pruner_fallback_model_name,
                reasoning_effort=pruner_reasoning_effort,
            )

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        use_llm_pruner: bool | None = None,
    ):
        # Pipeline completo:
        # 1. Stage 1: busca híbrida (Dense + BM25 + RRF) no pool de candidatos
        # 2. Stage 2a: Graph-Aware FK Boost (apenas tabelas + FKs)
        # 3. Stage 2b: LLM Pruner (padrão gpt-6-luna) com tracking de tokens
        # 4. Stage 3: Árvore de Steiner no grafo de FKs + agrupamento de regras
        pruner_ativo = self.use_llm_pruner if use_llm_pruner is None else use_llm_pruner
        eff_pool = max(
            top_k,
            self.fk_pool_k if self.use_fk_boost else 0,
            self.pruner_pool_k if pruner_ativo else 0,
        )

        retrieved_tables = self.rag._query(
            query,
            top_k=eff_pool,
            use_bm25=self.use_bm25,
            use_embedding=self.use_embedding,
        )
        raw_ids = list((retrieved_tables.get("ids") or [[]])[0])
        raw_docs = list((retrieved_tables.get("documents") or [[]])[0])
        raw_metas = list((retrieved_tables.get("metadatas") or [[]])[0])
        raw_dists = list((retrieved_tables.get("distances") or [[]])[0])

        tbl_entries: list[tuple[str, str, dict, float]] = []
        rule_entries: list[tuple[str, str, dict, float]] = []
        for idx_i, doc_id in enumerate(raw_ids):
            doc = raw_docs[idx_i] if idx_i < len(raw_docs) else ""
            meta = raw_metas[idx_i] if idx_i < len(raw_metas) else {}
            score = float(raw_dists[idx_i]) if idx_i < len(raw_dists) else 0.0
            if (
                doc_id in self.schema_graph.rule_schemas
                or doc_id.startswith("rule:")
                or meta.get("tipo") == "regra"
            ):
                rule_entries.append((doc_id, doc, meta, score))
            else:
                tbl_entries.append((doc_id, doc, meta, score))

        # Stage 2a: Graph-Aware FK Boost (antes do corte final e da Steiner Tree)
        if self.use_fk_boost and self._fk_booster is not None:
            fk_cut = (
                max(self.pruner_pool_k, top_k)
                if (pruner_ativo and self._pruner is not None)
                else top_k
            )
            tbl_entries = self._fk_booster.rerank(
                tbl_entries,
                top_k=fk_cut,
                num_ancoras=self.fk_num_ancoras,
                alpha=self.fk_alpha,
                w_pai=self.fk_w_pai,
                w_filho=self.fk_w_filho,
            )
        elif not pruner_ativo:
            tbl_entries = tbl_entries[:top_k]

        # Stage 2b: LLM Pruner (padrão gpt-6-luna)
        token_update = _empty_token_update()
        pruner_aplicado = False
        if pruner_ativo and self._pruner is not None:
            tbl_entries, rule_entries, token_update, pruner_aplicado = self._pruner.prune(
                query=query,
                tbl_entries=tbl_entries[: max(self.pruner_pool_k, top_k)],
                rule_entries=rule_entries,
                top_k_fallback=top_k,
            )

        initial_ids: list[str] = []
        docs_por_id: dict[str, str] = {}
        for node_id, doc, _, _ in tbl_entries + rule_entries:
            if node_id not in initial_ids:
                initial_ids.append(node_id)
            if doc or node_id not in docs_por_id:
                docs_por_id[node_id] = doc or (self.schema_graph.get_schema(node_id) or "")

        relations, missing_tables = self.schema_graph._get_relations(
            initial_ids,
            steiner_apenas_tabelas=self.steiner_apenas_tabelas,
            max_tabelas_por_regra=self.max_tabelas_por_regra,
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
        #    (quando o LLM Pruner rodou com sucesso, preservamos exatamente as regras que ele aprovou)
        nao_expandir_efetivo = self.nao_expandir_regras_tabelas or pruner_aplicado
        relacoes_set = set(relations)
        for rule_id in self.schema_graph.get_rules_for_tables(
            tabelas_ordenadas,
            nao_expandir_regras_tabelas=nao_expandir_efetivo,
        ):
            if self.incluir_doc_tabelas and rule_id.startswith(("tbl:", "col:")):
                continue
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

        # 2b. Documentação compacta inline (`tbl:<tabela>` e `col:<tabela>.<col>`)
        #     apenas para as tabelas finais selecionadas (descrição, enums e restrições de uso).
        if self.incluir_doc_tabelas and tabelas_ordenadas:
            tabelas_finais_set = set(tabelas_ordenadas)
            for tabela in tabelas_ordenadas:
                if tabela in docs_por_id:
                    docs_por_id[tabela] = enriquecer_ddl_com_dicionario(
                        self.schema_graph,
                        tabela,
                        docs_por_id[tabela],
                    )
            regras_ativas = [
                r_id
                for r_id in regras_ativas
                if not (
                    (r_id.startswith("tbl:") and r_id[4:].split("#")[0] in tabelas_finais_set)
                    or (r_id.startswith("col:") and r_id[4:].split(".")[0] in tabelas_finais_set)
                )
            ]

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

        # 4. Ordena ids e documents colocando a regra imediatamente antes da tabela a que se refere
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
        retrieved_tables["token_usage"] = token_update
        retrieved_tables["pruner_usado"] = pruner_aplicado
        return retrieved_tables, relations
    
if __name__ == '__main__':
    from .RAG_example import SCHEMA
    schema_graph_rag = SchemaGraphRAG(schema = SCHEMA)
    retrieved_tables, relations = schema_graph_rag.retrieve("What are the total sales for each customer?")
    print("Retrieved Tables:", retrieved_tables['ids'])
    print("Relations:", relations)