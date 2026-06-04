"""
Nó de Exploração - Arquitetura ReFoRCE.

Responsabilidade: investigar divergências entre candidatos paralelos
quando não há consenso, usando Chain of Thought + LLM + sandbox para
refinamento da pergunta ou interpretação dos dados.

Acionado quando: status_consenso == "ambiguo" e rodadas_exploracao < 2
Retorna: estado com pergunta refinada para nova rodada de Map-Reduce
"""

from langchain_google_genai import ChatGoogleGenerativeAI
from ..state import EstadoTextToInsight, EstadoCandidato
from ..utils import extrair_tokens
from .code_agent.code_sql import executar_sql_sqlite


PROMPT_EXPLORADOR = """Você é um investigador SQL especialista. Sua tarefa é analisar
por que diferentes candidatos SQL chegaram a resultados divergentes para a mesma pergunta.

=== PERGUNTA ORIGINAL ===
{pergunta}

=== SCHEMA DO BANCO ===
{schema}

=== CANDIDATOS E SEUS RESULTADOS ===
{candidatos_info}

=== ANÁLISE SOLICITADA ===
Use Chain of Thought para responder:

1. DIAGNÓSTICO: Por que houve divergência?
   - Qual tabela/coluna gerou ambiguidade?
   - A pergunta permite múltiplas interpretações?
   - Há erros lógicos em alguma abordagem?

2. INVESTIGAÇÃO: O que os dados reais sugerem?
   - Qual candidato parece mais correto?
   - Há padrões nos dados que esclareçam a pergunta?

3. REFINAMENTO: Como refinar a pergunta?
   - Qual interpretação é mais provável baseada na lógica?
   - A pergunta original precisa de esclarecimento?
   - Sugerir uma versão refinada se necessário.

Responda em JSON com formato:
{{
    "diagnostico": "explicação da divergência",
    "melhor_candidato": <índice 0-4 ou -1 se indeciso>,
    "pergunta_refinada": "pergunta melhorada (ou mesma se clara)",
    "recomendacao": "próximo passo"
}}
"""


def nos_nodo_explorador(estado: EstadoTextToInsight, llm: ChatGoogleGenerativeAI) -> EstadoTextToInsight:
    """
    Nó de exploração: analisa divergências entre candidatos e refina a pergunta.
    
    Args:
        estado: Estado com candidatos divergentes
        llm: Cliente LLM para análise
    
    Returns:
        Estado com pergunta_refinada (se necessário) ou insights para nova rodada
    """
    
    print("\n🔍 NÓ DE EXPLORAÇÃO: Analisando divergências entre candidatos...")
    
    candidatos = estado.get("candidatos", [])
    candidatos_validos = [c for c in candidatos if c.get("valido", False)]
    
    if len(candidatos_validos) < 2:
        print("⚠️  Menos de 2 candidatos válidos → explorando sem sucesso definido")
        estado["motivo_ambiguidade"] = "Erro nos candidatos, exploração inconclusiva"
        return estado
    
    # --- PASSO 1: Formatar informações dos candidatos ---
    candidatos_info = ""
    for i, cand in enumerate(candidatos_validos[:3]):  # Mostrar até 3 para não poluir prompt
        assinatura = cand.get("assinatura_resultado", "UNKNOWN")[:16]
        total_linhas = cand.get("resultado_execucao", {}).get("total_linhas", 0)
        sql = cand.get("sql", "")[:100]
        
        candidatos_info += f"""
Candidato {i}:
  - SQL: {sql}...
  - Total linhas: {total_linhas}
  - Assinatura: {assinatura}...
"""
    
    # --- PASSO 2: Invocar LLM para análise Chain of Thought ---
    schema = estado.get("contexto_schema", "") or estado.get("contexto_rag_schema", "")
    pergunta = estado.get("pergunta_atual", "")
    
    prompt = PROMPT_EXPLORADOR.format(
        pergunta=pergunta,
        schema=schema[:1000],  # Limitar para não exceder token limit
        candidatos_info=candidatos_info,
    )
    
    try:
        resposta_llm = llm.invoke(prompt)
        resposta_texto = resposta_llm.content
        
        # Tentar parsear como JSON
        import json
        import re
        
        # Extrair JSON da resposta
        match = re.search(r'\{.*\}', resposta_texto, re.DOTALL)
        if match:
            analise = json.loads(match.group())
        else:
            analise = {
                "diagnostico": resposta_texto,
                "melhor_candidato": -1,
                "pergunta_refinada": pergunta,
                "recomendacao": "nova rodada com investigação manual"
            }
        
        print(f"📊 Análise LLM:")
        print(f"   Diagnóstico: {analise.get('diagnostico', '')[:100]}...")
        print(f"   Melhor candidato: {analise.get('melhor_candidato', -1)}")
        print(f"   Pergunta refinada: {analise.get('pergunta_refinada', pergunta)[:80]}...")
        
        # Atualizar estado com insights
        estado["motivo_ambiguidade"] = analise.get("diagnostico", "")
        
        # Se LLM sugeriu refinamento, atualizar pergunta
        pergunta_refinada = analise.get("pergunta_refinada", pergunta)
        if pergunta_refinada != pergunta:
            estado["pergunta_atual"] = pergunta_refinada
            print(f"   ✓ Pergunta refinada: {pergunta_refinada}")
        
        # ✅ NOVO: Garantir que candidatos são zerados para próxima rodada
        # (evita acúmulo mesmo sem operator.add)
        estado["candidatos"] = []
        estado["status_consenso"] = "nao_votado"
        
        print(f"   ✓ Candidatos resetados (lista vazia para próxima rodada Fan-out)")
        
        # Token tracking
        in_tokens, out_tokens, total_tokens = extrair_tokens(resposta_llm)
        estado["tokens_input"] = in_tokens
        estado["tokens_output"] = out_tokens
        estado["tokens_total"] = total_tokens
        
    except Exception as e:
        print(f"⚠️  Erro ao processar análise LLM: {e}")
        estado["motivo_ambiguidade"] = f"Erro na exploração: {str(e)}"
    
    return estado
