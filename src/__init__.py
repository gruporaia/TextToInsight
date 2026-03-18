"""
Núcleo do projeto Text-to-Insight.

Submódulos:
    - state: Definição do estado compartilhado do grafo.
    - nodes: Nós individuais do grafo (planner, schema, code_agent, sandbox, critic).
    - routers: Funções de roteamento condicional entre nós.
    - graph: Grafo compilado pronto para execução.
"""

from .state import EstadoTextToInsight

__all__ = ["EstadoTextToInsight"]
