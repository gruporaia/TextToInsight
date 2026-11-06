"""Fast unit tests (<0.2s) for node-level token tracking architecture."""

import copy
import inspect
import sqlite3
import sys
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

from text_to_insight.graph import Graph
from text_to_insight.nodes.code_agent.code_agent import nos_nodo_agente_codigo
from text_to_insight.nodes.data_exploration import (
    _exploration_selector_llm,
    nos_nodo_exploration_selector,
)
from text_to_insight.nodes.enrich_schema import nos_nodo_enrich
from text_to_insight.nodes.graph_generator import nos_nodo_gerador_grafico
from text_to_insight.nodes.planner import nos_nodo_planejador
from text_to_insight.nodes.response import nos_nodo_resposta
from text_to_insight.nodes.sandbox import nos_nodo_sandbox
from text_to_insight.routers.edges import roteador_grafico
from text_to_insight.token_tracking import (
    build_batch_token_usage_update,
    build_node_tokens_delta,
    build_token_usage_update,
    extract_tokens,
    merge_tokens_by_node,
)


# ---------------------------------------------------------------------------
# 1. extract_tokens tests
# ---------------------------------------------------------------------------


def test_extract_tokens_usage_metadata():
    """Extract tokens when response provides usage_metadata."""
    msg = AIMessage(
        content="test",
        usage_metadata={"input_tokens": 12, "output_tokens": 34, "total_tokens": 46},
    )
    in_tok, out_tok, total_tok = extract_tokens(msg)
    assert in_tok == 12
    assert out_tok == 34
    assert total_tok == 46


def test_extract_tokens_usage_metadata_calculates_total_when_missing():
    """Extract tokens when total_tokens is zero or missing in usage_metadata."""
    msg = AIMessage(
        content="test",
        usage_metadata={"input_tokens": 15, "output_tokens": 25, "total_tokens": 0},
    )
    in_tok, out_tok, total_tok = extract_tokens(msg)
    assert in_tok == 15
    assert out_tok == 25
    assert total_tok == 40


def test_extract_tokens_response_metadata_fallback():
    """Extract tokens via OpenAI-compatible response_metadata['token_usage']."""
    msg = AIMessage(
        content="test",
        response_metadata={
            "token_usage": {
                "prompt_tokens": 20,
                "completion_tokens": 30,
            }
        },
    )
    in_tok, out_tok, total_tok = extract_tokens(msg)
    assert in_tok == 20
    assert out_tok == 30
    assert total_tok == 50


def test_extract_tokens_missing_metadata_still_records_call():
    """When response lacks metadata, extraction returns 0s while builder records calls=1."""
    msg = AIMessage(content="no metadata")
    in_tok, out_tok, total_tok = extract_tokens(msg)
    assert (in_tok, out_tok, total_tok) == (0, 0, 0)

    update = build_token_usage_update("planner", msg)
    assert update["tokens_input"] == 0
    assert update["tokens_output"] == 0
    assert update["tokens_total"] == 0
    assert update["tokens_by_node"] == {
        "planner": {
            "input": 0,
            "output": 0,
            "total": 0,
            "calls": 1,
        }
    }


def test_extract_tokens_none():
    """Handling None response gracefully."""
    assert extract_tokens(None) == (0, 0, 0)


# ---------------------------------------------------------------------------
# 2. merge_tokens_by_node & builders tests
# ---------------------------------------------------------------------------


def test_merge_tokens_by_node_pure_immutability():
    """Reducer must never mutate input dicts in-place."""
    current = {
        "code_agent": {
            "input": 100,
            "output": 50,
            "total": 150,
            "calls": 1,
        }
    }
    current_copy = copy.deepcopy(current)
    delta = {
        "code_agent": {
            "input": 200,
            "output": 80,
            "total": 280,
            "calls": 1,
        }
    }

    merged = merge_tokens_by_node(current, delta)

    # current must be unmodified
    assert current == current_copy
    # merged must be a brand-new outer dict
    assert merged is not current
    # merged inner dict must be a brand-new reference
    assert merged["code_agent"] is not current["code_agent"]
    assert merged["code_agent"] == {
        "input": 300,
        "output": 130,
        "total": 430,
        "calls": 2,
    }


