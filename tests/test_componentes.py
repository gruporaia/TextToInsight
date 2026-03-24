"""
Testes de componentes do Text-to-Insight (sem chamadas de API).

Valida as partes determinísticas: schema, SQL validation/execution,
executor node e routers.
"""

import os
import sys

# Garante que o diretório raiz do projeto está no path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "olist_relational.db")


# ============================================================
# Schema Node
# ============================================================

def test_schema_extrai_tabelas():
    """Schema node retorna contexto com tabelas do olist DB."""
    from src.nodes.schema import nos_nodo_esquema

    estado = {"db_path": DB_PATH, "pergunta_usuario": "teste"}
    resultado = nos_nodo_esquema(estado)

    assert resultado["status"] == "schema_obtido"
    assert "Tabela:" in resultado["contexto_schema"]
    assert len(resultado["contexto_schema"]) > 100


def test_schema_erro_db_invalido():
    """Schema node retorna erro quando db_path não existe."""
    from src.nodes.schema import nos_nodo_esquema

    estado = {"db_path": "/caminho/inexistente.db", "pergunta_usuario": "teste"}
    resultado = nos_nodo_esquema(estado)

    assert resultado["status"] == "exec_erro"
    assert resultado["contexto_schema"] == ""


# ============================================================
# SQL Validation (code_sql.py)
# ============================================================

def test_sql_valida_select():
    """Aceita SELECT válido."""
    from src.nodes.code_agent.code_sql import validar_sql_segura

    ok, msg = validar_sql_segura("SELECT COUNT(*) FROM orders")
    assert ok is True
    assert msg == ""


def test_sql_valida_cte():
    """Aceita WITH/CTE."""
    from src.nodes.code_agent.code_sql import validar_sql_segura

    ok, msg = validar_sql_segura("WITH totals AS (SELECT id FROM orders) SELECT * FROM totals")
    assert ok is True


def test_sql_rejeita_insert():
    """Rejeita INSERT."""
    from src.nodes.code_agent.code_sql import validar_sql_segura

    ok, msg = validar_sql_segura("INSERT INTO orders VALUES (1, 2, 3)")
    assert ok is False


def test_sql_rejeita_drop():
    """Rejeita DROP."""
    from src.nodes.code_agent.code_sql import validar_sql_segura

    ok, msg = validar_sql_segura("DROP TABLE orders")
    assert ok is False


def test_sql_rejeita_multiplas():
    """Rejeita múltiplas statements."""
    from src.nodes.code_agent.code_sql import validar_sql_segura

    ok, msg = validar_sql_segura("SELECT 1; SELECT 2")
    assert ok is False


def test_sql_rejeita_vazia():
    """Rejeita SQL vazia."""
    from src.nodes.code_agent.code_sql import validar_sql_segura

    ok, msg = validar_sql_segura("")
    assert ok is False


# ============================================================
# SQL Execution (code_sql.py)
# ============================================================

def test_execucao_sql_valida():
    """Executa SELECT real no olist DB e retorna resultados."""
    from src.nodes.code_agent.code_sql import executar_sql_sqlite

    resultado = executar_sql_sqlite(DB_PATH, "SELECT COUNT(*) as total FROM orders")
    assert resultado["ok"] is True
    assert resultado["total_linhas_resultado"] == 1
    assert resultado["linhas_resultado_preview"][0]["total"] > 0


def test_execucao_sql_com_limite():
    """Respeita limite_preview."""
    from src.nodes.code_agent.code_sql import executar_sql_sqlite

    resultado = executar_sql_sqlite(DB_PATH, "SELECT * FROM orders", limite_preview=5)
    assert resultado["ok"] is True
    assert len(resultado["linhas_resultado_preview"]) <= 5


def test_execucao_sql_invalida():
    """Retorna erro para SQL com sintaxe inválida."""
    from src.nodes.code_agent.code_sql import executar_sql_sqlite

    resultado = executar_sql_sqlite(DB_PATH, "SELECT FROM")
    assert resultado["ok"] is False
    assert resultado["erro_execucao"] != ""


def test_execucao_db_inexistente():
    """Retorna erro para DB inexistente."""
    from src.nodes.code_agent.code_sql import executar_sql_sqlite

    resultado = executar_sql_sqlite("/nao/existe.db", "SELECT 1")
    assert resultado["ok"] is False


# ============================================================
# Executor Node (sandbox.py refatorado)
# ============================================================

def test_executor_sucesso():
    """Executor executa SQL do estado e retorna resultado."""
    from src.nodes.sandbox import nos_nodo_sandbox

    estado = {
        "sql_gerada": "SELECT COUNT(*) as total FROM orders",
        "db_path": DB_PATH,
        "pergunta_usuario": "teste",
    }
    resultado = nos_nodo_sandbox(estado)

    assert resultado["status"] == "exec_ok"
    assert resultado["total_linhas_resultado"] == 1
    assert len(resultado["linhas_resultado_preview"]) > 0


def test_executor_sql_vazia():
    """Executor retorna erro quando sql_gerada está vazia."""
    from src.nodes.sandbox import nos_nodo_sandbox

    estado = {
        "sql_gerada": "",
        "db_path": DB_PATH,
        "pergunta_usuario": "teste",
    }
    resultado = nos_nodo_sandbox(estado)

    assert resultado["status"] == "exec_erro"


def test_executor_sql_com_erro():
    """Executor retorna exec_erro para SQL com problema."""
    from src.nodes.sandbox import nos_nodo_sandbox

    estado = {
        "sql_gerada": "SELECT * FROM tabela_que_nao_existe",
        "db_path": DB_PATH,
        "pergunta_usuario": "teste",
    }
    resultado = nos_nodo_sandbox(estado)

    assert resultado["status"] == "exec_erro"
    assert resultado["erro_execucao"] != ""


# ============================================================
# Routers
# ============================================================

def test_roteador_sandbox_exec_ok():
    """Roteador sandbox direciona para critico quando exec_ok."""
    from src.routers.edges import roteador_sandbox

    estado = {"status": "exec_ok", "tentativas_loop": 1}
    assert roteador_sandbox(estado) == "critico"


def test_roteador_sandbox_exec_erro():
    """Roteador sandbox direciona para planejador quando exec_erro."""
    from src.routers.edges import roteador_sandbox

    estado = {"status": "exec_erro", "tentativas_loop": 1}
    assert roteador_sandbox(estado) == "planejador"


def test_roteador_sandbox_muitas_tentativas():
    """Roteador sandbox direciona para planejador com muitas tentativas."""
    from src.routers.edges import roteador_sandbox

    estado = {"status": "exec_erro", "tentativas_loop": 5}
    assert roteador_sandbox(estado) == "planejador"


def test_roteador_planejador_sem_schema():
    """Roteador planejador direciona para esquema quando schema vazio."""
    from src.routers.edges import roteador_planejador

    estado = {"contexto_schema": "", "status": "iniciado"}
    assert roteador_planejador(estado) == "esquema"


def test_roteador_planejador_pronto():
    """Roteador planejador direciona para agente_codigo quando pronto."""
    from src.routers.edges import roteador_planejador

    estado = {"contexto_schema": "tabelas...", "status": "pronto_codificacao"}
    assert roteador_planejador(estado) == "agente_codigo"


def test_roteador_planejador_aprovado():
    """Roteador planejador direciona para fim quando aprovado."""
    from src.routers.edges import roteador_planejador

    estado = {"contexto_schema": "tabelas...", "status": "aprovado"}
    assert roteador_planejador(estado) == "fim"
