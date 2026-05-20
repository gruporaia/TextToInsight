import re
import networkx as nx
#toda a lógica do grafo deve ficar aqui (ligar tabelas e colunas por foreign keys)

from .RAG_example import SCHEMA

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
                fk_matches = re.findall(r"->\s+(\w+)\.", table) #acha as tables pais
                for parent_id in fk_matches:
                    edges.append((child_id, parent_id))

        self.graph.add_edges_from(edges) #isso deve ser suficiente, só falta mexer na engine e depois integrar no grafo principal

    def _get_relations(self, tables: list):
        #aqui a ideia é pegar as tabelas retornadas pelo RAG e achar o caminho mais curto entre elas usando o grafo
        #isso deve retornar as relações necessárias para ligar os dados
        relations = []
        if len(tables) < 2:
            return relations
        # Joins são semanticamente bidirecionais: A pode unir-se a B independentemente da direção da FK.
        # Por isso usamos a versão não-direcionada do grafo apenas para path-finding.
        grafo_undirected = self.graph.to_undirected()
        for i in range(len(tables)):
            for j in range(i + 1, len(tables)):
                try:
                    path = nx.shortest_path(grafo_undirected, source=tables[i], target=tables[j])
                    relations.append(path)
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    continue
        return relations

if __name__ == '__main__':
    graph_class = SchemaGraph(schema = SCHEMA.get("contexto_schema", ""))
    print(list(graph_class.graph.nodes))
    print(list(graph_class.graph.edges))
    print(graph_class._get_relations(['orders', 'customers']))