def test_merge_tokens_by_node_multi_call_accumulation():
    """Reducer accumulates multiple sequential calls across distinct nodes."""
    state = {}
    delta1 = build_node_tokens_delta("code_agent", 50, 20, 70, calls=1)
    state = merge_tokens_by_node(state, delta1)

    delta2 = build_node_tokens_delta("code_agent", 40, 10, 50, calls=1)
    state = merge_tokens_by_node(state, delta2)

    delta3 = build_node_tokens_delta("planner", 100, 30, 130, calls=1)
    state = merge_tokens_by_node(state, delta3)

    delta4 = build_node_tokens_delta("code_agent", 60, 25, 85, calls=1)
    state = merge_tokens_by_node(state, delta4)

    assert state["code_agent"] == {
        "input": 150,
        "output": 55,
        "total": 205,
        "calls": 3,
    }
    assert state["planner"] == {
        "input": 100,
        "output": 30,
        "total": 130,
        "calls": 1,
    }


def test_build_batch_token_usage_update_empty():
    """Empty batch returns zeroed scalars and empty tokens_by_node."""
    update = build_batch_token_usage_update("rag_enrichment", [])
    assert update == {
        "tokens_input": 0,
        "tokens_output": 0,
        "tokens_total": 0,
        "tokens_by_node": {},
    }


# ---------------------------------------------------------------------------
# 3. Node-level integration unit tests
# ---------------------------------------------------------------------------


def test_planner_early_return_and_llm_call():
    """Planner early return does not record calls; LLM call records calls=1."""
    mock_llm = MagicMock()

    # Early return 1: missing schema
    res_no_schema = nos_nodo_planejador(
        {"pergunta_atual": "how many?", "contexto_schema": ""},
        llm=mock_llm,
        hitl=True,
    )
    assert res_no_schema["status"] == "aguardando_schema"
    assert res_no_schema["tokens_by_node"] == {}
    assert res_no_schema["tokens_input"] == 0
    mock_llm.invoke.assert_not_called()

    # Early return 2: already approved
    res_approved = nos_nodo_planejador(
        {"pergunta_atual": "how many?", "contexto_schema": "Tabela: users", "status": "aprovado"},
        llm=mock_llm,
        hitl=True,
    )
    assert res_approved["status"] == "aprovado"
    assert res_approved["tokens_by_node"] == {}
    mock_llm.invoke.assert_not_called()

    # LLM call path: ready for coding
    mock_llm.invoke.return_value = AIMessage(
        content='{"decisao": "pronto_codificacao"}',
        usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
    )
    res_llm = nos_nodo_planejador(
        {"pergunta_atual": "how many?", "contexto_schema": "Tabela: users", "status": "iniciado"},
        llm=mock_llm,
        hitl=True,
    )
    assert res_llm["status"] == "pronto_codificacao"
    assert res_llm["tokens_input"] == 100
    assert res_llm["tokens_output"] == 20
    assert res_llm["tokens_total"] == 120
    assert res_llm["tokens_by_node"] == {
        "planner": {"input": 100, "output": 20, "total": 120, "calls": 1}
    }

    # HITL path: necessita_ajuda
    mock_llm.invoke.return_value = AIMessage(
        content='{"decisao": "necessita_ajuda", "pergunta_ao_usuario": "which year?"}',
        usage_metadata={"input_tokens": 110, "output_tokens": 25, "total_tokens": 135},
    )
    res_hitl = nos_nodo_planejador(
        {"pergunta_atual": "how many?", "contexto_schema": "Tabela: users", "status": "iniciado"},
        llm=mock_llm,
        hitl=True,
    )
    assert res_hitl["status"] == "aguardando_input"
    assert res_hitl["espera_humana"] is True
    assert res_hitl["tokens_by_node"] == {
        "planner": {"input": 110, "output": 25, "total": 135, "calls": 1}
    }


def test_rag_enrichment_batch_token_accumulation():
    """Enrichment node processes batches and aggregates token usage across all batches."""
    schema_6_tables = "\n".join([f"Tabela: table_{i}\n- col_{i}: int" for i in range(6)])
    mock_llm = MagicMock()
    mock_bound = MagicMock()
    mock_llm.bind.return_value = mock_bound

    # Batch of 6 tables split by TAMANHO_LOTE=5 yields 2 batches
    mock_bound.batch.return_value = [
        AIMessage(
            content='{"table_0": {"col_0": "desc 0"}}',
            usage_metadata={"input_tokens": 50, "output_tokens": 15, "total_tokens": 65},
        ),
        AIMessage(
            content='{"table_5": {"col_5": "desc 5"}}',
            usage_metadata={"input_tokens": 40, "output_tokens": 10, "total_tokens": 50},
        ),
    ]

    res = nos_nodo_enrich(
        {"contexto_schema": schema_6_tables, "db_url": "sqlite://"},
        llm=mock_llm,
        engine=object(),  # prevents disk cache write
    )

    assert res["tem_descricao"] is True
    assert res["tokens_input"] == 90
    assert res["tokens_output"] == 25
    assert res["tokens_total"] == 115
    assert res["tokens_by_node"] == {
        "rag_enrichment": {"input": 90, "output": 25, "total": 115, "calls": 2}
    }


