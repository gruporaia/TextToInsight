import hashlib
from .graph_logic import SchemaGraph
from .rag_logic import RAGRetriever
from pathlib import Path
import chromadb
#aqui vamos ter a integração da lógica do grafo com a lógica do RAG
#a ideia é que o RAG seja responsável por recuperar as informações relevantes para responder às perguntas, usando o grafo como guia para navegar pelas relações entre os dados

BASE_DIR = Path(__file__).resolve().parent

class SchemaGraphRAG:
    def __init__(self, schema: dict = None):
        #definição do grafo após o get do schema
        if schema:
            schema_string = schema.get("contexto_schema", "")
            schema_hash = hashlib.md5(schema_string.encode('utf-8')).hexdigest()
            collection_name = f"schema_{schema_hash}"
            chroma_client = chromadb.PersistentClient(path=str(BASE_DIR / "chroma_db"))
            self.schema_graph = SchemaGraph(schema = schema_string)
            self.rag = RAGRetriever(chroma_client = chroma_client, document_schema = schema, collection_name =collection_name)

    def retrieve(self, query: str, top_k: int = 5):
        #a query no no schema vai rolar aqui, essa função deve:
        # 1. acessar as tables e colunas relevantes
        # 2. usar as relações do grafo para navegar entre as tabelas e colunas
        # 3. recuperar os dados relevantes para responder à pergunta com as relações necessárias para ligar os dados

        #o importante aqui é encontrar o caminho mais curto que liga as tabelas retornadas pelo RAG com base no grafo
        #que foi produzido com o schema fornecido
        retrieved_tables = self.rag._query(query, top_k=top_k)
        relations, missing_tables = self.schema_graph._get_relations(retrieved_tables['ids'][0])
        if missing_tables:
            print(f"[SchemaGraphRAG] Warning: The following tables were retrieved by RAG but are missing in the graph: {missing_tables}")
        tabelas_relacionadas = set()
        for rel in relations:
            tabelas_relacionadas.add(rel[0])
            tabelas_relacionadas.add(rel[1])

        tabelas_faltando = tabelas_relacionadas - set(retrieved_tables['ids'][0])
        for t in tabelas_faltando:
            schema = self.schema_graph.table_schemas.get(t)
            if schema:
                retrieved_tables['ids'][0].append(t)
                retrieved_tables['documents'][0].append(schema)
        return retrieved_tables, relations
    
if __name__ == '__main__':
    from .RAG_example import SCHEMA
    schema_graph_rag = SchemaGraphRAG(schema = SCHEMA)
    retrieved_tables, relations = schema_graph_rag.retrieve("What are the total sales for each customer?")
    print("Retrieved Tables:", retrieved_tables['ids'])
    print("Relations:", relations)