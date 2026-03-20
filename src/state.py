"""
Definição do estado compartilhado do grafo de agentes Text-to-Insight.

Este módulo define a estrutura de dados que será passada entre os nós do grafo,
contendo todas as informações necessárias para o fluxo de execução.
"""

# trocando imports para incluir tipos de status e opcionais
# isso ajuda a garantir que o estado seja consistente e fácil de entender para os agentes e roteadores do grafo
from typing import Any, Literal, TypedDict
# Uso de Literal para reduzir erro de digitação e inconsistências de roteamento
StatusExecucao = Literal[
"iniciado",
"aguardando_schema",
"schema_obtido",
"pronto_codificacao",
"sql_gerada",
"sql_invalida",
"exec_ok",
"exec_erro",
"revisando_estrategia",
"aprovado",
"reprovado",
]

ClassificacaoQuery = Literal[
"easy",
"non_nested_complex",
"nested_complex",
]

# Criação de classe mãe que será estendida para EstadoTextToInsight para que pergunta_usuario e db_path sejam obrigatórios
class EstadoEntrada(TypedDict):
    pergunta_usuario: str
    db_path: str


class EstadoTextToInsight(EstadoEntrada, total = False):
    """
    Estado compartilhado do grafo Text-to-Insight (modo híbrido SQL + executor Python fixo).

    Contrato:
    - Campos obrigatórios (via EstadoEntrada):
      - pergunta_usuario: pergunta em linguagem natural.
      - db_path: caminho absoluto/relativo para o arquivo SQLite (.db).
    - Campos opcionais (preenchidos progressivamente pelos nós):
      - contexto_schema: schema textual extraído do SQLite para orientar geração de SQL.
      - query_classificacao: classe da consulta (easy, non_nested_complex, nested_complex).
      - sql_gerada: SQL produzida pelo agente para execução.
      - codigo_gerado: artefato textual opcional (mantido para compatibilidade).
      - linhas_resultado_preview: amostra de linhas retornadas (lista de dicts).
      - total_linhas_resultado: quantidade total de linhas retornadas pela consulta.
      - erro_execucao: mensagem de erro estruturada em caso de falha.
      - saida_terminal: saída textual resumida da execução.
      - feedback_critico: feedback do nó crítico para iteração.
      - status: estágio atual do fluxo, tipado por StatusExecucao.
      - tentativas_loop: contador de tentativas de geração/execução.

    Observação:
    Como total=False, os campos acima são opcionais e podem ser adicionados ao
    estado conforme cada etapa do grafo é executada.
    """
    
    contexto_schema: str
    query_classificacao: ClassificacaoQuery
    sql_gerada: str  # consulta sql gerada
    codigo_gerado: str # código python gerado
    linhas_resultado_preview: list[dict[str, Any]] # resultado da execução do código no sandbox (amostra do resultado SQL para avaliação crítica sem trafegar tudo)
    # obs: exemplo: [{"customer_id": "abc", "total": 120.5}, {"customer_id": "def", "total": 98.0}]
    ## talvez este campo não seja necessário se já temos a saída do terminal, mas pode ser útil para o crítico avaliar a qualidade do SQL gerado sem precisar analisar o código completo
    total_linhas_resultado: int # total de linhas do resultado SQL
    erro_execucao: str 
    saida_terminal: str
    feedback_critico: str
    status: StatusExecucao
    tentativas_loop: int