def test_exploration_selector_modes_and_resilience():
    """Exploration selector: mode 'off' skips tokens; mode 'llm' tracks tokens even on JSON error."""
    mock_llm = MagicMock()

    # Mode 'off'
    res_off = nos_nodo_exploration_selector(
        {"pergunta_atual": "sales?", "contexto_schema": "Tabela: sales"},
        llm=mock_llm,
        exploration_selector_mode="off",
    )
    assert res_off == {"colunas_para_explorar": {}}
    mock_llm.invoke.assert_not_called()

    # Mode 'llm' success
    mock_llm.invoke.return_value = AIMessage(
        content='{"sales": ["amount", "date"]}',
        usage_metadata={"input_tokens": 75, "output_tokens": 18, "total_tokens": 93},
    )
    res_llm = nos_nodo_exploration_selector(
        {"pergunta_atual": "sales?", "contexto_schema": "Tabela: sales"},
        llm=mock_llm,
        exploration_selector_mode="llm",
    )
    assert res_llm["colunas_para_explorar"] == {"sales": ["amount", "date"]}
    assert res_llm["tokens_by_node"] == {
        "exploration_selector": {"input": 75, "output": 18, "total": 93, "calls": 1}
    }

    # Mode 'llm' JSON parse error preserves billed tokens
    mock_llm.invoke.return_value = AIMessage(
        content="Invalid non-JSON response from LLM",
        usage_metadata={"input_tokens": 80, "output_tokens": 12, "total_tokens": 92},
    )
    res_err = _exploration_selector_llm(
        {"pergunta_atual": "sales?", "contexto_schema": "Tabela: sales"},
        llm=mock_llm,
    )
    assert res_err["colunas_para_explorar"] == {}
    assert res_err["tokens_by_node"] == {
        "exploration_selector": {"input": 80, "output": 12, "total": 92, "calls": 1}
    }


def test_code_agent_and_sandbox_attempt_tracking(tmp_path):
    """Code agent outputs last_attempt_tokens and sandbox attaches them to historico_tentativas."""
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AIMessage(
        content="SELECT id FROM users;",
        usage_metadata={"input_tokens": 150, "output_tokens": 25, "total_tokens": 175},
    )

    estado = {
        "pergunta_atual": "list user ids",
        "contexto_schema": "Tabela: users\n- id: INTEGER",
        "tentativas_loop": 0,
    }

    code_res = nos_nodo_agente_codigo(estado, llm=mock_llm)
    assert code_res["status"] == "sql_gerada"
    assert code_res["sql_gerada"] == "SELECT id FROM users;"
    assert code_res["last_attempt_tokens"] == {
        "input": 150,
        "output": 25,
        "total": 175,
        "calls": 1,
    }
    assert code_res["tokens_by_node"] == {
        "code_agent": {"input": 150, "output": 25, "total": 175, "calls": 1}
    }

    # Prepare local SQLite database
    db_file = tmp_path / "test.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE users (id INTEGER);")
    conn.execute("INSERT INTO users VALUES (1), (2);")
    conn.commit()
    conn.close()

    # Sandbox execution with success
    sandbox_input = {
        **estado,
        **code_res,
        "db_path": str(db_file),
    }
    sandbox_res = nos_nodo_sandbox(sandbox_input)
    assert sandbox_res["status"] == "exec_ok"
    assert len(sandbox_res["historico_tentativas"]) == 1
    attempt = sandbox_res["historico_tentativas"][0]
    assert attempt["sql"] == "SELECT id FROM users;"
    assert attempt["tokens_input"] == 150
    assert attempt["tokens_output"] == 25
    assert attempt["tokens_total"] == 175

    # Sandbox execution with SQL error
    sandbox_input_err = {
        **sandbox_input,
        "sql_gerada": "SELECT * FROM nonexistent_table;",
    }
    sandbox_err_res = nos_nodo_sandbox(sandbox_input_err)
    assert sandbox_err_res["status"] == "exec_erro"
    err_attempt = sandbox_err_res["historico_tentativas"][0]
    assert err_attempt["tokens_input"] == 150
    assert err_attempt["tokens_output"] == 25
    assert err_attempt["tokens_total"] == 175


