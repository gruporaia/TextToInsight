"""Integração entre adaptador CLI (main.py) e InsightEngine."""

import os
import sys
import importlib
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main as main_entry
from text_to_insight.InsightEngine import InsightEngine


class _FakeCompiledGraph:
    def __init__(self):
        self._threads: dict[str, dict] = {}

    def _thread_state(self, thread_id: str) -> dict:
        if thread_id not in self._threads:
            self._threads[thread_id] = {
                "values": {},
                "next": (),
            }
        return self._threads[thread_id]

    @staticmethod
    def _estado_aprovado(base_state: dict) -> dict:
        estado = dict(base_state)
        estado.update(
            {
                "status": "aprovado",
                "espera_humana": False,
                "sql_gerada": "SELECT 1 AS total",
                "saida_terminal": "[EXECUTOR] Execucao OK | linhas_total=1 | preview=1",
                "linhas_resultado_preview": [{"total": 1}],
                "total_linhas_resultado": 1,
                "feedback_critico": "Resultado aprovado.",
                "resposta_natural": "Existe 1 registro no resultado.",
            }
        )
        return estado

    def stream(self, estado_execucao, config, stream_mode="values"):
        thread_id = config["configurable"]["thread_id"]
        state = self._thread_state(thread_id)

        if estado_execucao is not None:
            pergunta = str(estado_execucao.get("pergunta_usuario", ""))
            if "hitl" in pergunta.lower():
                state["values"] = {
                    **estado_execucao,
                    "status": "aguardando_input",
                    "espera_humana": True,
                    "pergunta_ao_usuario": "Pode confirmar os filtros da consulta?",
                }
                state["next"] = ("espera_humana",)
            else:
                state["values"] = self._estado_aprovado(estado_execucao)
                state["next"] = ()
        else:
            state["values"] = self._estado_aprovado(state["values"])
            state["next"] = ()

        yield state["values"]

    def get_state(self, config):
        thread_id = config["configurable"]["thread_id"]
        state = self._thread_state(thread_id)
        return SimpleNamespace(values=state["values"], next=state["next"])

    def update_state(self, config, values):
        thread_id = config["configurable"]["thread_id"]
        state = self._thread_state(thread_id)
        state["values"].update(values)


class _FakeGraph:
    def __init__(self, api_key, model, hitl=True):
        self.grafo_text_to_insight = _FakeCompiledGraph()


def _patch_integracao(monkeypatch):
    insight_engine_module = importlib.import_module("text_to_insight.InsightEngine")
    monkeypatch.setattr(insight_engine_module, "Graph", _FakeGraph)
    monkeypatch.setattr("text_to_insight.runtime.salvar_metricas_csv", lambda *args, **kwargs: None)
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key")


def test_integracao_main_engine_sucesso(monkeypatch):
    _patch_integracao(monkeypatch)

    resultado = main_entry.main(["--hitl", "on", "Quantos pedidos existem?"])

    assert resultado["status"] == "aprovado"
    assert resultado["sql_gerada"] == "SELECT 1 AS total"


def test_integracao_main_engine_hitl_on_com_retomada(monkeypatch):
    _patch_integracao(monkeypatch)

    engine = InsightEngine(
        api_key="fake-key",
        model="gemini-2.5-flash",
        db_path="data/olist_relational.db",
        hitl=True,
        show_output=False,
    )

    primeiro = engine.get_insight(thread_id="thread_hitl", query="consulta com hitl")
    assert primeiro["status"] == "AWAITING_USER"

    segundo = engine.resume(thread_id="thread_hitl", user_response="Sim, pode prosseguir")
    assert segundo["status"] == "aprovado"
    assert len(segundo.get("historico_conversa", [])) == 1


def test_integracao_main_engine_hitl_off_bloqueado(monkeypatch):
    _patch_integracao(monkeypatch)

    resultado = main_entry.main(["--hitl", "off", "consulta com hitl"])

    assert resultado["status"] == "bloqueado_hitl"
    assert "intervenção humana" in resultado["erro_execucao"].lower()
