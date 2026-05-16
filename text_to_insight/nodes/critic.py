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

=== SCHEMA DO BANCO ===
{schema}

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

=== TENTATIVAS ANTERIORES ===
{historico_tentativas_section}

=== EXEMPLOS DE AVALIAÇÃO ===

-- EXEMPLO 1: REPROVADO (escopo incompleto) --
Pergunta: "Which airport has the least number of flights?"
SQL: SELECT SourceAirport FROM flights GROUP BY SourceAirport ORDER BY COUNT(*) ASC LIMIT 1
Resultado: [('AID',)]
VEREDITO: REPROVADO
Razão: A query conta apenas voos com partida (SourceAirport) e ignora voos com chegada (DestAirport).
O escopo da pergunta é "flights" em geral — a query responde a uma pergunta diferente.

-- EXEMPLO 2: REPROVADO (erro semântico: MIN vs MAX) --
Pergunta: "Which Asian countries have a population larger than any country in Africa?"
SQL: SELECT Name FROM country WHERE Continent='Asia' AND Population > (SELECT MAX(Population) FROM country WHERE Continent='Africa')
Resultado: [] (vazio)
VEREDITO: REPROVADO
Razão: "Larger than any country in Africa" significa maior que pelo menos um país africano (MIN),
não maior que todos os países africanos (MAX). A lógica está semanticamente errada.

-- EXEMPLO 3: REPROVADO (resultado vazio suspeito) --
Pergunta: "Find the last name of students who live in North Carolina and are not enrolled in any degree."
SQL: SELECT last_name FROM Students WHERE state_province_county = 'North Carolina' AND ...
Resultado: [] (vazio)
VEREDITO: REPROVADO
Razão: Resultado vazio quando a pergunta espera dados reais é suspeito. Verifique se o filtro
de string corresponde exatamente ao valor no banco (ex: 'NorthCarolina' vs 'North Carolina').

-- EXEMPLO 4: REPROVADO (JOIN incorreto muda o que está sendo contado) --
Pergunta: "Find the name of makers that produced some cars in 1970."
SQL: SELECT DISTINCT Maker FROM car_makers JOIN car_names ON car_makers.Id = car_names.MakeId JOIN cars_data ON car_names.MakeId = cars_data.Id WHERE cars_data.Year = 1970
Resultado: [('chevrolet',), ('buick',)]
VEREDITO: REPROVADO
Razão: O JOIN usa car_names.MakeId para conectar a cars_data, mas cars_data.Id refere-se
ao ID do carro, não do fabricante. O caminho correto seria via model_list. Os resultados
parecem plausíveis mas derivam de uma junção incorreta.

-- EXEMPLO 5: APROVADO (formato diferente, resposta correta) --
Pergunta: "On average, when were the transcripts printed?"
SQL: SELECT AVG(transcript_date) AS average_transcript_date FROM Transcripts
Resultado: [('1989.9333333333334',)]
VEREDITO: APROVADO
Razão: O resultado é um número que representa a média das datas (formato numérico do SQLite).
Embora não seja uma data formatada, responde corretamente à pergunta. Diferença de
representação não é motivo de reprovação.

-- EXEMPLO 6: APROVADO (query mais simples que o gold, resultado equivalente) --
Pergunta: "Which model of car has the minimum horsepower?"
SQL: SELECT Model FROM car_names JOIN cars_data ON car_names.MakeId = cars_data.Id WHERE Horsepower = (SELECT MIN(Horsepower) FROM cars_data) LIMIT 1
Resultado: [('triumph',)]
VEREDITO: APROVADO
Razão: A query retorna corretamente o modelo com menor potência. O LIMIT 1 garante unicidade
e o resultado é semanticamente correto. Aprovar.

=== CRITÉRIOS DE AVALIAÇÃO ===

REPROVE quando houver:
- Escopo incompleto: query cobre apenas parte do que a pergunta pede
- Erro semântico: lógica correta na forma mas errada no significado (MIN vs MAX, ANY vs ALL)
- JOIN incorreto que altera os dados sendo agregados ou filtrados
- Resultado vazio quando a pergunta claramente espera dados
- Filtro com valor literal diferente do que está no banco
- Métrica errada (SUM vs AVG, COUNT vs COUNT DISTINCT, etc.)
- Erro de execução SQL

APROVE quando:
- O resultado responde à pergunta, mesmo com formato ou representação diferente
- Há colunas extras que não prejudicam a resposta
- A precisão numérica difere mas o valor está correto
- A query é mais simples que o esperado mas semanticamente equivalente

Avalie com rigor semântico. Resultados que parecem plausíveis mas derivam de lógica
incorreta devem ser reprovados. Não presuma que uma query bem-formada está correta.

Responda no formato:
VEREDITO: APROVADO ou REPROVADO
FEEDBACK: <sua avaliação em 1-3 frases>"""


def _formatar_historico_para_critico(historico: list[dict]) -> str:
    """Formata o histórico de tentativas anteriores para o prompt do crítico."""
    if not historico:
        return "Nenhuma tentativa anterior (esta é a primeira)."

    partes = []
    for i, tent in enumerate(historico, 1):
        bloco = f"--- Tentativa {i} ---\n"
        bloco += f"SQL: {tent.get('sql', '(vazia)')}\n"
        if tent.get("erro"):
            bloco += f"Erro: {tent['erro']}\n"
        if tent.get("feedback"):
            bloco += f"Feedback: {tent['feedback']}\n"
        partes.append(bloco)

    return "\n".join(partes)


def nos_nodo_critico(estado: EstadoTextToInsight, llm: ChatGoogleGenerativeAI) -> dict:
    """
    Nó Crítico: usa Gemini para avaliar qualidade do resultado.
    """
    pergunta = estado.get("pergunta_usuario", "")
    sql = estado.get("sql_gerada", "")
    schema = estado.get("contexto_schema", "")
    preview = estado.get("linhas_resultado_preview", [])
    total = estado.get("total_linhas_resultado", 0)
    saida = estado.get("saida_terminal", "")
    conversa_previa = estado.get("historico_conversa", "")
    erro = estado.get("erro_execucao", "")
    status_exec = estado.get("status", "")
    historico = estado.get("historico_tentativas", [])

    print("[CRITICO] Avaliando resultado...")

    # Se houve erro de execução, reprova direto sem gastar API
    if status_exec == "exec_erro" or erro:
        feedback = f"SQL falhou na execução: {erro}"
        print(f"[CRITICO] Reprovado por erro de execução: {erro[:80]}")
        return {
            "feedback_critico": feedback,
            "status": "reprovado",
            # Registrar tentativa com erro no histórico
            "historico_tentativas": [{"sql": sql, "erro": erro, "feedback": feedback}],
        }

    # Formata preview para o prompt
    preview_str = str(preview[:10]) if preview else "Nenhum resultado"
    historico_section = _formatar_historico_para_critico(historico)

    prompt = PROMPT_CRITIC.format(
        pergunta=pergunta,
        schema=schema,
        sql=sql,
        status_exec=status_exec,
        conversa_previa=conversa_previa if conversa_previa else "Nenhuma",
        total_linhas=total,
        preview=preview_str,
        erro=erro if erro else "Nenhum",
        historico_tentativas_section=historico_section,
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
        # Registrar esta tentativa no histórico (acumula via operator.add)
        "historico_tentativas": [{"sql": sql, "feedback": feedback}],
    }

