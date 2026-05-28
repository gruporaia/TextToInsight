"""
Roteadores de borda condicional do grafo Text-to-Insight.

Este módulo contém as funções que decidem para qual nó o grafo deve prosseguir
baseado no estado atual. Muito útil para lógica condicional complexa.

Roteadores disponíveis:
    - roteador_sandbox: Define o fluxo após execução do código
    - roteador_planejador: Define o fluxo após planejamento
    - roteador_grafico: Define se gera gráfico ou vai direto para resposta
    - roteador_fan_out: ReFoRCE Map-out - cria 5 Send para parallelização
    - roteador_votacao: ReFoRCE Reduce - consensus voting após Fan-in
"""

from .edges import (
    roteador_sandbox,
    roteador_planejador,
    roteador_grafico,
    roteador_fan_out,
    roteador_votacao,
)

__all__ = [
    "roteador_sandbox",
    "roteador_planejador",
    "roteador_grafico",
    "roteador_fan_out",
    "roteador_votacao",
]

