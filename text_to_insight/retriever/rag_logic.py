import re
import hashlib
import chromadb
from pathlib import Path
from .RAG_example import SCHEMA
#o RAG em si vai ser configurado aqui

BASE_DIR = Path(__file__).resolve().parent

class RAGRetriever:
    #Identifiquei um problema, a semântica vai ser bem fraca entre queries e o nome das tables/colunas, o que dificulta a recuperação, uma solução seria: 
    #usar a descrição das tables/colunas para enriquecer o retrieve (isso pode ser custoso)
    #indexar o conteúdo das tabelas (pode ser bem custoso)
    #usar um modelo ridiculamente leve pra fazer descrições semânticas das tables (+custo dnv)

    #proponho avaliar da forma como esta, se estiver muito ruim pensamos em pivotar
    def __init__(self, chroma_client: chromadb.Client = None, document_schema: dict = None, collection_name: str = "schema_collection"):
        #o chroma client deve ser resolvido no engine, criando uma instancia unica e compartilhada, para evitar overhead de conexões
        self.chroma_client = chroma_client
        #a collection name agora é meio inútil, mas é bom manter a flexibilidade de ter mais de uma collection, caso seja necessário indexar outros tipos de documentos no futuro
        self.collection_name = collection_name
        self.collection = self.chroma_client.get_or_create_collection(name=self.collection_name)
        
        self._add_documents(document_schema)

    def _add_documents(self, document_schema: dict):      
        schema_string = document_schema.get("contexto_schema", "")
        print("[RAG] collection size:", self.collection.count())
        if not schema_string:
            print("[RAG] no schema provided")
            return
        #aqui eu optei por verificar com hash se o SCHEMA ja existe, assim a gente pode atualizar sempre que mudar a database
        try:
            with open(BASE_DIR / ".schema_hash") as f:
                old_hash = f.read().strip()
        except FileNotFoundError:
            old_hash = None
        new_schema = hashlib.md5(schema_string.encode()).hexdigest()
        # Se a collection está vazia, o hash cacheado não reflete o estado real do índice
        # (ex.: chroma_db apagado, ou collection nova em diretório isolado). Reindexar.
        collection_vazia = self.collection.count() == 0
        if new_schema != old_hash or collection_vazia:
            print(f"[RAG] new schema detected, indexing...")
            with open(BASE_DIR / ".schema_hash", "w") as f:
                f.write(new_schema)
            #o regex esta baseado na nossa lógica do nó de schema
            regex_pattern = r"(Tabela: [\s\S]*?)(?=\nTabela: |$)"  
            table_chunks = [block.strip() for block in re.findall(regex_pattern, schema_string)]
            ids = []
            #cada chunk é uma table, desse jeito a gente tem uma granularidade melhor pra recuperar as informações, e o nome da tabela ja ajuda um pouco na semântica
            for block in table_chunks:
                name_match = re.search(r"Tabela: (\w+)", block)
                table_id = name_match.group(1) if name_match else f"table_{hash(block)}"
                ids.append(table_id)
            if ids:
                self.collection.upsert(ids=ids, documents=table_chunks)
            print(f"[RAG] indexing completed with {len(ids)} tables")
            return
        print(f"[RAG] skipping indexing (already exists)")
        return

    def _retrieve(self, query: str, top_k: int = 5):
        #aqui não coloquei modelo de embbeding específico, então isso pode estar afetando um teco o retrieve, ver depois
        results = self.collection.query(
            query_texts=[query],
            n_results=top_k
        )
        return results
    
    def _query(self, query: str, top_k: int = 5):
        return self._retrieve(query=query, top_k=top_k)
    
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