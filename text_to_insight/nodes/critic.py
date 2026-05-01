"""
Nó Crítico do grafo de agentes Text-to-Insight.

Responsabilidade única: avaliar se a SQL gerada e seus resultados
respondem corretamente à pergunta original do usuário.
"""

from langchain_google_genai import ChatGoogleGenerativeAI

from ..state import EstadoTextToInsight
from ..utils import extrair_tokens

PROMPT_CRITIC = """Você é um revisor de qualidade para consultas SQL geradas por IA.

Sua tarefa: avaliar se a consulta SQL e seus resultados respondem adequadamente
à pergunta original do usuário.

=== PERGUNTA DO USUÁRIO ===
{pergunta}

=== CONVERSA COM O AGENTE (se houver) ===
{conversa_previa}

=== SQL GERADA ===
{sql}

=== RESULTADO DA EXECUÇÃO ===
Status: {status_exec}
Total de linhas: {total_linhas}
Amostra dos resultados (primeiras linhas):
{preview}

=== ERROS (se houver) ===
{erro}

Avalie:
1. A SQL responde à pergunta do usuário?
2. Os resultados fazem sentido?
3. Há algum erro lógico ou de interpretação?

Ao avaliar, priorize utilidade prática e correção semântica da resposta,
não perfeição formal.

Diferenças de formato, representação ou precisão que não alterem
substancialmente a resposta NÃO devem causar reprovação.

Exemplos de casos que normalmente devem ser APROVADOS:
- Ano médio retornado como float em vez de inteiro/data
- Pequenas diferenças de arredondamento
- Colunas extras irrelevantes
- Nomes/aliases diferentes
- Resultado parcialmente correto mas ainda útil
- Agregações corretas com precisão numérica diferente da esperada

REPROVE apenas quando houver falha material, por exemplo:
- A query responde outra pergunta
- O dado necessário para responder não está presentes
- Filtros importantes estão errados ou ausentes
- JOIN incorreto altera significativamente os resultados
- Métrica errada (SUM vs AVG, COUNT vs COUNT DISTINCT, etc.)
- Resultado vazio inesperado
- Erro SQL ou inconsistência lógica grave

Considere o custo de retentativas. Em caso de dúvida entre APROVADO
e REPROVADO, prefira APROVADO se a resposta ainda for útil para o usuário. Leve em consideração que ainda tem um agente depois de você que irá interpretar o resultado da query e criar uma resposta em linguagem natural.

Responda no formato:
VEREDITO: APROVADO ou REPROVADO
FEEDBACK: <sua avaliação em 1-3 frases>"""


def nos_nodo_critico(estado: EstadoTextToInsight, llm: ChatGoogleGenerativeAI) -> dict:
    """
    Nó Crítico: usa Gemini para avaliar qualidade do resultado.
    """
    pergunta = estado.get("pergunta_usuario", "")
    sql = estado.get("sql_gerada", "")
    preview = estado.get("linhas_resultado_preview", [])
    total = estado.get("total_linhas_resultado", 0)
    saida = estado.get("saida_terminal", "")
    conversa_previa = estado.get("historico_conversa", "")
    erro = estado.get("erro_execucao", "")
    status_exec = estado.get("status", "")

    print("[CRITICO] Avaliando resultado...")

    # Se houve erro de execução, reprova direto sem gastar API
    if status_exec == "exec_erro" or erro:
        feedback = f"SQL falhou na execução: {erro}"
        print(f"[CRITICO] Reprovado por erro de execução: {erro[:80]}")
        return {
            "feedback_critico": feedback,
            "status": "reprovado",
        }

    # Formata preview para o prompt
    preview_str = str(preview[:10]) if preview else "Nenhum resultado"

    prompt = PROMPT_CRITIC.format(
        pergunta=pergunta,
        sql=sql,
        status_exec=status_exec,
        conversa_previa=conversa_previa if conversa_previa else "Nenhuma",
        total_linhas=total,
        preview=preview_str,
        erro=erro if erro else "Nenhum",
    )

    resposta = llm.invoke(prompt)
    texto = resposta.content.strip()

    # Parse do veredito
    veredito = "reprovado"
    if "APROVADO" in texto.upper():
        veredito = "aprovado"

    # Extrai feedback
    feedback = texto
    if "FEEDBACK:" in texto.upper():
        partes = texto.upper().split("FEEDBACK:")
        if len(partes) > 1:
            # Pega o texto original após FEEDBACK:
            idx = texto.upper().index("FEEDBACK:")
            feedback = texto[idx + len("FEEDBACK:"):].strip()

    print(f"[CRITICO] Veredito: {veredito}")
    print(f"[CRITICO] Feedback: {feedback[:100]}...")

    in_tokens, out_tokens, total_tokens = extrair_tokens(resposta)

    return {
        "feedback_critico": feedback,
        "status": veredito,
        "tokens_input": in_tokens,
        "tokens_output": out_tokens,
        "tokens_total": total_tokens,
    }
