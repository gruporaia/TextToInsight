"""
Testes individuais de cada nó com API real + banco real (Layer 2).

Cada teste exercita UM ÚNICO nó isoladamente, com estado construído manualmente.
Se um teste falha, você sabe exatamente qual nó quebrou.

Usa API real do Gemini — requer GOOGLE_API_KEY e quota disponível.
Executa: pytest tests/test_nodes.py -v -s
"""

import os
import sys
import time
import pytest
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

import pytest

load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "olist_relational.db")

@pytest.fixture
def llm():
    """Retorna uma instância real do Gemini para os testes dos nós.""" #como estamos usando vcr, não haverá mais requisição direta, apenas repetição
                                                                       #do primeiro resultado da requisição, é possível verificar isso em test/cassettes
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        pytest.skip("GOOGLE_API_KEY não encontrada no .env. Pulando teste.")
    
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=api_key
    )

def _obter_schema_real() -> str:
    """Helper: extrai schema real do olist DB (sem API, só SQLite)."""
    from src.nodes.schema import nos_nodo_esquema
    resultado = nos_nodo_esquema({"db_path": DB_PATH, "pergunta_usuario": "teste"})
    return resultado["contexto_schema"]


# ============================================================
# PLANNER — caminho determinístico (sem API)
# ============================================================

def test_planner_sem_schema(llm):
    """Planner sem schema → aguardando_schema (determinístico, sem API)."""
    from src.nodes.planner import nos_nodo_planejador

    estado = {
        "pergunta_usuario": "Quantos pedidos existem?",
        "contexto_schema": "",
        "feedback_critico": "",
        "status": "iniciado",
        "tentativas_loop": 0,
        "erro_execucao": "",
    }
    resultado = nos_nodo_planejador(estado, llm)
    assert resultado["status"] == "aguardando_schema"


# ============================================================
# PLANNER — com API
# ============================================================

@pytest.mark.vcr
@pytest.mark.timeout(60)
def test_planner_com_schema_decide_codificar(llm):
    """Planner com schema e sem feedback → deve decidir gerar código."""
    from src.nodes.planner import nos_nodo_planejador

    schema = _obter_schema_real()
    estado = {
        "pergunta_usuario": "Quantos pedidos existem no banco?",
        "contexto_schema": schema,
        "feedback_critico": "",
        "status": "schema_obtido",
        "tentativas_loop": 0,
        "erro_execucao": "",
    }
    resultado = nos_nodo_planejador(estado, llm)

    assert resultado["status"] in ("pronto_codificacao", "revisando_estrategia")
    print(f"  → Planner decidiu: {resultado['status']}")


@pytest.mark.vcr
@pytest.mark.timeout(60)
def test_planner_com_feedback_revisa(llm):
    """Planner com feedback do crítico → deve revisar estratégia."""
    from src.nodes.planner import nos_nodo_planejador

    time.sleep(5)  # rate limit
    schema = _obter_schema_real()
    estado = {
        "pergunta_usuario": "Quantos pedidos existem no banco?",
        "contexto_schema": schema,
        "feedback_critico": "A SQL retornou dados incorretos, faltou filtrar por status.",
        "status": "reprovado",
        "tentativas_loop": 1,
        "erro_execucao": "",
    }
    resultado = nos_nodo_planejador(estado, llm)

    assert resultado["status"] in ("pronto_codificacao", "revisando_estrategia")
    print(f"  → Planner decidiu: {resultado['status']}")


# ============================================================
# CODE AGENT — com API
# ============================================================

@pytest.mark.vcr
@pytest.mark.timeout(60)
def test_code_agent_gera_sql(llm):
    """Code Agent recebe pergunta + schema → retorna SQL válida."""
    from src.nodes.code_agent.code_agent import nos_nodo_agente_codigo

    time.sleep(5)  # rate limit
    schema = _obter_schema_real()
    estado = {
        "pergunta_usuario": "Quantos pedidos existem no banco?",
        "contexto_schema": schema,
        "feedback_critico": "",
        "sql_gerada": "",
        "tentativas_loop": 0,
    }
    resultado = nos_nodo_agente_codigo(estado, llm)

    assert resultado["sql_gerada"] != ""
    assert resultado["status"] == "sql_gerada"
    assert resultado["tentativas_loop"] == 1

    sql = resultado["sql_gerada"].strip().upper()
    assert sql.startswith("SELECT") or sql.startswith("WITH"), \
        f"SQL deveria começar com SELECT/WITH, mas começa com: {sql[:30]}"

    print(f"  → SQL gerada: {resultado['sql_gerada']}")


