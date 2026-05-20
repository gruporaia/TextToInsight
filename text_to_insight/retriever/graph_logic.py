import re
import networkx as nx
#toda a lógica do grafo deve ficar aqui (ligar tabelas e colunas por foreign keys)

from .RAG_example import SCHEMA

class SchemaGraph:
#o objetivo aqui seria conectar as tabelas pelas FK, não pensei ainda exatamente como fazer, vou pensar
    def __init__(self, schema: str = None):
        self.graph = nx.DiGraph()
        self.table_schemas = {}
        if schema:
            self._add_schema(schema)

    def _add_schema(self, schema: str):
        regex_pattern = r"(Tabela: [\s\S]*?)(?=\nTabela: |$)"  
        table_chunks = [t.strip() for t in re.findall(regex_pattern, schema)]
        
        possiveis_chaves = {}

        for table in table_chunks:
            name_match = re.search(r"Tabela:\s+(\w+)", table)
            if not name_match: 
                continue
                
            tabela_nome = name_match.group(1)
            self.graph.add_node(tabela_nome)
            self.table_schemas[tabela_nome] = table 
            
            if 'Foreign keys:' in table:
                fk_matches = re.findall(r"-\s+(\w+)\s+->\s+(\w+)\.(\w+)", table)
                for col_origem, parent_id, col_destino in fk_matches:
                    self.graph.add_edge(tabela_nome, parent_id, child_col=col_origem, parent_col=col_destino)
                    if parent_id not in self.graph:
                        self.graph.add_node(parent_id)
            
            colunas_match = re.findall(r"-\s+(\w+):", table)
            for col in colunas_match:
                if col.endswith("_id") or col.endswith("_code") or col == "zipcode":
                    if col not in possiveis_chaves:
                        possiveis_chaves[col] = []
                    possiveis_chaves[col].append(tabela_nome)

        for coluna, tabelas in possiveis_chaves.items():
            if len(tabelas) > 1:
                for i in range(len(tabelas)):
                    for j in range(i + 1, len(tabelas)):
                        t1, t2 = tabelas[i], tabelas[j]
                        if not self.graph.has_edge(t1, t2) and not self.graph.has_edge(t2, t1):
                            self.graph.add_edge(t1, t2, child_col=coluna, parent_col=coluna)

    def _get_relations(self, tables: list):
        relations = set()
        missing_tables = set()
        
        if len(tables) < 2:
            return list(relations), list(missing_tables)
            
        grafo_undirected = self.graph.to_undirected()
        
        for i in range(len(tables)):
            for j in range(i + 1, len(tables)):
                orig, dest = tables[i], tables[j]
                if orig not in grafo_undirected or dest not in grafo_undirected:
                    continue
                    
                try:
                    path = nx.shortest_path(grafo_undirected, source=orig, target=dest)
                    
                    for k in range(len(path) - 1):
                        t1, t2 = path[k], path[k+1]
                        
                        if self.graph.has_edge(t1, t2):
                            c1 = self.graph[t1][t2]['child_col']
                            c2 = self.graph[t1][t2]['parent_col']
                            relations.add(f"{t1}.{c1} = {t2}.{c2}")
                        elif self.graph.has_edge(t2, t1):
                            c1 = self.graph[t2][t1]['parent_col']
                            c2 = self.graph[t2][t1]['child_col']
                            relations.add(f"{t1}.{c1} = {t2}.{c2}")
                            
                        if t1 not in tables:
                            missing_tables.add(t1)
                        if t2 not in tables:
                            missing_tables.add(t2)
                            
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    continue
                    
        return list(relations), list(missing_tables)

if __name__ == '__main__':
    graph_class = SchemaGraph(schema = SCHEMA.get("contexto_schema", ""))
    print(list(graph_class.graph.nodes))
    print(list(graph_class.graph.edges))
    print(graph_class._get_relations(['pizza_names', 'pizza_runners']))