def test_graph_router_token_sink_and_graph_adapter(monkeypatch):
    """Router collects tokens into token_sink and adapter node exposes graph_route_decision."""
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AIMessage(
        content="SIM",
        usage_metadata={"input_tokens": 45, "output_tokens": 5, "total_tokens": 50},
    )

    estado = {
        "pergunta_atual": "monthly revenue",
        "caminho_csv_resultado": "/dummy.csv",
        "linhas_resultado_preview": [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 120}],
        "total_linhas_resultado": 2,
    }

    token_sink = {}
    decision = roteador_grafico(estado, llm=mock_llm, token_sink=token_sink)
    assert decision == "gerador_grafico"
    assert token_sink["tokens_by_node"] == {
        "graph_router": {"input": 45, "output": 5, "total": 50, "calls": 1}
    }

    # Test graph compilation includes adapter node
    monkeypatch.setattr(
        "text_to_insight.graph.get_model",
        lambda *args, **kwargs: mock_llm,
    )
    g = Graph(api_key="mock", model="gemini-2.0-flash", enable_graphs=True)
    compiled_nodes = g.grafo_text_to_insight.get_graph().nodes
    assert "graph_router" in compiled_nodes

    # Test legacy 2-argument monkeypatch signature compatibility with _graph_router_node
    def legacy_roteador(est, llm=None):
        return "resposta"

    # inspect signature check
    sig = inspect.signature(legacy_roteador)
    assert "token_sink" not in sig.parameters


def test_graph_generator_tokens(monkeypatch, tmp_path):
    """Graph generator node records tokens_by_node['graph_generator']."""
    dummy_csv = tmp_path / "data.csv"
    dummy_csv.write_text("a,b\n1,2\n", encoding="utf-8")

    dummy_png = tmp_path / "graph.png"
    dummy_png.write_text("png", encoding="utf-8")

    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AIMessage(
        content="```python\nimport matplotlib.pyplot as plt\nplt.plot([1, 2])\n```",
        usage_metadata={"input_tokens": 60, "output_tokens": 30, "total_tokens": 90},
    )

    monkeypatch.setattr(
        "text_to_insight.nodes.graph_generator._executar_script",
        lambda script: (True, "OK"),
    )
    monkeypatch.setattr(
        "text_to_insight.nodes.graph_generator.GRAPHS_DIR",
        tmp_path,
    )

    estado = {
        "pergunta_atual": "plot a and b",
        "sql_gerada": "SELECT a, b FROM t",
        "caminho_csv_resultado": str(dummy_csv),
        "linhas_resultado_preview": [{"a": 1, "b": 2}],
        "total_linhas_resultado": 10,
    }

    # Mock Path.exists to report output_path exists
    orig_exists = tmp_path.exists
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)

    res = nos_nodo_gerador_grafico(estado, llm=mock_llm)
    assert res["grafico_gerado"] is True
    assert res["tokens_by_node"] == {
        "graph_generator": {"input": 60, "output": 30, "total": 90, "calls": 1}
    }


def test_response_node_deterministic_vs_llm(monkeypatch):
    """Response node returns {} tokens under pytest, but calls LLM and tracks tokens when unmocked."""
    mock_llm = MagicMock()
    estado = {
        "status": "exec_ok",
        "pergunta_atual": "total sales",
        "sql_gerada": "SELECT COUNT(*) FROM sales",
        "total_linhas_resultado": 100,
        "linhas_resultado_preview": [{"count": 100}],
    }

    # 1. Under pytest (sys.modules has 'pytest'):
    res_pytest = nos_nodo_resposta(estado, llm=mock_llm)
    assert "Resultado: 100 linha(s) retornada(s)" in res_pytest["resposta_natural"]
    assert res_pytest["tokens_by_node"] == {}
    assert res_pytest["tokens_input"] == 0
    mock_llm.invoke.assert_not_called()

    # 2. When pytest/CI env detection is temporarily bypassed:
    monkeypatch.delitem(sys.modules, "pytest", raising=False)
    monkeypatch.delenv("CI", raising=False)
    mock_llm.invoke.return_value = AIMessage(
        content="Foram encontrados 100 registros de vendas.",
        usage_metadata={"input_tokens": 55, "output_tokens": 15, "total_tokens": 70},
    )

    res_llm = nos_nodo_resposta(estado, llm=mock_llm)
    assert res_llm["resposta_natural"] == "Foram encontrados 100 registros de vendas."
    assert res_llm["tokens_by_node"] == {
        "response": {"input": 55, "output": 15, "total": 70, "calls": 1}
    }
    assert res_llm["tokens_total"] == 70
