"""
Testes de integração do Text-to-Insight (usa API real + banco real).

Estes testes validam o pipeline completo: pergunta → planner → schema →
code agent → executor → critic → resposta.
"""

import os
import sys
import time
from pathlib import Path

import pytest
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "olist_relational.db")

load_dotenv()

@pytest.fixture
def grafo(llm_profile):
    """Retorna o grafo compilado com o provider de teste configurado no ambiente."""
    from text_to_insight.graph import Graph

    return Graph(llm_profile.api_key, llm_profile.model_name, hitl=True)


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
        _estado_inicial("Quais são as 5 categorias com a maior quantidade total de itens vendidos?"), config
    ) # antes a pergunta era "Quais são as 5 categorias de produtos mais vendidos por quantidade?" e ela era uma pergunta que necessitava de mais input de contexto para o gpt4o

    assert resultado["status"] == "aprovado"
    assert resultado["sql_gerada"] != ""
    assert len(resultado.get("linhas_resultado_preview", [])) > 0


@pytest.mark.vcr
@pytest.mark.timeout(120)
def test_estado_final_completo(grafo):
    """Estado final tem todos os campos-chave preenchidos."""
    config = {"configurable": {"thread_id": "teste_estado"}}
    resultado = grafo.grafo_text_to_insight.invoke(
        _estado_inicial("Considerando o valor total cobrado por pedido, qual é a média de valor dos pedidos?"), config
    ) 
    # antes era "Qual o valor médio dos pedidos?", mas o gpt4o não conseguia entender o contexto de "valor dos pedidos" sem mencionar o campo específico "valor total cobrado por pedido"

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


# ============================================================
# INTEGRAÇÃO — fluxo de gráficos (sem LLM real)
# ============================================================

"""Testes de integração focados no fluxo de geração de gráficos, usando mocks para o LLM e nós do grafo."""

def _montar_grafo_fake(monkeypatch, tmp_path, enable_graphs: bool):
    import text_to_insight.graph as graph_module
    import text_to_insight.model_selection as model_selection
    from text_to_insight.nodes import csv_saver as csv_module

    def _fake_get_model(model, api_key):
        return object()

    monkeypatch.setattr(model_selection, "get_model", _fake_get_model)
    monkeypatch.setattr(graph_module, "get_model", _fake_get_model)

    monkeypatch.setattr(csv_module, "RESULTS_DIR", tmp_path / "results")

    def _fake_planejador(estado, llm=None, hitl=True):
        return {"status": "pronto_codificacao"}

    def _fake_agente_codigo(estado, llm=None):
        return {"sql_gerada": "SELECT 1", "status": "sql_gerada", "tentativas_loop": 1}

    def _fake_sandbox(estado):
        return {
            "status": "exec_ok",
            "linhas_resultado_preview": [{"valor": 1}, {"valor": 2}],
            "linhas_resultado_completo": [{"valor": 1}, {"valor": 2}],
            "total_linhas_resultado": 2,
            "saida_terminal": "ok",
        }

    def _fake_critico(estado, llm=None):
        return {"status": "aprovado", "feedback_critico": "Aprovado"}

    def _fake_resposta(estado, llm=None):
        return {"resposta_natural": "ok"}

    def _fake_gerador_grafico(estado, llm=None):
        graphs_dir = tmp_path / "graphs"
        graphs_dir.mkdir(exist_ok=True)
        output_path = graphs_dir / "grafico_teste.png"
        output_path.write_bytes(b"fakepng")
        return {"grafico_gerado": True, "caminho_grafico": str(output_path)}

    def _fake_roteador_planejador(estado):
        return "agente_codigo"

    def _fake_roteador_sandbox(estado):
        return "critico"

    def _fake_roteador_grafico(estado, llm=None):
        return "gerador_grafico"

    monkeypatch.setattr(graph_module, "nos_nodo_planejador", _fake_planejador)
    monkeypatch.setattr(graph_module, "nos_nodo_agente_codigo", _fake_agente_codigo)
    monkeypatch.setattr(graph_module, "nos_nodo_sandbox", _fake_sandbox)
    monkeypatch.setattr(graph_module, "nos_nodo_critico", _fake_critico)
    monkeypatch.setattr(graph_module, "nos_nodo_resposta", _fake_resposta)
    monkeypatch.setattr(graph_module, "nos_nodo_gerador_grafico", _fake_gerador_grafico)
    monkeypatch.setattr(graph_module, "nos_nodo_salvar_csv", csv_module.nos_nodo_salvar_csv)
    monkeypatch.setattr(graph_module, "roteador_planejador", _fake_roteador_planejador)
    monkeypatch.setattr(graph_module, "roteador_sandbox", _fake_roteador_sandbox)
    monkeypatch.setattr(graph_module, "roteador_grafico", _fake_roteador_grafico)

    return graph_module.Graph(api_key="fake", model="fake", hitl=False, enable_graphs=enable_graphs)


def test_grafo_com_graficos_gera_csv_e_png(monkeypatch, tmp_path):
    grafo = _montar_grafo_fake(monkeypatch, tmp_path, enable_graphs=True)
    config = {"configurable": {"thread_id": "grafo_graficos_true"}}

    estado = {
        "pergunta_original": "Teste",
        "pergunta_atual": "Teste",
        "db_path": "fake.db",
    }
    resultado = grafo.grafo_text_to_insight.invoke(estado, config)

    csv_path = resultado.get("caminho_csv_resultado", "")
    assert csv_path
    assert Path(csv_path).exists()

    assert resultado.get("grafico_gerado") is True
    grafico_path = resultado.get("caminho_grafico", "")
    assert grafico_path
    assert Path(grafico_path).exists()
    assert Path(grafico_path).stat().st_size > 0


def test_grafo_sem_graficos_bypassa(monkeypatch, tmp_path):
    grafo = _montar_grafo_fake(monkeypatch, tmp_path, enable_graphs=False)
    config = {"configurable": {"thread_id": "grafo_graficos_false"}}

    estado = {
        "pergunta_original": "Teste",
        "pergunta_atual": "Teste",
        "db_path": "fake.db",
    }
    resultado = grafo.grafo_text_to_insight.invoke(estado, config)

    assert resultado.get("caminho_csv_resultado", "") == ""
    assert resultado.get("grafico_gerado", False) is False
    assert resultado.get("caminho_grafico", "") == ""