import re
import networkx as nx
from networkx.algorithms.approximation import steiner_tree
#toda a lógica do grafo deve ficar aqui (ligar tabelas e colunas por foreign keys)

from .RAG_example import SCHEMA


class SchemaGraph:
#o objetivo aqui seria conectar as tabelas pelas FK, não pensei ainda exatamente como fazer, vou pensar
    def __init__(self, schema: str = None):
        self.graph = nx.Graph()
        self.table_schemas = {}
        if schema:
            self._add_schema(schema)

    def _add_schema(self, schema: str):
        regex_pattern = r"(Tabela: [\s\S]*?)(?=\nTabela: |$)"  
        table_chunks = [t.strip() for t in re.findall(regex_pattern, schema)]
        
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
                    self.graph.add_edge(
                        tabela_nome, parent_id, 
                        weight=1,
                        tabela_origem=tabela_nome, 
                        tabela_destino=parent_id,
                        child_col=col_origem, 
                        parent_col=col_destino
                    )
            
            #Se não houver FK, roda SchemaCrawler ou qualquer outra técnica (Isso aqui é discutível, provavelmente deveriamso passar o SC para um nó antes do RAG)

    def _get_relations(self, tables: list):
        """
        Recebe uma lista de tabelas (que foram recuperadas pelo RAG) e tenta encontrar
        o caminho mais curto de ligações (Foreign Keys / JOINs) entre elas, usando
        a teoria de grafos (Árvore de Steiner).
        
        Isso é vital porque o RAG só devolve "quais tabelas", mas o LLM precisa saber 
        "como essas tabelas se conectam" para escrever a query SQL corretamente.
        """
        relations = set()
        valid_tables = [t for t in tables if t in self.graph]
        missing_tables = [t for t in tables if t not in self.graph]

        if len(valid_tables) < 2:
            return list(relations), list(missing_tables)
        
        try:
            # 1. Encontra todos os subgrafos (ilhas ou componentes conectados)
            # O grafo principal pode ter várias "ilhas" de tabelas que não se conversam
            componentes = list(nx.connected_components(self.graph))
            
            for comp in componentes:
                # 2. Verifica quais tabelas que o RAG recuperou estão DENTRO desta ilha específica
                tabelas_no_comp = [t for t in valid_tables if t in comp]
                
                # 3. Se houver 2 ou mais tabelas recuperadas na mesma ilha,
                # precisamos encontrar as chaves (FKs) que ligam elas.
                if len(tabelas_no_comp) >= 2:
                    subgrafo = self.graph.subgraph(comp)
                    
                    # 4. A Árvore de Steiner encontra o menor caminho conectando 
                    # as tabelas alvo, inclusive passando por tabelas intermediárias se necessário.
                    path = steiner_tree(subgrafo, tabelas_no_comp)
                    edges = list(path.edges(data=True))
                    
                    # 5. Salva todas as relações (JOINs/FKs) encontradas nesse caminho
                    for u, v, data in edges:
                        origem = data.get('tabela_origem')
                        destino = data.get('tabela_destino')
                        child_col = data.get('child_col')
                        parent_col = data.get('parent_col')
                        
                        if origem and destino: 
                            relations.add((origem, destino, child_col, parent_col))
                            
            if not relations:
                print("[GRAPH STEINER] As tabelas solicitadas não possuem caminhos (FK) entre si.")

        except nx.NetworkXException as e:
            print(f"[GRAPH ERROR] Não foi possível encontrar um caminho entre todas as tabelas: {e}")

        return list(relations), list(missing_tables)

if __name__ == '__main__':
    graph_class = SchemaGraph(schema = SCHEMA.get("contexto_schema", ""))
    print(list(graph_class.graph.nodes))
    print(list(graph_class.graph.edges))
    print(graph_class._get_relations(['pizza_names', 'pizza_runners']))