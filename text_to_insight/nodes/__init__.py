"""
Nós do grafo de agentes Text-to-Insight.

Este módulo contém todas as funções que representam os nós do grafo,
cada uma responsável por uma etapa específica do pipeline.

Nós disponíveis:
    - planner: Orquestra a estratégia de execução.
    - schema: Busca contexto e metadados do banco de dados.
    - code_agent: Gera código Python baseado no plano.
    - sandbox: Executa o código de forma segura.
    - critic: Avalia a saída e fornece feedback (DEPRECATED → voting_node).
    - voting_node: Votação por maioria (ReFoRCE).
    - exploration: Exploração de divergências (ReFoRCE).
    - csv_saver: Salva resultado da query em CSV.
    - graph_generator: Gera gráfico matplotlib a partir do CSV.
"""

from .planner import nos_nodo_planejador
from .schema import nos_nodo_esquema
from .retriever import nos_nodo_retriever
from .code_agent.code_agent import nos_nodo_agente_codigo
from .sandbox import nos_nodo_sandbox
from .critic import nos_nodo_critico
from .voting_node import nos_nodo_votacao
from .exploration import nos_nodo_explorador
from .response import nos_nodo_resposta
from .csv_saver import nos_nodo_salvar_csv
from .graph_generator import nos_nodo_gerador_grafico

__all__ = [
    "nos_nodo_planejador",
    "nos_nodo_esquema",
    "nos_nodo_retriever",
    "nos_nodo_agente_codigo",
    "nos_nodo_sandbox",
    "nos_nodo_critico",
    "nos_nodo_votacao",
    "nos_nodo_explorador",
    "nos_nodo_resposta",
    "nos_nodo_salvar_csv",
    "nos_nodo_gerador_grafico",
]

