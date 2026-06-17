"""
Nó Planejador do grafo de agentes Text-to-Insight.

Responsabilidade única: interpretar a pergunta do usuário e o contexto atual
para decidir a próxima etapa do fluxo (status de roteamento).
"""

import json
from langchain_google_genai import ChatGoogleGenerativeAI

from ..state import EstadoTextToInsight
# Importando a função de extração de tokens
from ..utils import extrair_tokens

PROMPT_PLANNER = """Você é o planejador de um sistema que transforma perguntas em consultas SQL.

Seu papel: analisar a situação atual e decidir a próxima ação.

Contexto atual:
- Pergunta do usuário: "{pergunta}"

- conversa_previa: {conversa_previa}

- Schema: {schema}

- Feedback do crítico: {feedback}
- Tentativas realizadas: {tentativas}
- Status atual: {status_atual}
- Erro anterior: {erro}

{diretrizes}
"""


def nos_nodo_planejador(estado: EstadoTextToInsight, llm: ChatGoogleGenerativeAI, hitl: bool) -> dict:
    """
    Nó Planejador: decide a próxima etapa do fluxo.

    Lógica determinística para schema vazio; LLM para decisões mais complexas.
    """
    pergunta = (
        estado.get("pergunta_atual", "")
        or estado.get("pergunta_original", "")
        or estado.get("pergunta_usuario", "")
    )
    conversa_previa = estado.get("historico_conversa", "")
    schema = estado.get("contexto_schema", "")
    contexto_rag_schema = estado.get("contexto_rag_schema", "")
    feedback = estado.get("feedback_critico", "")
    tentativas = estado.get("tentativas_loop", 0)
    status = estado.get("status", "iniciado")
    erro = estado.get("erro_execucao", "")
    # Contrato estável para métricas: sempre retornamos esses campos,
    # mesmo quando o nó não chama LLM (valor zero).
    in_tokens = 0
    out_tokens = 0
    total_tokens = 0

    print(f"[PLANEJADOR] Pergunta: {pergunta[:50]}... | Status: {status}")

    # Caso determinístico: sem schema, precisa buscá-lo primeiro
    if not schema:
        print("[PLANEJADOR] Schema vazio → aguardando_schema")
        # Retorno antecipado sem uso de LLM: tokens ficam zerados.
        return {
            "status": "aguardando_schema",
            "tentativas_loop": tentativas,
            "tokens_input": in_tokens,
            "tokens_output": out_tokens,
            "tokens_total": total_tokens,
        }

    # Se já foi aprovado, mantém
    if status == "aprovado":
        print("[PLANEJADOR] Já aprovado → mantendo status")
        # Sem nova chamada ao modelo: preserva contadores em zero.
        return {
            "status": "aprovado",
            "tentativas_loop": tentativas,
            "tokens_input": in_tokens,
            "tokens_output": out_tokens,
            "tokens_total": total_tokens,
        }
    
    diretrizes = """Responda EXATAMENTE no formato JSON abaixo, sem formatação markdown (```json):
{
    "decisao": "escolha_uma_opcao"
}
Opções válidas para 'decisão':
- "pronto_codificacao" → se temos schema, a pergunta faz sentido e devemos gerar/regenerar SQL
- "revisando_estrategia" → se o crítico reprovou e devemos tentar uma abordagem diferente"""

    if hitl:
        diretrizes = """AVALIAÇÃO CRÍTICA:
Verifique se a "Pergunta do usuário" pode ser respondida com as tabelas e colunas do Schema.
Se houver ambiguidade, conceitos não mapeados no banco de dados, ou se a intenção do usuário não estiver clara, você DEVE pedir mais informações.

Responda EXATAMENTE no formato JSON abaixo, sem formatação markdown (```json):
{
    "decisao": "escolha_uma_opcao",
    "pergunta_ao_usuario": "escreva a pergunta aqui se precisar de ajuda, ou deixe vazio se não precisar"
}

Opções válidas para 'decisão':
- "pronto_codificacao" → se temos schema, a pergunta faz sentido e devemos gerar/regenerar SQL
- "revisando_estrategia" → se o crítico reprovou e devemos tentar uma abordagem diferente
- "necessita_ajuda" → a pergunta não é clara, não faz sentido, falta contexto ou não há dados no schema para responder."""

    # Usa LLM para decidir estratégia
    prompt = PROMPT_PLANNER.format(
        pergunta=pergunta,
        schema_disponivel="Sim" if schema else "Não",
        feedback=feedback if feedback else "Nenhum",
        tentativas=tentativas,
        status_atual=status,
        erro=erro if erro else "Nenhum",
        schema=contexto_rag_schema if contexto_rag_schema else schema,
        conversa_previa=conversa_previa if conversa_previa else "Nenhuma",
        diretrizes=diretrizes,
    )

    resposta_llm = llm.invoke(prompt)
    # A partir daqui houve chamada ao LLM; registramos tokens reais da resposta.
    in_tokens, out_tokens, total_tokens = extrair_tokens(resposta_llm)
    conteudo_bruto = resposta_llm.content.strip()

    # Limpeza caso o LLM retorne blocos de código markdown (```json ... ```)
    if conteudo_bruto.startswith("```json"):
        conteudo_bruto = conteudo_bruto[7:-3].strip()
    elif conteudo_bruto.startswith("```"):
        conteudo_bruto = conteudo_bruto[3:-3].strip()

    try:
        dados_resposta = json.loads(conteudo_bruto)
        decisao = dados_resposta.get("decisao", "").lower()
        pergunta_agente = dados_resposta.get("pergunta_ao_usuario", "").strip()
    except json.JSONDecodeError:
        print(f"[PLANEJADOR] Erro ao parsear JSON: {conteudo_bruto}")
        # Fallback de segurança
        decisao = "revisando_estrategia" if feedback else "pronto_codificacao"
        pergunta_agente = ""

    # Mapeia para os estados do grafo e levanta a flag de HITL se necessário
    if decisao == "necessita_ajuda":
        print(f"[PLANEJADOR] Solicitando ajuda: {pergunta_agente}")
        # Mesmo no fluxo HITL, retornamos tokens para manter consistência
        # para CSV, dashboards e testes que consomem o estado.
        return {
            "status": "aguardando_input", 
            "espera_humana": True,            # Flag que o router vai ler
            "pergunta_ao_usuario": pergunta_agente, # A pergunta que vai aparecer no terminal
            "tentativas_loop": tentativas,
            "tokens_input": in_tokens,
            "tokens_output": out_tokens,
            "tokens_total": total_tokens,
        }

    # Mapeia resposta para status válido
    status_validos = ["pronto_codificacao", "revisando_estrategia", "aprovado"]
    if decisao not in status_validos:
        # Fallback: se tem feedback, revisa; senão, gera código
        decisao = "revisando_estrategia" if feedback else "pronto_codificacao"

    print(f"[PLANEJADOR] Decisão LLM: {decisao}")

    return {
        "status": decisao,
        "espera_humana": False,  
        "tentativas_loop": tentativas,
        "tokens_input": in_tokens,
        "tokens_output": out_tokens,
        "tokens_total": total_tokens,       
    }
