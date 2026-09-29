"""
Nó Agente de Código do grafo de agentes Text-to-Insight.

Responsabilidade única: gerar SQL executável a partir da pergunta do usuário,
do contexto do schema e de feedback anterior (se houver), usando Gemini.
"""

import re
from langchain_google_genai import ChatGoogleGenerativeAI

from ...state import EstadoTextToInsight
from ...utils import extrair_tokens

# Nome de exibição e observações de sintaxe por dialeto, injetados no prompt do
# LLM para que a SQL gerada use a sintaxe correta do banco alvo (não apenas SQLite).
_NOME_DIALETO = {
    "sqlite": "SQLite",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
}

_NOTA_DIALETO = {
    "sqlite": (
        "Sintaxe SQLite: use || para concatenar strings, strftime() para "
        "datas/horas, e LIMIT/OFFSET para paginação. Identificadores não "
        "precisam de aspas."
    ),
    "postgresql": (
        "Sintaxe PostgreSQL: use || para concatenar strings, EXTRACT()/"
        "TO_CHAR() para datas/horas, e LIMIT/OFFSET para paginação. "
        "Identificadores com maiúsculas ou caracteres especiais exigem "
        "aspas duplas (ex: \"NomeColuna\"); NÃO use crases (`)."
    ),
    "mysql": (
        "Sintaxe MySQL: use CONCAT() para concatenar strings (NÃO use ||), "
        "DATE_FORMAT()/EXTRACT() para datas/horas, e LIMIT/OFFSET para "
        "paginação. Identificadores podem usar crases (`nome_coluna`); "
        "NÃO use aspas duplas para identificadores."
    ),
}


def _resolver_info_dialeto(estado: EstadoTextToInsight, engine=None) -> tuple[str, str]:
    """Resolve (nome_exibicao, nota_sintaxe) do dialeto configurado no estado.

    Ordem de resolução:
    1. `engine.dialect.name`, se uma Engine SQLAlchemy foi injetada pelo
       chamador (caso mais confiável: vem direto da conexão, sem adivinhar).
    2. `db_dialeto`, se informado explicitamente no estado.
    3. `db_url`, cujo dialeto é lido do prefixo da URL (sem conectar).
    4. 'sqlite' como default, preservando o comportamento anterior quando
       nada é informado (compatibilidade retroativa).
    """
    if engine is not None:
        dialeto = engine.dialect.name
        nome = _NOME_DIALETO.get(dialeto, _NOME_DIALETO["sqlite"])
        nota = _NOTA_DIALETO.get(dialeto, _NOTA_DIALETO["sqlite"])
        return nome, nota

    dialeto = (estado.get("db_dialeto") or "").strip().lower()

    if not dialeto:
        db_url = (estado.get("db_url") or "").strip()
        if db_url:
            try:
                from sqlalchemy.engine import make_url

                dialeto = make_url(db_url).get_backend_name()
            except Exception:
                dialeto = ""

    dialeto = dialeto or "sqlite"
    nome = _NOME_DIALETO.get(dialeto, _NOME_DIALETO["sqlite"])
    nota = _NOTA_DIALETO.get(dialeto, _NOTA_DIALETO["sqlite"])
    return nome, nota


PROMPT_TEMPLATE_COT = """Você é um especialista em SQL para bancos {dialeto}.

Sua tarefa: gerar UMA única consulta SQL SELECT que responda à pergunta do usuário,
usando o schema do banco de dados fornecido abaixo.

Regras:
- Você DEVE primeiro pensar passo a passo sobre como resolver a pergunta. Escreva o seu raciocínio dentro das tags <thought> e </thought>.
- Gere APENAS uma consulta SELECT (ou WITH/CTE seguido de SELECT) após o raciocínio.
- NÃO use INSERT, UPDATE, DELETE, DROP, ALTER ou qualquer comando de escrita.
- Use nomes de tabelas e colunas EXATAMENTE como aparecem no schema.
- Se a pergunta for ambígua, faça a interpretação mais razoável.
- Use as estatísticas de dados (DATA EXPLORATION) para entender distribuições, formatos e valores reais das colunas.
- {nota_dialeto}

=== SCHEMA DO BANCO ===
{schema}

=== DATA EXPLORATION (estatísticas amostrais) ===
{data_exploration}

=== PERGUNTA DO USUÁRIO ===
{pergunta}

=== CONVERSA PRÉVIA (CONTEXTO ADICIONAL) ===
{conversa_previa}

=== HISTÓRICO DE TENTATIVAS ANTERIORES ===
{historico_tentativas_section}

Sua resposta DEVE ter exatamente este formato:
<thought>
Seu raciocínio lógico detalhado aqui.
</thought>
```sql
Sua consulta SQL aqui
```"""

