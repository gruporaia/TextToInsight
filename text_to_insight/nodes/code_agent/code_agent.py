"""
Nó Agente de Código do grafo de agentes Text-to-Insight.

Responsabilidade única: gerar SQL executável a partir da pergunta do usuário,
do contexto do schema e de feedback anterior (se houver), usando Gemini.
"""

import re
from langchain_google_genai import ChatGoogleGenerativeAI

from ...state import EstadoTextToInsight
from ...utils import extrair_tokens

PROMPT_TEMPLATE = """Você é um especialista em SQL para bancos SQLite.

Sua tarefa: gerar UMA única consulta SQL SELECT que responda à pergunta do usuário,
usando o schema do banco de dados fornecido abaixo.

Regras:
- Gere APENAS uma consulta SELECT (ou WITH/CTE seguido de SELECT).
- NÃO use INSERT, UPDATE, DELETE, DROP, ALTER ou qualquer comando de escrita.
- NÃO inclua explicações, apenas a SQL pura.
- Use nomes de tabelas e colunas EXATAMENTE como aparecem no schema.
- Se a pergunta for ambígua, faça a interpretação mais razoável.

=== SCHEMA DO BANCO ===
{schema}

=== PERGUNTA DO USUÁRIO ===
{pergunta}

=== CONVERSA PRÉVIA (CONTEXTO ADICIONAL) ===
{conversa_previa}

=== HISTÓRICO DE TENTATIVAS ANTERIORES ===
{historico_tentativas_section}

Responda APENAS com a consulta SQL, sem markdown, sem explicação."""


def _extrair_sql(resposta: str) -> str:
    """Extrai SQL pura da resposta do LLM, removendo markdown e texto extra."""
    # Remove blocos de código markdown
    match = re.search(r"```(?:sql)?\s*\n?(.*?)```", resposta, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Se não tem markdown, retorna a resposta limpa
    return resposta.strip()


def _formatar_historico_tentativas(historico: list[dict]) -> str:
    """Formata o histórico de tentativas anteriores para inclusão no prompt."""
    if not historico:
        return "Nenhuma tentativa anterior."

    partes = []
    for i, tent in enumerate(historico, 1):
        bloco = f"--- Tentativa {i} ---\n"
        bloco += f"SQL gerada:\n{tent.get('sql', '(vazia)')}\n"
        if tent.get("erro"):
            bloco += f"Erro de execução: {tent['erro']}\n"
        if tent.get("feedback"):
            bloco += f"Feedback do crítico: {tent['feedback']}\n"
        partes.append(bloco)

    return "\n".join(partes) + "\nNÃO repita os mesmos erros. Gere uma SQL diferente e corrigida."


def nos_nodo_agente_codigo(estado: EstadoTextToInsight, llm: ChatGoogleGenerativeAI) -> dict:
    """
    Nó Agente de Código: usa Gemini para gerar SQL a partir da pergunta + schema.
    """
    pergunta = estado.get("pergunta_usuario", "")
    conversa_previa = estado.get("historico_conversa", "")
    schema = estado.get("contexto_schema", "")
    historico = estado.get("historico_tentativas", [])
    tentativas = estado.get("tentativas_loop", 0)

    print(f"[AGENTE_CODIGO] Gerando SQL (tentativa {tentativas + 1})...")

    historico_section = _formatar_historico_tentativas(historico)

    prompt = PROMPT_TEMPLATE.format(
        schema=schema,
        pergunta=pergunta,
        conversa_previa=conversa_previa if conversa_previa else "Nenhuma",
        historico_tentativas_section=historico_section,
    )
    
    resposta = llm.invoke(prompt)
    sql = _extrair_sql(resposta.content)

    print(f"[AGENTE_CODIGO] SQL gerada: {sql[:100]}...")

    in_tokens, out_tokens, total_tokens = extrair_tokens(resposta)

    return {
        "sql_gerada": sql,
        "status": "sql_gerada",
        "tentativas_loop": tentativas + 1,
        # Retornando o número de tokens nessa chamada do Gemini
        "tokens_input": in_tokens,
        "tokens_output": out_tokens,
        "tokens_total": total_tokens,
    }

