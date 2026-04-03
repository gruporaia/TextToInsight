"""
Nó Planejador do grafo de agentes Text-to-Insight.

Responsabilidade única: interpretar a pergunta do usuário e o contexto atual
para decidir a próxima etapa do fluxo (status de roteamento).
"""

from langchain_google_genai import ChatGoogleGenerativeAI

from ..state import EstadoTextToInsight
# Importando a função de extração de tokens
from ..utils import extrair_tokens

PROMPT_PLANNER = """Você é o planejador de um sistema que transforma perguntas em consultas SQL.

Seu papel: analisar a situação atual e decidir a próxima ação.

Contexto atual:
- Pergunta do usuário: "{pergunta}"
- Schema disponível: {schema_disponivel}
- Feedback do crítico: {feedback}
- Tentativas realizadas: {tentativas}
- Status atual: {status_atual}
- Erro anterior: {erro}

Decida a próxima ação respondendo com EXATAMENTE uma das opções abaixo:
- "pronto_codificacao" → se temos schema e devemos gerar/regenerar SQL
- "revisando_estrategia" → se o crítico reprovou e devemos tentar uma abordagem diferente
- "aprovado" → se o resultado já foi aprovado pelo crítico

Responda APENAS com uma das opções acima, sem explicação."""


def nos_nodo_planejador(estado: EstadoTextToInsight, llm: ChatGoogleGenerativeAI) -> dict:
    """
    Nó Planejador: decide a próxima etapa do fluxo.

    Lógica determinística para schema vazio; LLM para decisões mais complexas.
    """
    pergunta = estado.get("pergunta_usuario", "")
    schema = estado.get("contexto_schema", "")
    feedback = estado.get("feedback_critico", "")
    tentativas = estado.get("tentativas_loop", 0)
    status = estado.get("status", "iniciado")
    erro = estado.get("erro_execucao", "")

    print(f"[PLANEJADOR] Pergunta: {pergunta[:50]}... | Status: {status}")

    # Caso determinístico: sem schema, precisa buscá-lo primeiro
    if not schema:
        print("[PLANEJADOR] Schema vazio → aguardando_schema")
        return {
            "status": "aguardando_schema",
            "tentativas_loop": tentativas,
        }

    # Se já foi aprovado, mantém
    if status == "aprovado":
        print("[PLANEJADOR] Já aprovado → mantendo status")
        return {"status": "aprovado", "tentativas_loop": tentativas}

    # Usa LLM para decidir estratégia
    prompt = PROMPT_PLANNER.format(
        pergunta=pergunta,
        schema_disponivel="Sim" if schema else "Não",
        feedback=feedback if feedback else "Nenhum",
        tentativas=tentativas,
        status_atual=status,
        erro=erro if erro else "Nenhum",
    )

    resposta = llm.invoke(prompt)
    decisao = resposta.content.strip().strip('"').lower()

    # Mapeia resposta para status válido
    status_validos = ["pronto_codificacao", "revisando_estrategia", "aprovado"]
    if decisao not in status_validos:
        # Fallback: se tem feedback, revisa; senão, gera código
        decisao = "revisando_estrategia" if feedback else "pronto_codificacao"

    print(f"[PLANEJADOR] Decisão LLM: {decisao}")

    in_tokens, out_tokens, total_tokens = extrair_tokens(resposta)
    
    return {
        "status": decisao,
        "tentativas_loop": tentativas,

        "tokens_input": in_tokens,
        "tokens_output": out_tokens,
        "tokens_total": total_tokens,       
    }
