"""
Nó Retriever (GraphRAG) do grafo de agentes Text-to-Insight.

Lê o `contexto_schema` produzido pelo nó de schema, recupera o subconjunto de
tabelas relevantes para a pergunta via SchemaGraphRAG (vetor + grafo de FKs),
formata o resultado como texto e adiciona ao campo contexto_rag_schema.
"""

from ..state import EstadoTextToInsight
from ..retriever.engine import SchemaGraphRAG


def _formatar_contexto_rag(retrieved, relations) -> str:
    tabelas_txt = "\n\n".join(retrieved["documents"][0])
    rels_formatadas = []
    for origem, destino, col_origem, col_destino in relations:
        join_str = f"{origem} JOIN {destino} ON {origem}.{col_origem} = {destino}.{col_destino}"
        rels_formatadas.append(join_str)
        
    rels_txt = "\n".join(rels_formatadas) if rels_formatadas else "(sem relações - tabelas isoladas)"
    
    return (
        "=== SCHEMA RELEVANTE (via RAG) ===\n\n"
        f"{tabelas_txt}\n\n"
        "=== RELAÇÕES NECESSÁRIAS (caminhos de JOIN) ===\n"
        f"{rels_txt}\n"
    )


def nos_nodo_retriever(estado: EstadoTextToInsight) -> dict:
    pergunta = estado.get("pergunta_atual", "")
    schema_full = estado.get("contexto_schema", "")
    schema_size = len(schema_full)
    tentativas_revisao = estado.get("tentativas_revisao_retriever", 0)

    if not schema_full or not pergunta or schema_size < 1500: 
        print(f"[RETRIEVER] contexto_schema contém {schema_size} chars, mantendo original")
        return {
            "status": "schema_obtido",
            "tentativas_revisao_retriever": tentativas_revisao + 1,
        }

    print(f"[RETRIEVER] schema completo: {len(schema_full)} chars (~{len(schema_full)//4} tokens)")
    
    tentativas = estado.get("tentativas_loop", 0)
    top_k_dinamico = 5 + (tentativas * 4)
    
    rag = SchemaGraphRAG(schema={"contexto_schema": schema_full})
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
