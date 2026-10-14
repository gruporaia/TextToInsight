"""Testes unitários do Registry + Factory de seleção de modelo (sem chamadas de rede)."""

import pytest

from text_to_insight.model_selection import (
    ModelFactory,
    ModelRegistry,
    default_registry,
    get_model,
)


def test_registry_padrao_contem_provedores_builtin():
    assert "gemini" in default_registry
    assert "openai" in default_registry


@pytest.mark.parametrize(
    "model_name, provider",
    [
        ("gemini-2.5-flash", "gemini"),
        ("GEMINI-PRO", "gemini"),
        ("gpt-4o-mini", "openai"),
        ("GPT-4.1", "openai"),
    ],
)
def test_registry_resolve_por_palavra_chave(model_name, provider):
    assert default_registry.resolve(model_name).name == provider


def test_registry_modelo_desconhecido_levanta_value_error():
    with pytest.raises(ValueError, match="não reconhecido"):
        default_registry.resolve("claude-3")


def test_registry_registro_duplicado_levanta_erro():
    registry = ModelRegistry()
    registry.register("x", builder=lambda m, k: None)
    with pytest.raises(ValueError, match="já registrado"):
        registry.register("x", builder=lambda m, k: None)


def test_factory_usa_builder_do_registry_customizado():
    registry = ModelRegistry()
    chamadas = []

    @registry.register("fake", keywords=["fake", "mock"])
    def _build(model_name, api_key):
        chamadas.append((model_name, api_key))
        return {"model": model_name, "key": api_key}

    factory = ModelFactory(registry)
    assert factory.create("Mock-Model", "k1") == {"model": "Mock-Model", "key": "k1"}
    assert chamadas == [("Mock-Model", "k1")]

    registry.unregister("fake")
    with pytest.raises(ValueError):
        factory.create("mock-model", "k1")


def test_get_model_instancia_gemini():
    from langchain_google_genai import ChatGoogleGenerativeAI

    llm = get_model("gemini-2.5-flash", "fake-key")
    assert isinstance(llm, ChatGoogleGenerativeAI)


def test_get_model_instancia_openai():
    from langchain_openai import ChatOpenAI

    llm = get_model("gpt-4o-mini", "fake-key")
    assert isinstance(llm, ChatOpenAI)


def test_get_model_openai_repassa_kwargs():
    llm = get_model("gpt-4o-mini", "fake-key", temperature=0.2, max_tokens=128)
    assert llm.temperature == 0.2
    assert llm.max_tokens == 128


def test_get_model_gemini_repassa_kwargs():
    llm = get_model("gemini-2.5-flash", "fake-key", temperature=0.1, max_tokens=64)
    assert llm.temperature == 0.1
    assert llm.max_output_tokens == 64


def test_factory_repassa_kwargs_ao_builder():
    registry = ModelRegistry()
    registry.register("fake", builder=lambda m, k, **kw: kw)
    assert ModelFactory(registry).create("fake-1", "k", temperature=0.5) == {"temperature": 0.5}


def test_get_model_desconhecido_levanta_value_error():
    with pytest.raises(ValueError):
        get_model("llama-3", "fake-key")


# --------------------------------------------------------------------------- #
# Propagação de model_kwargs: CLI -> InsightEngine -> Graph -> get_model
# --------------------------------------------------------------------------- #

from text_to_insight import cli  # noqa: E402


def test_cli_flags_ausentes_nao_geram_model_kwargs():
    args = cli._parse_args([])
    assert cli._montar_model_kwargs(args) == {}


def test_cli_flags_geram_model_kwargs():
    args = cli._parse_args(["--temperature", "0", "--max-tokens", "256"])
    assert cli._montar_model_kwargs(args) == {"temperature": 0.0, "max_tokens": 256}


@pytest.mark.parametrize(
    "argv",
    [["--temperature", "-1"], ["--max-tokens", "0"], ["--temperature", "abc"]],
)
def test_cli_flags_invalidas_sao_rejeitadas(argv):
    with pytest.raises(SystemExit):
        cli._parse_args(argv)


def test_cli_repassa_model_kwargs_para_engine(monkeypatch):
    capturado = {}

    class _FakeEngine:
        def __init__(self, **kwargs):
            capturado.update(kwargs)

        def run(self, **kwargs):
            return {"status": "aprovado"}

    monkeypatch.setattr(cli, "InsightEngine", _FakeEngine)
    monkeypatch.setattr(cli, "exibir_resultado_console", lambda r: None)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")

    cli.main(["--hitl", "off", "--temperature", "0.3", "--max-tokens", "100", "pergunta"])
    assert capturado["model_kwargs"] == {"temperature": 0.3, "max_tokens": 100}


def test_graph_repassa_model_kwargs_para_get_model(monkeypatch):
    import text_to_insight.graph as graph_module

    chamadas = []

    def _fake_get_model(model, api_key, **kwargs):
        chamadas.append((model, api_key, kwargs))
        return object()

    monkeypatch.setattr(graph_module, "get_model", _fake_get_model)
    graph_module.Graph(api_key="k", model="gpt-x", hitl=False, model_kwargs={"temperature": 0})
    assert chamadas == [("gpt-x", "k", {"temperature": 0})]


def test_engine_repassa_model_kwargs_para_graph(monkeypatch):
    import importlib

    engine_module = importlib.import_module("text_to_insight.InsightEngine")

    capturado = {}

    class _FakeGraph:
        def __init__(self, **kwargs):
            capturado.update(kwargs)

    monkeypatch.setattr(engine_module, "Graph", _FakeGraph)
    engine_module.InsightEngine(
        api_key="k", model="gpt-x", db_path="x.db", model_kwargs={"max_tokens": 10}
    )
    assert capturado["model_kwargs"] == {"max_tokens": 10}
