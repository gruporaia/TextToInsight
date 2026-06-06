"""
Funções de roteamento condicional para o grafo Text-to-Insight.

As arestas condicionais decidem para qual nó o grafo deve prosseguir
baseado nas condições do estado atual.
"""

from typing import Literal
from langchain_google_genai import ChatGoogleGenerativeAI

from ..state import EstadoTextToInsight
from ..utils import extrair_tokens

PROMPT_ROTEADOR_GRAFICO = """Você é um assistente que decide se os resultados de uma consulta SQL
devem ser acompanhados de um gráfico (visualização).

Analise a pergunta do usuário e as características dos dados retornados.

=== PERGUNTA DO USUÁRIO ===
{pergunta}

=== COLUNAS DO RESULTADO ===
{colunas}

=== TOTAL DE LINHAS ===
{total_linhas}

=== AMOSTRA DOS DADOS ===
{amostra}

Um gráfico é útil quando:
- A pergunta envolve comparações entre categorias (ex: vendas por região)
- Há dados temporais ou tendências (ex: evolução ao longo dos meses)
- Há distribuições ou rankings (ex: top 10 produtos)
- Há agregações numéricas que se beneficiam de visualização
- O resultado tem mais de 1 linha com pelo menos uma coluna numérica

Um gráfico NÃO é útil quando:
- O resultado é um único valor escalar (ex: total geral)
- A pergunta pede um dado específico pontual (ex: nome de um cliente)
- O resultado tem apenas 1 linha
- Não há colunas numéricas para plotar

Responda APENAS com uma palavra: SIM ou NAO
"""


def roteador_sandbox(estado: EstadoTextToInsight) -> Literal["critico", "planejador"]:
    """
    Roteador após execução do Executor (sandbox).

    - exec_ok → critico (avaliar resultado)
    - exec_erro + tentativas < 3 → planejador (reconsiderar)
    - tentativas >= 3 → crítico (desistir/reiniciar -> encerrar loop)
    """
    status = estado.get("status", "")
    tentativas = estado.get("tentativas_loop", 0)

    print(f"[ROTEADOR_SANDBOX] Status: {status}, Tentativas: {tentativas}")

    if status == "exec_ok":
        print("[ROTEADOR_SANDBOX] Execução OK → critico")
        return "critico"

    if status == "exec_erro" and tentativas < 3:
        print("[ROTEADOR_SANDBOX] Erro detectado → planejador para retry")
        return "planejador"

    print("[ROTEADOR_SANDBOX] Muitas tentativas ou erro → critico (para forçar o fim do loop)")
    return "critico"


def roteador_planejador(estado: EstadoTextToInsight) -> Literal["esquema", "agente_codigo", "planejador", "fim", "espera_humana"]:
    """
    Roteador após Planejador.

    - contexto_schema vazio → esquema
    - pronto_codificacao ou revisando_estrategia → agente_codigo
    - aprovado → fim
    """
    contexto = estado.get("contexto_schema", "")
    status = estado.get("status", "")
    esperar = estado.get("espera_humana", False)
    tentativas_revisao = estado.get("tentativas_revisao_retriever", 0)

    MAX_TENTATIVAS_REVISAO = 2

    print(f"[ROTEADOR_PLANEJADOR] Status: {status}, Schema preenchido: {bool(contexto)}")

    if esperar:
        print("[ROTEADOR_PLANEJADOR] Espera humana ativa → espera_humana")
        return "espera_humana"

    if not contexto:
        print("[ROTEADOR_PLANEJADOR] Schema vazio → esquema")
        return "esquema"

    if status == "pronto_codificacao":
        print("[ROTEADOR_PLANEJADOR] → agente_codigo")
        return "agente_codigo"

    if status == "revisando_estrategia":
        if len(contexto) < 1500:
            print(
                f"[ROTEADOR_PLANEJADOR] Schema pequeno ({len(contexto)} chars), "
                f"RAG não ajudaria → agente_codigo (direto)"
            )
            return "agente_codigo"
        if tentativas_revisao >= MAX_TENTATIVAS_REVISAO:
            print(
                f"[ROTEADOR_PLANEJADOR] Limite de {MAX_TENTATIVAS_REVISAO} expansões "
                f"RAG atingido → agente_codigo (forçado)"
            )
            return "agente_codigo"
        print("[ROTEADOR_PLANEJADOR] Revisando estratégia → retriever (expandir contexto RAG)")
        return "retriever"

    if status == "aprovado":
        print("[ROTEADOR_PLANEJADOR] Aprovado → fim")
        return "fim"

    # Default: status não reconhecido — forçar geração de código para evitar auto-loop
    print(f"[ROTEADOR_PLANEJADOR] Status não reconhecido '{status}' → agente_codigo (safety net)")
    return "agente_codigo"

def roteador_schema(estado: EstadoTextToInsight) -> Literal["retriever", "enriquecimento_rag"]:
    tem_descricao = estado.get("tem_descricao", False)
    return "retriever" if tem_descricao else "enriquecimento_rag"

def roteador_grafico(estado: EstadoTextToInsight, llm: ChatGoogleGenerativeAI) -> Literal["gerador_grafico", "resposta"]:
    """
    Roteador após salvar CSV: decide se gera gráfico ou vai direto para resposta.

    Usa o LLM para avaliar se a pergunta e os dados justificam uma visualização.
    """
    pergunta = estado.get("pergunta_atual", "")
    preview = estado.get("linhas_resultado_preview", [])
    total = estado.get("total_linhas_resultado", 0)
    csv_path = estado.get("caminho_csv_resultado", "")

    # Se não há CSV ou dados, pular gráfico
    if not csv_path or not preview or total == 0:
        print("[ROTEADOR_GRAFICO] Sem dados para gráfico → resposta")
        return "resposta"

    # Se resultado é uma única linha, provavelmente não precisa de gráfico
    if total == 1:
        print("[ROTEADOR_GRAFICO] Apenas 1 linha → resposta")
        return "resposta"

    colunas = list(preview[0].keys()) if preview and isinstance(preview[0], dict) else []
    amostra_str = str(preview[:5]) if preview else "(vazio)"

    prompt = PROMPT_ROTEADOR_GRAFICO.format(
        pergunta=pergunta,
        colunas=", ".join(colunas) if colunas else "(desconhecidas)",
        total_linhas=total,
        amostra=amostra_str,
    )

    try:
        resposta = llm.invoke(prompt)
        texto = resposta.content.strip().upper()

        in_tokens, out_tokens, total_tokens = extrair_tokens(resposta)

        decisao = "SIM" in texto
        print(f"[ROTEADOR_GRAFICO] Decisão LLM: {'SIM' if decisao else 'NAO'} → {'gerador_grafico' if decisao else 'resposta'}")

        if decisao:
            return "gerador_grafico"
        else:
            return "resposta"
    except Exception as e:
        print(f"[ROTEADOR_GRAFICO] Erro ao consultar LLM: {e} → resposta (fallback)")
        return "resposta"
