import re
import chromadb
#o RAG em si vai ser configurado aqui

class RAGRetriever:
    def __init__(self, chroma_client: chromadb.Client = None, document_schema: dict = None, collection_name: str = "schema_collection"):
        self.chroma_client = chroma_client
        self.collection_name = collection_name
        self.collection = self.chroma_client.get_or_create_collection(name=self.collection_name)
        
        if not document_schema or self.collection.count() > 0:
            status = "skipping indexing (already exists)" if self.collection.count() > 0 else "no schema provided"
            print(f"[RAG] {status}")
            return 

        print(f"[RAG] Indexing new schema into {self.collection_name}...")
        self._add_documents(document_schema)

    def _add_documents(self, document_schema: dict):
        regex_pattern = r"(Tabela: [\s\S]*?)(?=\nTabela: |$)"
        schema_string = document_schema.get("contexto_schema", "")
        if not schema_string:
            return
        table_chunks = [block.strip() for block in re.findall(regex_pattern, schema_string)]
        ids = []
        for block in table_chunks:
            name_match = re.search(r"Tabela: (\w+)", block)
            table_id = name_match.group(1) if name_match else f"table_{hash(block)}"
            ids.append(table_id)
        if ids:
            self.collection.upsert(ids=ids, documents=table_chunks)

    def _retrieve(self, query: str, top_k: int = 5):
        results = self.collection.query(
            query_texts=[query],
            n_results=top_k
        )
        return results['documents'][0] if results['documents'] else []
    
    def query(self, query: str, top_k: int = 5):
        return self._retrieve(query=query, top_k=top_k)