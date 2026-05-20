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

    from text_to_insight.graph import Graph
    return Graph(api_key, "gemini-2.5-flash", hitl=True)


@pytest.fixture(autouse=True)
def rate_limit_delay():
    """Espera entre testes para respeitar rate limit da API."""
    yield
    time.sleep(15)


def _estado_inicial(pergunta: str) -> dict:
    return {
        "pergunta_original": pergunta,
        "pergunta_atual": pergunta,
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


def test_grafo_compila_com_retriever(grafo):
    """Sanidade: grafo compila e contém o nó retriever."""
    compilado = grafo.app()
    assert "retriever" in compilado.get_graph().nodes


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


@pytest.mark.vcr
@pytest.mark.timeout(180)
def test_hitl_nova_pergunta_substitui(grafo):
    """HITL com nova pergunta deve substituir pergunta_atual sem mudar a original."""
    from text_to_insight.runtime import registrar_resposta_humana

    config = {"configurable": {"thread_id": "teste_hitl_nova_pergunta"}}
    grafo.grafo_text_to_insight.invoke(_estado_inicial("Quem e o Brad Pitt?"), config)

    snapshot = grafo.grafo_text_to_insight.get_state(config)
    assert snapshot.values.get("espera_humana") is True or snapshot.values.get("status") == "aguardando_input"

    registrar_resposta_humana(
        grafo_app=grafo.grafo_text_to_insight,
        config=config,
        user_response="Quero saber quantos clientes existem",
    )

    for _ in grafo.grafo_text_to_insight.stream(None, config, stream_mode="values"):
        pass

    resultado_final = grafo.grafo_text_to_insight.get_state(config).values

    assert resultado_final.get("pergunta_original") == "Quem e o Brad Pitt?"
    assert resultado_final.get("pergunta_atual") == "Quero saber quantos clientes existem"
    assert "Brad Pitt" not in str(resultado_final.get("resposta_natural", ""))