import re
import networkx as nx
#toda a lógica do grafo deve ficar aqui (ligar tabelas e colunas por foreign keys)

class SchemaGraph:
#o objetivo aqui seria conectar as tabelas pelas FK, não pensei ainda exatamente como fazer, vou pensar
    def __init__(self, schema: str = None):
        self.graph = nx.DiGraph()
        if schema:
            self._add_schema(schema)

    def _add_schema(self, schema: str):
        regex_pattern = r"(Tabela: [\s\S]*?)(?=\nTabela: |$)"  
        table_chunks = [schema.strip() for schema in re.findall(regex_pattern, schema)] #table
        edges = []
        for table in table_chunks:
            name_match = re.search(r"Tabela: (\w+)", table)
            child_id = name_match.group(1) if name_match else f"table_{hash(table)}" #table name
            self.graph.add_node(child_id)
            if 'Foreign keys:' in table: #isso faz sentido? preciso revisitar pós stress test
                fk_matches = re.findall(r"->\s+(\w+)\.", table) #achar as tables pais
                for parent_id in fk_matches:
                    edges.append((child_id, parent_id))

        self.graph.add_edges_from(edges) #isso deve ser suficiente, só falta mexer na engine e depois integrar no grafo principal