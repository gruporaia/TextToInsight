"""
Nós do grafo de agentes Text-to-Insight.

Este módulo contém todas as funções que representam os nós do grafo,
cada uma responsável por uma etapa específica do pipeline.

Nós disponíveis:
    - planner: Orquestra a estratégia de execução.
    - schema: Busca contexto e metadados do banco de dados.
    - code_agent: Gera código Python baseado no plano.
    - sandbox: Executa o código de forma segura.
    - critic: Avalia a saída e fornece feedback.
"""

from .planner import nos_nodo_planejador
from .schema import nos_nodo_esquema
from .code_agent import nos_nodo_agente_codigo
from .sandbox import nos_nodo_sandbox
from .critic import nos_nodo_critico

__all__ = [
    "nos_nodo_planejador",
    "nos_nodo_esquema",
    "nos_nodo_agente_codigo",
    "nos_nodo_sandbox",
    "nos_nodo_critico",
]
