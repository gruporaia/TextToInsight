"""
Nó Retriever (GraphRAG) do grafo de agentes Text-to-Insight.

Lê o `contexto_schema` produzido pelo nó de schema, recupera o subconjunto de
tabelas relevantes para a pergunta via SchemaGraphRAG (vetor + grafo de FKs),
formata o resultado como texto e sobrescreve `contexto_schema` no estado.

Política: sobrescreve `contexto_schema` para manter o CodeAgent e o template
de prompt inalterados (decisão registrada no plano).
"""

from ..state import EstadoTextToInsight
from ..retriever.engine import SchemaGraphRAG


def _formatar_contexto_rag(retrieved, relations) -> str:
    tabelas_txt = "\n\n".join(retrieved["documents"][0])
    rels_txt = "\n".join(" -> ".join(p) for p in relations) or "(sem relações)"
    return (
        "=== SCHEMA RELEVANTE (via RAG) ===\n\n"
        f"{tabelas_txt}\n\n"
        "=== RELAÇÕES (caminhos no grafo) ===\n"
        f"{rels_txt}\n"
    )


def nos_nodo_retriever(estado: EstadoTextToInsight) -> dict:
    pergunta = estado.get("pergunta_usuario", "")
    schema_full = estado.get("contexto_schema", "")
    if not schema_full or not pergunta:
        return {}

    print(f"[RETRIEVER] schema completo: {len(schema_full)} chars (~{len(schema_full)//4} tokens)")
    rag = SchemaGraphRAG(schema={"contexto_schema": schema_full})
    retrieved, relations = rag.retrieve(pergunta)
    reduzido = _formatar_contexto_rag(retrieved, relations)
    print(
        f"[RETRIEVER] schema reduzido: {len(reduzido)} chars (~{len(reduzido)//4} tokens) "
        f"| tabelas: {retrieved['ids'][0]}"
    )
    return {"contexto_schema": reduzido}
