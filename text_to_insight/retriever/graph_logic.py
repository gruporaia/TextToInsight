import re
import networkx as nx
from networkx.algorithms.approximation import steiner_tree
#toda a lógica do grafo deve ficar aqui (ligar tabelas e colunas por foreign keys)

from .RAG_example import SCHEMA


class SchemaGraph:
#o objetivo aqui seria conectar as tabelas pelas FK, não pensei ainda exatamente como fazer, vou pensar
    def __init__(
        self,
        schema: str = None,
        schema_info: list[dict] | dict = None,
        steiner_apenas_tabelas: bool = True,
    ):
        self.graph = nx.MultiGraph()
        self.table_schemas = {}
        self.rule_schemas = {}
        self.steiner_apenas_tabelas = steiner_apenas_tabelas
        if schema or schema_info:
            self._add_schema(schema=schema, schema_info=schema_info)

    def _add_schema(self, schema: str = None, schema_info: list[dict] | dict = None):
        if schema:
            regex_pattern = r"(Tabela: [\s\S]*?)(?=\nTabela: |$)"
            table_chunks = [t.strip() for t in re.findall(regex_pattern, schema)]

            for table in table_chunks:
                name_match = re.search(r"Tabela:\s+(\w+)", table)
                if not name_match:
                    continue

                tabela_nome = name_match.group(1)
                self.graph.add_node(tabela_nome, tipo="tabela")
                self.table_schemas[tabela_nome] = table

                if 'Foreign keys:' in table:
                    fk_matches = re.findall(r"-\s+(\w+)\s+->\s+(\w+)\.(\w+)", table)
                    for col_origem, parent_id, col_destino in fk_matches:
                        if parent_id not in self.graph:
                            self.graph.add_node(parent_id, tipo="tabela")
                        else:
                            self.graph.nodes[parent_id].setdefault("tipo", "tabela")

                        self.graph.add_edge(
                            tabela_nome, parent_id,
                            weight=1,
                            tipo="foreign_key",
                            tabela_origem=tabela_nome,
                            tabela_destino=parent_id,
                            child_col=col_origem,
                            parent_col=col_destino
                        )

                #Se não houver FK, a inferência de FKs virtuais (inferir_fks_virtuais) é que cria as arestas

        if schema_info:
            info_list = (
                [schema_info] if isinstance(schema_info, dict) else list(schema_info)
            )
            for item in info_list:
                if not isinstance(item, dict):
                    continue
                rule_id = item.get("id")
                if not rule_id:
                    continue
                rule_id = str(rule_id)
                titulo = str(item.get("titulo", "")).strip()
                descricao = str(item.get("descricao", "")).strip()
                tabelas_alvo = list(item.get("tabelas_alvo") or [])
                colunas_alvo = list(item.get("colunas_alvo") or [])

                self.graph.add_node(
                    rule_id,
                    tipo="regra",
                    titulo=titulo,
                    descricao=descricao,
                    tabelas_alvo=tabelas_alvo,
                    colunas_alvo=colunas_alvo,
                    origem_documento=str(item.get("origem_documento", "")),
                    precisa_llm=bool(item.get("precisa_llm", False)),
                )
                if titulo and descricao and not descricao.lower().startswith(titulo.lower()):
                    self.rule_schemas[rule_id] = f"{titulo}: {descricao}"
                else:
                    self.rule_schemas[rule_id] = descricao or titulo

                for tabela_alvo in tabelas_alvo:
                    if (
                        tabela_alvo in self.graph
                        and self.graph.nodes[tabela_alvo].get("tipo") == "tabela"
                    ):
                        self.graph.add_edge(
                            rule_id,
                            tabela_alvo,
                            weight=5,
                            tipo="aplica_se",
                            tabela_origem=rule_id,
                            tabela_destino=tabela_alvo,
                            child_col="aplica_se",
                            parent_col=tabela_alvo,
                        )

    def get_rules_for_tables(self, tables: list[str]) -> list[str]:
        """
        Expansão Tabela -> Regra: retorna os IDs de todas as regras associadas
        às tabelas informadas (via aresta 'aplica_se').
        """
        regras: list[str] = []
        vistas: set[str] = set()
        for tabela in tables:
            if tabela not in self.graph:
                continue
            for u, v, data in self.graph.edges(tabela, data=True):
                if data.get("tipo") == "aplica_se":
                    candidato = u if v == tabela else v
                    if (
                        candidato in self.graph
                        and self.graph.nodes[candidato].get("tipo") == "regra"
                        and candidato not in vistas
                    ):
                        vistas.add(candidato)
                        regras.append(candidato)
        return regras

    def get_schema(self, table_name: str) -> str | None:
        """
        Retorna o schema DDL da tabela (ou texto da regra) por nome/id, com fallback case-insensitive.
        """
        if not table_name:
            return None
        if table_name in self.table_schemas:
            return self.table_schemas[table_name]
        if table_name in self.rule_schemas:
            return self.rule_schemas[table_name]
        table_lower = table_name.lower()
        for name, schema_text in self.table_schemas.items():
            if name.lower() == table_lower:
                return schema_text
        for name, rule_text in self.rule_schemas.items():
            if name.lower() == table_lower:
                return rule_text
        return None

    def _get_relations(self, nodes: list, steiner_apenas_tabelas: bool | None = None):
        """
        Recebe uma lista de nós (tabelas e/ou regras recuperadas pelo RAG).
        1. As regras retornadas referenciam suas respectivas tabelas (aresta "aplica_se",
           no sentido regra -> tabela).
        2. A partir das tabelas (recuperadas diretamente ou ativadas por regras),
           executa a Árvore de Steiner:
           - Se `steiner_apenas_tabelas=True` (padrão): percorre APENAS nós do tipo "tabela"
             e arestas "foreign_key" (apenas tabela -> tabela).
           - Se `steiner_apenas_tabelas=False`: permite expansão da Steiner Tree tanto por
             tabela -> tabela quanto por tabela -> regra -> tabela.
        """
        apenas_tabelas = (
            self.steiner_apenas_tabelas
            if steiner_apenas_tabelas is None
            else steiner_apenas_tabelas
        )
        relations = set()
        valid_nodes = [n for n in nodes if n in self.graph]
        missing_nodes = [n for n in nodes if n not in self.graph]

        # 1. Expansão unidirecional inicial: Regra -> Tabela (via aresta "aplica_se")
        target_tables = []
        for node in valid_nodes:
            node_tipo = self.graph.nodes[node].get("tipo", "tabela")
            if node_tipo == "tabela":
                if node not in target_tables:
                    target_tables.append(node)
            elif node_tipo == "regra":
                for u, v, data in self.graph.edges(node, data=True):
                    if data.get("tipo") == "aplica_se":
                        tabela_alvo = v if u == node else u
                        if (
                            tabela_alvo in self.graph
                            and self.graph.nodes[tabela_alvo].get("tipo") == "tabela"
                        ):
                            relations.add(
                                (
                                    node,
                                    tabela_alvo,
                                    data.get("child_col", "aplica_se"),
                                    data.get("parent_col", tabela_alvo),
                                )
                            )
                            if tabela_alvo not in target_tables:
                                target_tables.append(tabela_alvo)

        if len(target_tables) < 2:
            return list(relations), list(missing_nodes)

        try:
            if apenas_tabelas:
                # Subgrafo estritamente de tabelas e Foreign Keys (impede passar por regras)
                grafo_busca = nx.subgraph_view(
                    self.graph,
                    filter_node=lambda n: self.graph.nodes[n].get("tipo") == "tabela",
                    filter_edge=lambda u, v, k: (
                        self.graph.edges[u, v, k].get("tipo") == "foreign_key"
                        and self.graph.nodes[u].get("tipo") == "tabela"
                        and self.graph.nodes[v].get("tipo") == "tabela"
                    ),
                )
            else:
                # Permite expansão tanto por tabela -> tabela quanto por tabela -> regra -> tabela
                grafo_busca = self.graph

            # 1. Encontra todos os subgrafos (ilhas ou componentes conectados)
            componentes = list(nx.connected_components(grafo_busca))

            for comp in componentes:
                # 2. Verifica quais tabelas alvo estão DENTRO desta ilha específica
                tabelas_no_comp = [t for t in target_tables if t in comp]

                # 3. Se houver 2 ou mais tabelas na mesma ilha, encontra o caminho via Steiner Tree
                if len(tabelas_no_comp) >= 2:
                    subgrafo = grafo_busca.subgraph(comp)

                    # 4. A Árvore de Steiner encontra o menor caminho conectando as tabelas alvo
                    path = steiner_tree(subgrafo, tabelas_no_comp)
                    edges = list(path.edges(data=True))

                    # 5. Salva todas as relações encontradas nesse caminho
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

        return list(relations), list(missing_nodes)

if __name__ == '__main__':
    graph_class = SchemaGraph(schema = SCHEMA.get("contexto_schema", ""))
    print(list(graph_class.graph.nodes))
    print(list(graph_class.graph.edges))
    print(graph_class._get_relations(['pizza_names', 'pizza_runners']))