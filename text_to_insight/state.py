"""
Definição do estado compartilhado do grafo de agentes Text-to-Insight.

Este módulo define a estrutura de dados que será passada entre os nós do grafo,
contendo todas as informações necessárias para o fluxo de execução.
"""

# trocando imports para incluir tipos de status e opcionais
# isso ajuda a garantir que o estado seja consistente e fácil de entender para os agentes e roteadores do grafo

# --> Adição dos imports de Annotated e operator para possibilitar o tracking dos tokens.
from typing import Any, Literal, TypedDict, Annotated
import operator
# Uso de Literal para reduzir erro de digitação e inconsistências de roteamento
StatusExecucao = Literal[
"iniciado",
"aguardando_schema",
"schema_obtido",
"pronto_codificacao",
"sql_gerada",
"exec_ok",
"exec_erro",
"revisando_estrategia",
"aprovado",
"reprovado",
]

# Criação de classe mãe que será estendida para EstadoTextToInsight para que
# pergunta_original/pergunta_atual sejam obrigatorios.
# A conexão com o banco NÃO vive no estado (não é serializável pelo
# checkpointer): ela é injetada nos nós que tocam o banco. O `db_path` permanece
# como metadado OPCIONAL, usado apenas para cache de schema e logs.
class EstadoEntrada(TypedDict):
    pergunta_original: str
    pergunta_atual: str


class EstadoTextToInsight(EstadoEntrada, total = False):
    """
    Estado compartilhado do grafo Text-to-Insight (MVP SQL-only).

    Campos obrigatórios (via EstadoEntrada):
      - pergunta_original: pergunta inicial do usuario (imutavel apos o primeiro set).
      - pergunta_atual: pergunta corrente, fonte de verdade para o fluxo.

    Campos opcionais (preenchidos progressivamente pelos nós):
      - db_path: caminho para o arquivo SQLite (.db), quando aplicável.
        Metadado opcional para cache de schema/logs; o acesso ao banco usa a
        conexão injetada nos nós.
      - contexto_schema: schema textual extraído do SQLite.
      - sql_gerada: SQL produzida pelo agente de código.
      - linhas_resultado_preview: amostra de linhas retornadas (max 30).
      - total_linhas_resultado: total de linhas do resultado SQL.
      - erro_execucao: mensagem de erro em caso de falha.
      - saida_terminal: saída textual resumida da execução.
      - feedback_critico: feedback do nó crítico para iteração.
      - status: estágio atual do fluxo (StatusExecucao).
      - tentativas_loop: contador de tentativas de geração/execução.
    """
    
    db_path: str
    contexto_schema: str
    contexto_rag_schema: str
    tem_descricao : bool
    sql_gerada: str
    linhas_resultado_preview: list[dict[str, Any]]
    total_linhas_resultado: int
    erro_execucao: str
    saida_terminal: str
    feedback_critico: str
    status: StatusExecucao
    espera_humana: bool
    pergunta_ao_usuario: str
    historico_conversa: list[tuple[str, str]]
    tentativas_loop: int
    tentativas_revisao_retriever: int
    resposta_natural: str
    historico_tentativas: Annotated[list[dict[str, str]], operator.add]
    linhas_resultado_completo: list[dict[str, Any]]
    schemacrawler_bin : str | None
    db_config: dict[str, Any] | None
    inferir_fks_virtuais: bool
    inferir_pks_virtuais: bool
    usar_schemacrawler: bool

    # Campos para geração de gráficos
    caminho_csv_resultado: str
    caminho_grafico: str
    grafico_gerado: bool

    # Campos exclusivos para métricas. Possibilita a soma automática dos tokens utilizados
    # por cada chamada do Gemini nos vários diferentes nós.
    tokens_input: Annotated[int, operator.add]
    tokens_output: Annotated[int, operator.add]
    tokens_total: Annotated[int, operator.add]