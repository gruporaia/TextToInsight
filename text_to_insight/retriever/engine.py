from .graph_logic import SchemaGraph
from .rag_logic import RAGRetriever
from pathlib import Path
import chromadb
from .RAG_example import SCHEMA
#aqui vamos ter a integração da lógica do grafo com a lógica do RAG
#a ideia é que o RAG seja responsável por recuperar as informações relevantes para responder às perguntas, usando o grafo como guia para navegar pelas relações entre os dados

BASE_DIR = Path(__file__).resolve().parent

class SchemaGraphRAG:
    def __init__(self, schema: dict = None):
        #definição do grafo após o get do schema
        if schema:
            chroma_client = chromadb.PersistentClient(path=str(BASE_DIR / "chroma_db"))
            self.schema_graph = SchemaGraph(schema = SCHEMA.get("contexto_schema", ""))
            self.rag = RAGRetriever(chroma_client = chroma_client, document_schema = schema)

    def retrieve(self, query: str):
        #a query no no schema vai rolar aqui, essa função deve:
        # 1. acessar as tables e colunas relevantes
        # 2. usar as relações do grafo para navegar entre as tabelas e colunas
        # 3. recuperar os dados relevantes para responder à pergunta com as relações necessárias para ligar os dados

        #o importante aqui é encontrar o caminho mais curto que liga as tabelas retornadas pelo RAG com base no grafo
        #que foi produzido com o schema fornecido
        retrieved_tables = self.rag._query(query)
        relations = self.schema_graph._get_relations(retrieved_tables['ids'][0])
        return retrieved_tables, relations
    
if __name__ == '__main__':
    schema_graph_rag = SchemaGraphRAG(schema = SCHEMA)
    retrieved_tables, relations = schema_graph_rag.retrieve("What are the total sales for each customer?")
    print("Retrieved Tables:", retrieved_tables['ids'])
    print("Relations:", relations)