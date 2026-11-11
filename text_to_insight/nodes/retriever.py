"""
Nó Retriever (GraphRAG) do grafo de agentes Text-to-Insight.

Lê o `contexto_schema` produzido pelo nó de schema, recupera o subconjunto de
tabelas relevantes para a pergunta via SchemaGraphRAG (vetor + grafo de FKs),
formata o resultado como texto e adiciona ao campo contexto_rag_schema.
"""

from pathlib import Path

from ..state import EstadoTextToInsight
from ..retriever.engine import SchemaGraphRAG


def _formatar_contexto_rag(retrieved, relations=None) -> str:
    tabelas_ordenadas = retrieved.get("tabelas_ordenadas")
    tabelas_documentos = retrieved.get("tabelas_documentos") or {}
    regras_por_tabela = retrieved.get("regras_por_tabela") or {}
    regras_sem_tabela = retrieved.get("regras_sem_tabela") or []

    # Caso `retrieved` venha de um mock simples sem `tabelas_ordenadas`, reconstrói via `relations` (se houver)
    if tabelas_ordenadas is None:
        ids = (retrieved.get("ids") or [[]])[0]
        docs = (retrieved.get("documents") or [[]])[0]
        docs_por_id = dict(zip(ids, docs))

        aplica_se_map: dict[str, list[dict]] = {}
        regras_ids = set()
        if relations:
            for rel in relations:
                if len(rel) >= 3 and rel[2] == "aplica_se":
                    r_id, t_id = rel[0], rel[1]
                    regras_ids.add(r_id)
                    aplica_se_map.setdefault(t_id, []).append(
                        {"id": r_id, "documento": docs_por_id.get(r_id, "")}
                    )

        if aplica_se_map:
            tabelas_ordenadas = [i for i in ids if i not in regras_ids]
            tabelas_documentos = {t: docs_por_id.get(t, "") for t in tabelas_ordenadas}
            regras_por_tabela = aplica_se_map
        else:
            tabelas_txt = "\n\n".join(docs)
            return (
                "=== SCHEMA RELEVANTE (via RAG) ===\n\n"
                f"{tabelas_txt}\n"
            )

    blocos: list[str] = []

    for r_info in regras_sem_tabela:
        r_doc = (r_info.get("documento") or "").strip()
        r_id = r_info.get("id", "regra")
        if r_doc:
            blocos.append(f"Regra ({r_id}): {r_doc}")

    for tabela in tabelas_ordenadas:
        doc_tabela = (tabelas_documentos.get(tabela) or "").strip()
        if not doc_tabela:
            continue
        regras_da_tabela = regras_por_tabela.get(tabela) or []
        if regras_da_tabela:
            linhas_regras = [
                f"Regra ({r['id']} -> {tabela}): {r['documento'].strip()}"
                for r in regras_da_tabela
                if r.get("documento")
            ]
            if linhas_regras:
                blocos.append("\n".join(linhas_regras) + "\n" + doc_tabela)
            else:
                blocos.append(doc_tabela)
        else:
            blocos.append(doc_tabela)

    tabelas_txt = "\n\n".join(blocos)
    return (
        "=== SCHEMA RELEVANTE (via RAG) ===\n\n"
        f"{tabelas_txt}\n"
    )


def nos_nodo_retriever(
    estado: EstadoTextToInsight,
    use_rag: bool = True,
    use_bm25: bool = True,
    use_embedding: bool = True,
    steiner_apenas_tabelas: bool = True,
    docs_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
) -> dict:
    pergunta = (
        estado.get("pergunta_atual", "")
        or estado.get("pergunta_original", "")
        or estado.get("pergunta_usuario", "") # campo antigo de pergunta mas manter para compatibilidade com testes antigos
    ) # usa pergunta canônica do estado
    schema_full = estado.get("contexto_schema", "")
    schema_size = len(schema_full)
    tentativas_revisao = estado.get("tentativas_revisao_retriever", 0)

    if not schema_full:
        print("[RETRIEVER] contexto_schema vazio, não há nada a recuperar")
        return {
            "contexto_rag_schema": "",
            "status": "schema_vazio",
            "tentativas_revisao_retriever": tentativas_revisao + 1,
        }
    regras_estado = estado.get("regras") or estado.get("contexto_regras")
    if isinstance(regras_estado, str):
        regras_estado = [{"contexto_regras": regras_estado}]
    docs_dir_efetivo = docs_dir or estado.get("docs_dir")
    tem_regras = bool(regras_estado or docs_dir_efetivo)

    # if use_rag is False, the RAG is disabled so we want pass the full schema
    # but if the schema is not so big (less than 5000 chars) and there are no external rules, we can also pass the full schema to avoid RAG overhead
    if (not use_rag) or (schema_size < 5000 and not tem_regras):
        print("[RETRIEVER] RAG desativado. Passando o schema completo.")
        return {
            # contexto_rag_schema empty because we are not using RAG, the string must be empty because the code_agent will use this variable to check if RAG is used or not 
            "contexto_rag_schema": "", # (empty string returns False)
            "status": "schema_obtido",
            "tentativas_revisao_retriever": tentativas_revisao + 1,
        }

    print(f"[RETRIEVER] schema completo: {len(schema_full)} chars (~{len(schema_full)//4} tokens)")
    
    tentativas = estado.get("tentativas_loop", 0)
    top_k_dinamico = 8  + (tentativas * 4) # aumenta o top_k a cada tentativa de loop, para tentar recuperar mais tabelas se necessário

    rag = SchemaGraphRAG(
        schema={"contexto_schema": schema_full},
        rules=regras_estado,
        docs_dir=docs_dir_efetivo,
        cache_dir=cache_dir,
        use_bm25=use_bm25,
        use_embedding=use_embedding,
        steiner_apenas_tabelas=steiner_apenas_tabelas,
    )
    print(f"[RETRIEVER] Recuperando top_k={top_k_dinamico} tabelas (loop atual: {tentativas})...")
    retrieved, relations = rag.retrieve(pergunta, top_k=top_k_dinamico)
    
    print(f"[RETRIEVER] Tabelas recuperadas: {retrieved['ids'][0]}")
    print(f"[RETRIEVER] Relations: {relations}")
    reduzido = _formatar_contexto_rag(retrieved, relations)
    print(
        f"[RETRIEVER] schema reduzido: {len(reduzido)} chars (~{len(reduzido)//4} tokens) "
        f"| tabelas: {retrieved['ids'][0]}"
    )
    # Retorna o contexto novo e reseta o status
    return {
        "contexto_rag_schema": reduzido,
        "status": "schema_obtido",
        "tentativas_revisao_retriever": tentativas_revisao + 1,
    }
