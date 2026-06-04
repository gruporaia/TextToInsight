"""
Funções de roteamento condicional para o grafo Text-to-Insight.

As arestas condicionais decidem para qual nó o grafo deve prosseguir
baseado nas condições do estado atual.
"""

from typing import Literal
from langgraph.types import Send
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


def roteador_planejador(estado: EstadoTextToInsight) -> Literal["esquema", "agente_codigo", "critico", "fim"]:
    """
    Roteador após Planejador.

    - contexto_schema vazio → esquema
    - pronto_codificacao ou revisando_estrategia → agente_codigo
    - aprovado → fim
    """
    contexto = estado.get("contexto_schema", "")
    status = estado.get("status", "")
    esperar = estado.get("espera_humana", False)

    print(f"[ROTEADOR_PLANEJADOR] Status: {status}, Schema preenchido: {bool(contexto)}")

    if esperar:
        print("[ROTEADOR_PLANEJADOR] Espera humana ativa → espera_humana")
        return "espera_humana"

    if not contexto:
        print("[ROTEADOR_PLANEJADOR] Schema vazio → esquema")
        return "esquema"

    if status in ("pronto_codificacao", "revisando_estrategia"):
        print("[ROTEADOR_PLANEJADOR] → agente_codigo")
        return "agente_codigo"

    if status == "aprovado":
        print("[ROTEADOR_PLANEJADOR] Aprovado → fim")
        return "fim"

    # Default: gera código
    print("[ROTEADOR_PLANEJADOR] Default → planejador")
    return "planejador"


def roteador_grafico(estado: EstadoTextToInsight, llm: ChatGoogleGenerativeAI) -> Literal["gerador_grafico", "resposta"]:
    """
    Roteador após salvar CSV: decide se gera gráfico ou vai direto para resposta.

    Usa o LLM para avaliar se a pergunta e os dados justificam uma visualização.
    """
    pergunta = estado.get("pergunta_usuario", "")
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


# ============================================================================
# ARQUITETURA ReFoRCE: Roteadores para Map-Reduce
# ============================================================================

def roteador_fan_out(estado: EstadoTextToInsight) -> list[Send]:
    """
    Roteador Fan-out: cria 5 objetos Send para execução paralela de candidatos,
    injetando diversidade térmica (temperaturas diferentes).
    """
    from ..state import EstadoCandidato
    
    # Extrair contexto do estado pai
    pergunta = (
        estado.get("pergunta_atual", "")
        or estado.get("pergunta_original", "")
        or estado.get("pergunta_usuario", "")
    )
    schema = estado.get("contexto_rag_schema", "") or estado.get("contexto_schema", "")
    db_path = estado.get("db_path", "")
    historico = estado.get("historico_tentativas", [])
    
    # Diversidade Térmica: Do mais determinístico (0.0) ao mais criativo (0.9)
    temperaturas = [0.0, 0.2, 0.5, 0.7, 0.9]
    
    print(f"[ROTEADOR_FAN_OUT] Criando 5 candidatos em paralelo...")
    print(f"  Pergunta: {pergunta[:60]}...")
    print(f"  Temperaturas: {temperaturas}")
    
    sends = []
    for i, temp in enumerate(temperaturas):
        # Estado isolado para este candidato COM CONTEXTO COMPARTILHADO
        estado_candidato: EstadoCandidato = {
            "sql": "",
            "resultado_execucao": {},
            "erro": "",
            "tentativas_refinamento": 0,
            "valido": False,
            "assinatura_resultado": "",
            
            # A mágica da diversidade acontece aqui
            "temperatura": temp,
            
            # Contexto repassado
            "pergunta": pergunta,
            "schema": schema,
            "db_path": db_path,
            "historico_tentativas": historico,
        }
        
        # O nome do nó "gerador_candidato" deve corresponder ao add_node no graph.py
        send_obj = Send("gerador_candidato", estado_candidato)
        sends.append(send_obj)
        print(f"  → Send[{i}] criado com Temp={temp}")
    
    return sends


def roteador_votacao(estado: EstadoTextToInsight) -> Literal["salvar_csv", "nos_nodo_explorador", "resposta"]:
    """
    Roteador após votação: decide próximo passo baseado no status_consenso.
    
    - consenso_encontrado → salvar_csv (SQL aprovada)
    - ambiguo (rodadas < 2) → nos_nodo_explorador
    - ambiguo (rodadas >= 2) → resposta (fallback)
    """
    status_consenso = estado.get("status_consenso", "nao_votado")
    rodadas_exploracao = estado.get("rodadas_exploracao", 0)
    max_rodadas = 5
    
    print(f"[ROTEADOR_VOTACAO] Status: {status_consenso}, Rodadas: {rodadas_exploracao}/{max_rodadas}")
    
    if status_consenso == "consenso_encontrado":
        print("[ROTEADOR_VOTACAO] ✅ Consenso → salvar_csv")
        return "salvar_csv"
    
    if status_consenso == "ambiguo":
        if rodadas_exploracao < max_rodadas:
            print("[ROTEADOR_VOTACAO] ❌ Sem consenso → nos_nodo_explorador (exploração)")
            return "nos_nodo_explorador"
        else:
            print(f"[ROTEADOR_VOTACAO] ⚠️ Limite de exploração atingido → resposta (fallback)")
            return "resposta"
    
    # Default: fallback para resposta
    print("[ROTEADOR_VOTACAO] Default → resposta (status desconhecido)")
    return "resposta"
