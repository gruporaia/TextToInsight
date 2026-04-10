"""
Testes de integração do Text-to-Insight (usa API real do Gemini + banco real).

Estes testes validam o pipeline completo: pergunta → planner → schema →
code agent → executor → critic → resposta.
"""

import os
import sys
import time

import pytest
from dotenv import load_dotenv

from src.nodes.schema import _formatar_schema_sqlite

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "olist_relational.db")

load_dotenv()

@pytest.fixture
def grafo():
    """Retorna o grafo compilado."""
    api_key = os.getenv("GOOGLE_API_KEY") #como estamos usando vcr, não haverá mais requisição direta, apenas repetição
                                          #do primeiro resultado da requisição, é possível verificar isso em test/cassettes
    if not api_key:
        pytest.skip("Variável GOOGLE_API_KEY não encontrada. Pulando testes de integração.")

    from src.graph import Graph
    return Graph(api_key)


@pytest.fixture(autouse=True)
def rate_limit_delay():
    """Espera entre testes para respeitar rate limit da API."""
    yield
    time.sleep(15)


def _estado_inicial(pergunta: str) -> dict:
    return {
        "pergunta_usuario": pergunta,
        "historico_conversa": [],
        "contexto_schema": "",
        "sql_gerada": "",
        "saida_terminal": "",
        "feedback_critico": "",
        "erro_execucao": "",
        "status": "iniciado",
        "tentativas_loop": 0,
        "db_path": DB_PATH,
    }


def test_grafo_compila(grafo):
    """Grafo compila sem erros."""
    assert grafo is not None


@pytest.mark.vcr
@pytest.mark.timeout(120)
def test_pergunta_simples(grafo):
    """Pergunta simples percorre o grafo e chega ao status aprovado."""
    config = {"configurable": {"thread_id": "teste_simples"}}
    resultado = grafo.grafo_text_to_insight.invoke(_estado_inicial("Quantos pedidos existem no banco?"), config)

    assert resultado["status"] == "aprovado"
    assert resultado["sql_gerada"] != ""
    assert resultado["contexto_schema"] != ""
    assert resultado["total_linhas_resultado"] >= 1


@pytest.mark.vcr
@pytest.mark.timeout(120)
def test_pergunta_com_ranking(grafo):
    """Pergunta com ranking retorna múltiplas linhas."""
    config = {"configurable": {"thread_id": "teste_simples"}}
    resultado = grafo.grafo_text_to_insight.invoke(
        _estado_inicial("Quais sao as 5 categorias de produtos mais vendidos por quantidade?"), config
    )

    assert resultado["status"] == "aprovado"
    assert resultado["sql_gerada"] != ""
    assert len(resultado.get("linhas_resultado_preview", [])) > 0


@pytest.mark.vcr
@pytest.mark.timeout(120)
def test_estado_final_completo(grafo):
    """Estado final tem todos os campos-chave preenchidos."""
    config = {"configurable": {"thread_id": "teste_estado"}}
    resultado = grafo.grafo_text_to_insight.invoke(
        _estado_inicial("Qual o valor medio dos pedidos?"), config
    )

    # Campos que devem estar preenchidos ao final
    assert resultado.get("contexto_schema", "") != ""
    assert resultado.get("sql_gerada", "") != ""
    assert resultado.get("saida_terminal", "") != ""
    assert resultado.get("tentativas_loop", 0) >= 1