@pytest.mark.vcr
@pytest.mark.timeout(60)
def test_code_agent_com_feedback_regenera(llm):
    """Code Agent com feedback do crítico → gera SQL diferente."""
    from src.nodes.code_agent.code_agent import nos_nodo_agente_codigo

    time.sleep(5)  # rate limit
    schema = _obter_schema_real()
    estado = {
        "pergunta_usuario": "Quais as 5 categorias de produtos mais vendidas?",
        "contexto_schema": schema,
        "feedback_critico": "A SQL anterior não tinha LIMIT 5, corrija.",
        "sql_gerada": "SELECT product_category_name FROM products",
        "tentativas_loop": 1,
    }
    resultado = nos_nodo_agente_codigo(estado, llm)

    assert resultado["sql_gerada"] != ""
    assert resultado["tentativas_loop"] == 2
    print(f"  → SQL regenerada: {resultado['sql_gerada']}")


# ============================================================
# EXECUTOR — com DB real (sem API)
# ============================================================

def test_executor_com_sql_real():
    """Executor executa SQL gerada manualmente contra olist DB."""
    from src.nodes.sandbox import nos_nodo_sandbox

    estado = {
        "sql_gerada": "SELECT COUNT(*) as total_pedidos FROM orders",
        "db_path": DB_PATH,
        "pergunta_usuario": "Quantos pedidos existem?",
    }
    resultado = nos_nodo_sandbox(estado)

    assert resultado["status"] == "exec_ok"
    assert resultado["total_linhas_resultado"] >= 1
    assert resultado["linhas_resultado_preview"][0]["total_pedidos"] > 0
    print(f"  → Resultado: {resultado['linhas_resultado_preview']}")


# ============================================================
# CRITIC — com API
# ============================================================

@pytest.mark.vcr
@pytest.mark.timeout(60)
def test_critic_avalia_resultado_correto(llm):
    """Critic recebe pergunta + SQL + resultado OK → avalia com LLM."""
    from src.nodes.critic import nos_nodo_critico

    time.sleep(5)  # rate limit
    estado = {
        "pergunta_usuario": "Quantos pedidos existem no banco?",
        "sql_gerada": "SELECT COUNT(*) as total_pedidos FROM orders",
        "linhas_resultado_preview": [{"total_pedidos": 99441}],
        "total_linhas_resultado": 1,
        "saida_terminal": "[EXECUTOR] Execucao OK | linhas_total=1 | preview=1",
        "erro_execucao": "",
        "status": "exec_ok",
    }
    resultado = nos_nodo_critico(estado, llm)

    assert resultado["status"] in ("aprovado", "reprovado")
    assert resultado["feedback_critico"] != ""
    print(f"  → Veredito: {resultado['status']}")
    print(f"  → Feedback: {resultado['feedback_critico'][:100]}")


def test_critic_reprova_erro_execucao(llm):
    """Critic com erro de execução → reprova sem chamar API (determinístico)."""
    from src.nodes.critic import nos_nodo_critico

    estado = {
        "pergunta_usuario": "Quantos pedidos existem?",
        "sql_gerada": "SELECT * FROM tabela_inexistente",
        "linhas_resultado_preview": [],
        "total_linhas_resultado": 0,
        "saida_terminal": "[EXECUTOR] Erro",
        "erro_execucao": "no such table: tabela_inexistente",
        "status": "exec_erro",
    }
    resultado = nos_nodo_critico(estado, llm)

    assert resultado["status"] == "reprovado"
    assert "tabela_inexistente" in resultado["feedback_critico"]
    print(f"  → Reprovado corretamente: {resultado['feedback_critico'][:80]}")


# ============================================================
# CADEIA: Code Agent → Executor (2 nós encadeados, com API)
# ============================================================

@pytest.mark.vcr
@pytest.mark.timeout(90)
def test_cadeia_code_agent_executor(llm):
    """Code Agent gera SQL, Executor executa — testa a conexão entre os dois."""
    from src.nodes.code_agent.code_agent import nos_nodo_agente_codigo
    from src.nodes.sandbox import nos_nodo_sandbox

    time.sleep(5)  # rate limit
    schema = _obter_schema_real()

    # Passo 1: Code Agent gera SQL
    estado_code = {
        "pergunta_usuario": "Quantos clientes existem no banco?",
        "contexto_schema": schema,
        "feedback_critico": "",
        "sql_gerada": "",
        "tentativas_loop": 0,
    }
    resultado_code = nos_nodo_agente_codigo(estado_code, llm)
    print(f"  → SQL gerada: {resultado_code['sql_gerada']}")

    assert resultado_code["sql_gerada"] != ""

    # Passo 2: Executor executa a SQL gerada
    estado_exec = {
        "sql_gerada": resultado_code["sql_gerada"],
        "db_path": DB_PATH,
        "pergunta_usuario": "Quantos clientes existem no banco?",
    }
    resultado_exec = nos_nodo_sandbox(estado_exec)
    print(f"  → Status execução: {resultado_exec['status']}")
    print(f"  → Preview: {resultado_exec.get('linhas_resultado_preview', [])}")

    assert resultado_exec["status"] == "exec_ok"
    assert resultado_exec["total_linhas_resultado"] >= 1
