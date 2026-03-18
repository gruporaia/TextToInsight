"""
Definição do estado compartilhado do grafo de agentes Text-to-Insight.

Este módulo define a estrutura de dados que será passada entre os nós do grafo,
contendo todas as informações necessárias para o fluxo de execução.
"""

from typing import TypedDict


class EstadoTextToInsight(TypedDict):
    """
    Estado compartilhado do grafo de agentes Supervisor/Hierarchical.
    
    Atributos:
        pergunta_usuario (str): A pergunta ou solicitação inicial do usuário.
        contexto_schema (str): Contexto ou schema do banco de dados recuperado.
        codigo_gerado (str): Código gerado pela agente de código.
        saida_terminal (str): Resultado da execução do código no sandbox.
        feedback_critico (str): Feedback de crítica sobre a qualidade do código.
        status (str): Status atual da execução (ex: 'iniciado', 'codigo_ok', 'erro_codigo', 'concluido').
        tentativas_loop (int): Contador de tentativas de execução/reparação do código.
    """
    
    pergunta_usuario: str
    contexto_schema: str
    codigo_gerado: str
    saida_terminal: str
    feedback_critico: str
    status: str
    tentativas_loop: int
