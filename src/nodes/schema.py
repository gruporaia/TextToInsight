"""
Nó Schema (Esquema) do grafo de agentes Text-to-Insight.

O nó de schema é responsável por:
- Recuperar metadados e contexto do banco de dados
- Fornecer informações estruturais (tabelas, colunas, tipos)
- Enriquecer o estado com informações necessárias para a geração de código
"""

from ..state import EstadoTextToInsight


def nos_nodo_esquema(estado: EstadoTextToInsight) -> dict:
    """
    Nó Schema: Busca contexto e metadados do banco de dados.
    
    Simula uma consulta ao banco de dados para obter:
    - Estrutura das tabelas
    - Colunas e tipos de dados
    - Relacionamentos
    - Informações de índices
    
    Args:
        estado (EstadoTextToInsight): Estado atual do grafo.
    
    Returns:
        dict: Dicionário com atualizações do estado.
              - contexto_schema: String com metadados simulados
              - status: 'schema_obtido'
    """
    
    # Simular busca de schema do banco de dados
    print("[SCHEMA] Buscando contexto do banco de dados...")
    
    # Schema simulado (em produção, viria de um banco real)
    schema_simulado = """
    === SCHEMA DO BANCO DE DADOS ===
    
    Tabela: USUARIOS
    - id (INT, PRIMARY KEY)
    - nome (VARCHAR(255))
    - email (VARCHAR(255), UNIQUE)
    - data_criacao (TIMESTAMP)
    
    Tabela: PRODUTOS
    - id (INT, PRIMARY KEY)
    - nome (VARCHAR(255))
    - preco (DECIMAL(10, 2))
    - estoque (INT)
    
    Tabela: VENDAS
    - id (INT, PRIMARY KEY)
    - usuario_id (INT, FOREIGN KEY -> USUARIOS.id)
    - produto_id (INT, FOREIGN KEY -> PRODUTOS.id)
    - quantidade (INT)
    - data_venda (TIMESTAMP)
    """
    
    print("[SCHEMA] Contexto obtido com sucesso!")
    
    return {
        "contexto_schema": schema_simulado,
        "status": "schema_obtido",
    }
