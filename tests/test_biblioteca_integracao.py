"""Testes de integridade do Text-to-Insight como biblioteca.

Responsabilidade deste arquivo
------------------------------
Verificar o contrato público do pacote quando consumido como biblioteca
(`from text_to_insight import ...`), sem passar pela CLI ou pelo `main.py`.
Complementa `test_main_engine_integracao.py`, que exercita o caminho
CLI + engine.

Aqui o foco é o que um desenvolvedor externo enxerga ao integrar a
biblioteca: quais símbolos estão expostos, como `InsightEngine.run`,
`resume` e o callback `on_human_prompt` se comportam, e qual é o formato
documentado do payload `AWAITING_USER`.

Estratégia
----------
Substituímos o `Graph` real por um fake determinístico que dispara HITL
quando a pergunta contém "hitl" (mesma convenção do arquivo vizinho).
Nenhuma chamada de API é feita.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

import text_to_insight
from text_to_insight import InsightEngine


# --------------------------------------------------------------------------- #
# Fake graph — reproduz o padrão de `test_main_engine_integracao.py` para
# manter a convenção de "hitl na pergunta → pausa". Mantido self-contained
# para não acoplar dois test files por um helper compartilhado.
# --------------------------------------------------------------------------- #

class _FakeCompiledGraph:
    def __init__(self):
        self._threads: dict[str, dict] = {}

    def _thread_state(self, thread_id: str) -> dict:
        if thread_id not in self._threads:
            self._threads[thread_id] = {"values": {}, "next": ()}
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
            pergunta = (
                str(estado_execucao.get("pergunta_atual", ""))
                or str(estado_execucao.get("pergunta_original", ""))
                or str(estado_execucao.get("pergunta_usuario", ""))
            )
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


@pytest.fixture
def patched_runtime(monkeypatch):
    """Substitui o Graph real pelo fake e silencia o CSV de métricas."""
    insight_engine_module = importlib.import_module("text_to_insight.InsightEngine")
    monkeypatch.setattr(insight_engine_module, "Graph", _FakeGraph)
    monkeypatch.setattr(
        "text_to_insight.runtime.salvar_metricas_csv",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key")


def _fabricar_engine(hitl: bool = False) -> InsightEngine:
    return InsightEngine(
        api_key="fake-key",
        model="gemini-2.5-flash",
        db_path="data/olist_relational.db",
        hitl=hitl,
        show_output=False,
    )


# --------------------------------------------------------------------------- #
# Contrato de importação — a API pública documentada em __init__.py
# --------------------------------------------------------------------------- #

def test_simbolos_publicos_expostos_no_topo_do_pacote():
    """`text_to_insight` re-exporta os símbolos anunciados em `__all__`."""
    from text_to_insight import EstadoTextToInsight, Graph, InsightEngine as IE

    assert IE is InsightEngine
    assert set(text_to_insight.__all__) == {"InsightEngine", "Graph", "EstadoTextToInsight"}
    assert text_to_insight.Graph is Graph
    assert text_to_insight.EstadoTextToInsight is EstadoTextToInsight


# --------------------------------------------------------------------------- #
# Construção e defaults do engine
# --------------------------------------------------------------------------- #

def test_engine_constroi_com_defaults_documentados(patched_runtime):
    """O construtor aceita só os campos obrigatórios e aplica defaults sensatos."""
    engine = InsightEngine(
        api_key="fake-key",
        model="gemini-2.5-flash",
        db_path="data/olist_relational.db",
    )
    assert engine._hitl_ativado is False
    assert engine._show_output is False
    assert engine._db_path == "data/olist_relational.db"


# --------------------------------------------------------------------------- #
# `run` — caminho feliz
# --------------------------------------------------------------------------- #

def test_run_pergunta_clara_retorna_resultado_aprovado(patched_runtime):
    """Com HITL off e pergunta clara, `run` devolve um dict aprovado completo."""
    engine = _fabricar_engine(hitl=False)

    resultado = engine.run(thread_id="t_feliz", query="Quantos pedidos existem?")

    assert resultado["status"] == "aprovado"
    assert resultado["sql_gerada"] == "SELECT 1 AS total"
    assert resultado["resposta_natural"].startswith("Existe")
    assert resultado["linhas_resultado_preview"] == [{"total": 1}]
    assert resultado["total_linhas_resultado"] == 1


# --------------------------------------------------------------------------- #
# `run` — contrato do payload AWAITING_USER
# --------------------------------------------------------------------------- #

def test_run_sem_callback_devolve_awaiting_user_com_payload_documentado(patched_runtime):
    """O Ata 6 define esse contrato: status+message+chat_history+thread_id."""
    engine = _fabricar_engine(hitl=True)

    resultado = engine.run(thread_id="t_hitl_pause", query="consulta com hitl ambigua")

    assert resultado["status"] == "AWAITING_USER"
    assert resultado["thread_id"] == "t_hitl_pause"
    assert resultado["message"] == "Pode confirmar os filtros da consulta?"
    assert resultado["chat_history"] == []
    assert set(resultado.keys()) == {"status", "message", "chat_history", "thread_id"}


# --------------------------------------------------------------------------- #
# `resume` — continuação após uma pausa HITL
# --------------------------------------------------------------------------- #

def test_resume_retoma_fluxo_pausado_e_registra_historico(patched_runtime):
    engine = _fabricar_engine(hitl=True)
    engine.run(thread_id="t_resume", query="consulta com hitl ambigua")

    resultado = engine.resume(thread_id="t_resume", user_response="Sim, pode prosseguir")

    assert resultado["status"] == "aprovado"
    assert resultado["espera_humana"] is False
    historico = resultado.get("historico_conversa", [])
    assert len(historico) == 1
    pergunta_ai, resposta_user = historico[0]
    assert "Pode confirmar" in pergunta_ai
    assert "prosseguir" in resposta_user


# --------------------------------------------------------------------------- #
# `on_human_prompt` — consumer supplies a callback to resolve the pause inline
# --------------------------------------------------------------------------- #

def test_run_com_callback_resolve_pausa_sem_expor_awaiting_user(patched_runtime):
    """Quando o consumidor passa `on_human_prompt`, a engine não precisa
    devolver AWAITING_USER: ela chama o callback e segue até aprovação."""
    chamadas: list[str] = []

    def callback(pergunta_agente: str) -> str:
        chamadas.append(pergunta_agente)
        return "ok, prossiga"

    engine = _fabricar_engine(hitl=True)
    resultado = engine.run(
        thread_id="t_callback",
        query="consulta com hitl ambigua",
        on_human_prompt=callback,
    )

    assert resultado["status"] == "aprovado"
    assert chamadas == ["Pode confirmar os filtros da consulta?"]


# --------------------------------------------------------------------------- #
# Isolamento entre threads
# --------------------------------------------------------------------------- #

def test_threads_distintas_nao_compartilham_historico(patched_runtime):
    """Duas threads coexistem: pausa em uma não polui a outra."""
    engine = _fabricar_engine(hitl=True)
    engine.run(thread_id="t_A", query="consulta com hitl ambigua")  # pausa

    resultado_b = engine.run(thread_id="t_B", query="pergunta clara e direta")

    assert resultado_b["status"] == "aprovado"
    assert resultado_b.get("historico_conversa", []) == []


# --------------------------------------------------------------------------- #
# HITL desligado + pergunta ambígua → bloqueio com contrato documentado
# --------------------------------------------------------------------------- #

def test_run_com_hitl_off_bloqueia_quando_planner_pede_humano(patched_runtime):
    """Com HITL off, se o planner ainda pedir humano, o engine bloqueia em vez
    de travar — e o payload explica o motivo."""
    engine = _fabricar_engine(hitl=False)

    resultado = engine.run(thread_id="t_block", query="consulta com hitl ambigua")

    assert resultado["status"] == "bloqueado_hitl"
    assert "intervenção humana" in resultado["erro_execucao"].lower()
    assert "[HITL]" in resultado["saida_terminal"]