PROMPT_TEMPLATE_NO_COT = """Você é um especialista em SQL para bancos {dialeto}.

Sua tarefa: gerar UMA única consulta SQL SELECT que responda à pergunta do usuário,
usando o schema do banco de dados fornecido abaixo.

Regras:
- Gere APENAS uma consulta SELECT (ou WITH/CTE seguido de SELECT).
- NÃO use INSERT, UPDATE, DELETE, DROP, ALTER ou qualquer comando de escrita.
- NÃO inclua explicações, apenas a SQL pura.
- Use nomes de tabelas e colunas EXATAMENTE como aparecem no schema.
- Se a pergunta for ambígua, faça a interpretação mais razoável.
- Use as estatísticas de dados (DATA EXPLORATION) para entender distribuições, formatos e valores reais das colunas e raciocinar sobre a natureza do D.
- {nota_dialeto}

=== SCHEMA DO BANCO ===
{schema}

=== DATA EXPLORATION (estatísticas amostrais) ===
{data_exploration}

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
        partes.append(bloco)

    return "\n".join(partes) + "\nNÃO repita os mesmos erros. Gere uma SQL diferente e corrigida."


def nos_nodo_agente_codigo(estado: EstadoTextToInsight, llm: ChatGoogleGenerativeAI, use_cot: bool = True, engine=None) -> dict:
    """
    Nó Agente de Código: usa Gemini para gerar SQL a partir da pergunta + schema.

    `engine`, se informado, é a Engine SQLAlchemy injetada pelo chamador —
    usada apenas para resolver o dialeto (via `engine.dialect.name`) e ajustar
    a sintaxe do prompt, sem depender de `db_dialeto`/`db_url` no estado.
    """
    pergunta = (
        estado.get("pergunta_atual", "")
        or estado.get("pergunta_original", "")
        or estado.get("pergunta_usuario", "")
    )
    conversa_previa = estado.get("historico_conversa", "")
    schema = estado.get("contexto_schema", "")
    schema_rag = estado.get("contexto_rag_schema", "")
    data_exploration = estado.get("contexto_data_exploration", "")
    historico = estado.get("historico_tentativas", [])
    tentativas = estado.get("tentativas_loop", 0)

    print(f"[AGENTE_CODIGO] Gerando SQL (tentativa {tentativas + 1})...")

    historico_section = _formatar_historico_tentativas(historico)
    dialeto_nome, nota_dialeto = _resolver_info_dialeto(estado, engine=engine)

    template = PROMPT_TEMPLATE_COT if use_cot else PROMPT_TEMPLATE_NO_COT

    prompt = template.format(
        dialeto=dialeto_nome,
        nota_dialeto=nota_dialeto,
        schema=schema_rag if schema_rag else schema,
        data_exploration=data_exploration if data_exploration else "Não disponível.",
        pergunta=pergunta,
        conversa_previa=conversa_previa if conversa_previa else "Nenhuma",
        historico_tentativas_section=historico_section,
    )

    # print("\n\n[AGENTE_CODIGO] Prompt: \n", prompt, "\n\n")
    
    resposta = llm.invoke(prompt)
    resposta_texto = resposta.content

    # Extrair raciocínio CoT (se disponível)
    raciocinio = ""
    if use_cot:
        thought_match = re.search(r"<thought>(.*?)</thought>", resposta_texto, re.DOTALL)
        if thought_match:
            raciocinio = thought_match.group(1).strip()
            print(f"[AGENTE_CODIGO] Raciocínio: {raciocinio[:200]}...")

    sql = _extrair_sql(resposta_texto)

    print(f"[AGENTE_CODIGO] SQL gerada: {sql[:100]}...")

    in_tokens, out_tokens, total_tokens = extrair_tokens(resposta)

    # Montar contexto compacto para diagnóstico (schema + data exploration usados)
    contexto_usado = schema_rag if schema_rag else schema
    contexto_prompt = f"{contexto_usado}\n\n{data_exploration}" if data_exploration else contexto_usado

    return {
        "sql_gerada": sql,
        "status": "sql_gerada",
        "tentativas_loop": tentativas + 1,
        "raciocinio_agente": raciocinio,
        "contexto_prompt_agente": contexto_prompt,
        "ultimo_prompt": prompt,
        # Retornando o número de tokens nessa chamada do Gemini
        "tokens_input": in_tokens,
        "tokens_output": out_tokens,
        "tokens_total": total_tokens,
    }

