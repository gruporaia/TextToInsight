"""
Testes individuais de cada nó com API real + banco real (Layer 2).

Cada teste exercita UM ÚNICO nó isoladamente, com estado construído manualmente.
Se um teste falha, você sabe exatamente qual nó quebrou.

Usa API real — requer GOOGLE_API_KEY ou OPENAI_API_KEY e quota disponível.
Executa: pytest tests/test_nodes.py -v -s
"""

import os
import sys
import time
import csv
import subprocess
from pathlib import Path
import pytest
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "olist_relational.db")

def _obter_schema_real() -> str:
    """Helper: extrai schema real do olist DB (sem API, só SQLite)."""
    from text_to_insight.nodes.schema import nos_nodo_esquema
    resultado = nos_nodo_esquema({"db_path": DB_PATH, "pergunta_atual": "teste"})
    return resultado["contexto_schema"]


# ============================================================
# PLANNER — caminho determinístico (sem API)
# ============================================================

def test_planner_sem_schema(llm):
    """Planner sem schema → aguardando_schema (determinístico, sem API)."""
    from text_to_insight.nodes.planner import nos_nodo_planejador

    estado = {
        "pergunta_atual": "Quantos pedidos existem?",
        "contexto_schema": "",
        "feedback_critico": "",
        "status": "iniciado",
        "tentativas_loop": 0,
        "erro_execucao": "",
    }
    resultado = nos_nodo_planejador(estado, llm, hitl=True)
    assert resultado["status"] == "aguardando_schema"


# ============================================================
# PLANNER — com API
# ============================================================

@pytest.mark.vcr
@pytest.mark.timeout(60)
def test_planner_com_schema_decide_codificar(llm):
    """Planner com schema e sem feedback → deve decidir gerar código."""
    from text_to_insight.nodes.planner import nos_nodo_planejador

    schema = _obter_schema_real()
    estado = {
        "pergunta_atual": "Quantos pedidos existem no banco?",
        "contexto_schema": schema,
        "feedback_critico": "",
        "status": "schema_obtido",
        "tentativas_loop": 0,
        "erro_execucao": "",
    }
    resultado = nos_nodo_planejador(estado, llm, hitl=True)

    assert resultado["status"] in ("pronto_codificacao", "revisando_estrategia")
    print(f"  → Planner decidiu: {resultado['status']}")


@pytest.mark.vcr
@pytest.mark.timeout(60)
def test_planner_com_feedback_revisa(llm):
    """Planner com feedback do crítico → deve revisar estratégia."""
    from text_to_insight.nodes.planner import nos_nodo_planejador

    time.sleep(5)  # rate limit
    schema = _obter_schema_real()
    estado = {
        "pergunta_atual": "Quantos pedidos existem no banco?",
        "contexto_schema": schema,
        "feedback_critico": "A SQL retornou dados incorretos, faltou filtrar por status.",
        "status": "reprovado",
        "tentativas_loop": 1,
        "erro_execucao": "",
    }
    resultado = nos_nodo_planejador(estado, llm, hitl=True)

    assert resultado["status"] in ("pronto_codificacao", "revisando_estrategia", "aguardando_input")
    print(f"  → Planner decidiu: {resultado['status']}")

@pytest.mark.vcr
@pytest.mark.timeout(60)
def test_planner_pergunta_fora_de_escopo(llm):
    """Planner deve detectar pergunta fora de escopo e levantar a flag de HITL."""
    from text_to_insight.nodes.planner import nos_nodo_planejador

    time.sleep(5)  # rate limit
    schema = _obter_schema_real()
    estado = {
        "pergunta_atual": "Quantas vezes a Ahri ganhou o CBLOL?", 
        "contexto_schema": schema,
        "feedback_critico": "",
        "status": "schema_obtido",
        "tentativas_loop": 0,
        "erro_execucao": "",
    }
    
    resultado = nos_nodo_planejador(estado, llm, hitl=True)

    assert resultado.get("espera_humana") is True
    assert resultado.get("status") == "aguardando_input"
    assert "pergunta_ao_usuario" in resultado
    assert len(resultado["pergunta_ao_usuario"]) > 5
    print(f"  → Agente bloqueou com sucesso: {resultado['pergunta_ao_usuario']}")

# ============================================================
# CODE AGENT — com API
# ============================================================

@pytest.mark.vcr
@pytest.mark.timeout(60)
def test_code_agent_gera_sql(llm):
    """Code Agent recebe pergunta + schema → retorna SQL válida."""
    from text_to_insight.nodes.code_agent.code_agent import nos_nodo_agente_codigo

    time.sleep(5)  # rate limit
    schema = _obter_schema_real()
    estado = {
        "pergunta_atual": "Quantos pedidos existem no banco?",
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
    from text_to_insight.nodes.code_agent.code_agent import nos_nodo_agente_codigo

    time.sleep(5)  
    schema = _obter_schema_real()
    estado = {
        "pergunta_atual": "Quais as 5 categorias de produtos mais vendidas?",
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
    from text_to_insight.nodes.sandbox import nos_nodo_sandbox

    estado = {
        "sql_gerada": "SELECT COUNT(*) as total_pedidos FROM orders",
        "db_path": DB_PATH,
        "pergunta_atual": "Quantos pedidos existem?",
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
    from text_to_insight.nodes.critic import nos_nodo_critico

    time.sleep(5)  # rate limit
    estado = {
        "pergunta_atual": "Quantos pedidos existem no banco?",
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
    from text_to_insight.nodes.critic import nos_nodo_critico

    estado = {
        "pergunta_atual": "Quantos pedidos existem?",
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
    from text_to_insight.nodes.code_agent.code_agent import nos_nodo_agente_codigo
    from text_to_insight.nodes.sandbox import nos_nodo_sandbox

    time.sleep(5)  # rate limit
    schema = _obter_schema_real()

    # Passo 1: Code Agent gera SQL
    estado_code = {
        "pergunta_atual": "Quantos clientes existem no banco?",
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
        "pergunta_atual": "Quantos clientes existem no banco?",
    }
    resultado_exec = nos_nodo_sandbox(estado_exec)
    print(f"  → Status execução: {resultado_exec['status']}")
    print(f"  → Preview: {resultado_exec.get('linhas_resultado_preview', [])}")

    assert resultado_exec["status"] == "exec_ok"
    assert resultado_exec["total_linhas_resultado"] >= 1


# ============================================================
# GRÁFICOS — helpers determinísticos
# ============================================================

def test_extrair_codigo_python_com_markdown():
    from text_to_insight.nodes import graph_generator as gg

    # Extract only the code block, without markdown wrappers.
    texto = """```python
print('ok')
```"""
    assert gg._extrair_codigo_python(texto) == "print('ok')"


def test_extrair_codigo_python_sem_markdown():
    from text_to_insight.nodes import graph_generator as gg

    # Fallback path: when no markdown is present, return the original string.
    texto = "print('ok')"
    assert gg._extrair_codigo_python(texto) == "print('ok')"


def test_construir_script_inclui_csv_e_saida():
    from text_to_insight.nodes import graph_generator as gg

    # Validate that the script includes the CSV load, plotting code, and output path.
    script = gg._construir_script("/tmp/dados.csv", "/tmp/saida.png", "plt.plot([1],[2])")
    assert "pd.read_csv(\"/tmp/dados.csv\")" in script
    assert "plt.savefig(\"/tmp/saida.png\"" in script
    assert "plt.plot([1],[2])" in script
    assert "matplotlib.use('Agg')" in script


# ============================================================
# GRÁFICOS — csv_saver
# ============================================================

def test_csv_saver_sem_linhas(tmp_path, monkeypatch):
    from text_to_insight.nodes import csv_saver as csv_module

    # Redirect output to tmp_path and ensure empty input returns no file.
    monkeypatch.setattr(csv_module, "RESULTS_DIR", tmp_path / "results")
    resultado = csv_module.nos_nodo_salvar_csv({"linhas_resultado_completo": []})

    assert resultado["caminho_csv_resultado"] == ""


def test_csv_saver_cria_arquivo(tmp_path, monkeypatch):
    from text_to_insight.nodes import csv_saver as csv_module

    # Redirect output to tmp_path and validate header/row count and UTF-8 data.
    monkeypatch.setattr(csv_module, "RESULTS_DIR", tmp_path / "results")
    linhas = [
        {"categoria": "café", "valor": 10},
        {"categoria": "açaí", "valor": 12},
    ]

    resultado = csv_module.nos_nodo_salvar_csv({"linhas_resultado_completo": linhas})
    caminho = Path(resultado["caminho_csv_resultado"])

    assert caminho.exists()
    with caminho.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert reader.fieldnames == ["categoria", "valor"]
    assert len(rows) == 2
    assert rows[0]["categoria"] == "café"


# ============================================================
# GRÁFICOS — roteador_grafico (determinístico)
# ============================================================

class _FakeResponse:
    def __init__(self, content: str):
        self.content = content
        self.usage_metadata = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


class _FakeLLM:
    def __init__(self, content: str):
        self._content = content
        self.called = False

    def invoke(self, prompt: str):
        # Use a deterministic response without caring about the prompt text.
        self.called = True
        return _FakeResponse(self._content)


class _NoCallLLM:
    def invoke(self, prompt: str):
        # If invoked, the test should fail because this path should short-circuit.
        raise AssertionError("LLM não deveria ser chamado")


class _ErrorLLM:
    def invoke(self, prompt: str):
        # Force the fallback path by raising an exception.
        raise RuntimeError("boom")


def test_roteador_grafico_sem_csv_nao_chama_llm():
    from text_to_insight.routers.edges import roteador_grafico

    # Missing CSV should bypass LLM and return resposta.
    estado = {
        "pergunta_usuario": "Teste",
        "linhas_resultado_preview": [{"a": 1}],
        "total_linhas_resultado": 2,
        "caminho_csv_resultado": "",
    }
    assert roteador_grafico(estado, _NoCallLLM()) == "resposta"


def test_roteador_grafico_uma_linha_nao_chama_llm():
    from text_to_insight.routers.edges import roteador_grafico

    # Single-row results should not trigger visualization.
    estado = {
        "pergunta_usuario": "Teste",
        "linhas_resultado_preview": [{"a": 1}],
        "total_linhas_resultado": 1,
        "caminho_csv_resultado": "/tmp/resultado.csv",
    }
    assert roteador_grafico(estado, _NoCallLLM()) == "resposta"


def test_roteador_grafico_fallback_em_erro():
    from text_to_insight.routers.edges import roteador_grafico

    # Any LLM failure should fall back to resposta.
    estado = {
        "pergunta_usuario": "Teste",
        "linhas_resultado_preview": [{"a": 1}, {"a": 2}],
        "total_linhas_resultado": 2,
        "caminho_csv_resultado": "/tmp/resultado.csv",
    }
    assert roteador_grafico(estado, _ErrorLLM()) == "resposta"


def test_roteador_grafico_decisao_sim():
    from text_to_insight.routers.edges import roteador_grafico

    # Deterministic positive decision from fake LLM.
    llm = _FakeLLM("SIM")
    estado = {
        "pergunta_usuario": "Teste",
        "linhas_resultado_preview": [{"a": 1}, {"a": 2}],
        "total_linhas_resultado": 2,
        "caminho_csv_resultado": "/tmp/resultado.csv",
    }
    assert roteador_grafico(estado, llm) == "gerador_grafico"
    assert llm.called is True


def test_roteador_grafico_decisao_nao():
    from text_to_insight.routers.edges import roteador_grafico

    # Deterministic negative decision from fake LLM.
    llm = _FakeLLM("NAO")
    estado = {
        "pergunta_usuario": "Teste",
        "linhas_resultado_preview": [{"a": 1}, {"a": 2}],
        "total_linhas_resultado": 2,
        "caminho_csv_resultado": "/tmp/resultado.csv",
    }
    assert roteador_grafico(estado, llm) == "resposta"
    assert llm.called is True


# ============================================================
# GRAFICOS — executador de script (subprocesso)
# ============================================================

def test_executar_script_sucesso():
    from text_to_insight.nodes import graph_generator as gg

    # Minimal script that signals success via GRAPH_OK.
    ok, saida = gg._executar_script("print('GRAPH_OK')")
    assert ok is True
    assert "GRAPH_OK" in saida


def test_executar_script_timeout(monkeypatch):
    from text_to_insight.nodes import graph_generator as gg

    # Simulate a subprocess timeout to validate the error path.
    def _fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=30)

    monkeypatch.setattr(gg.subprocess, "run", _fake_run)
    ok, saida = gg._executar_script("print('GRAPH_OK')")

    assert ok is False
    assert "Timeout" in saida


def test_executar_script_falha_retorno(monkeypatch):
    from text_to_insight.nodes import graph_generator as gg

    # Simulate a non-zero return code and stderr output.
    def _fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=1, stdout="", stderr="erro")

    monkeypatch.setattr(gg.subprocess, "run", _fake_run)
    ok, saida = gg._executar_script("print('GRAPH_OK')")

    assert ok is False
    assert "erro" in saida


def test_executar_script_limpa_temporario(monkeypatch, tmp_path):
    from text_to_insight.nodes import graph_generator as gg

    temp_path = tmp_path / "temp_script.py"

    class _TempFile:
        def __init__(self, path: Path):
            self.name = str(path)
            self._fh = open(self.name, "w", encoding="utf-8")

        def write(self, data: str) -> None:
            self._fh.write(data)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self._fh.close()

    def _fake_named_tempfile(*args, **kwargs):
        # Use a deterministic temp path to verify cleanup.
        return _TempFile(temp_path)

    def _fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="GRAPH_OK", stderr="")

    monkeypatch.setattr(gg.tempfile, "NamedTemporaryFile", _fake_named_tempfile)
    monkeypatch.setattr(gg.subprocess, "run", _fake_run)

    ok, _ = gg._executar_script("print('GRAPH_OK')")
    assert ok is True
    assert temp_path.exists() is False


# ============================================================
# SCHEMA — com DB local (determinístico)
# ============================================================

def test_nos_nodo_esquema_banco_local():
    """Testa a introspecção de schema (PKs/FKs virtuais) sem schemacrawler."""
    from text_to_insight.nodes.schema import nos_nodo_esquema
    from text_to_insight.runtime import construir_estado_inicial
    
    state = construir_estado_inicial(
        pergunta='',
        db_path='spider2-lite/resource/databases/spider2-localdb/bank_sales_trading.sqlite', 
        inferir_fks_virtuais=True,
        inferir_pks_virtuais=True,
        usar_schemacrawler=False
    )
    new_state = nos_nodo_esquema(state)
    
    assert new_state is not None
    assert "contexto_schema" in new_state
    assert new_state.get("status") in ("schema_obtido", "exec_erro")
    print(new_state.get("contexto_schema", ""))
