# Guia de Desenvolvimento - Text-to-Insight

## Namespace oficial

O codigo-fonte da biblioteca esta no pacote `text_to_insight`.
Imports antigos via `src` nao devem mais ser usados.

## Setup local

### Pré-requisitos

- Python 3.10+
- venv/conda
- chave de API (Gemini ou OpenAI, conforme modelo escolhido)

### Instalação

```bash
python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
pip install -e .
```

### Configuracao

```bash
echo "GOOGLE_API_KEY=sua_chave" > .env
```

Banco SQLite esperado por padrao: `data/olist_relational.db`.

###  Verificação Rápida (Smoke) de import

```bash
python -c "from text_to_insight import InsightEngine, Graph; print('OK')"
```

## Contrato de execução

API estável da engine:

- `run(thread_id, query)` para iniciar;
- `resume(thread_id, user_response)` para retomar HITL;
- `get_insight(...)` mantido como API base.

Criterios minimos:

- fluxo completo do grafo;
- HITL on/off;
- retomada por `thread_id`;
- gravacao de metricas em CSV.

## Contrato HITL (perguntas)

- `pergunta_original`: pergunta inicial da thread, imutavel apos o primeiro set.
- `pergunta_atual`: pergunta corrente e fonte de verdade para o fluxo.
- Em HITL, se a resposta do usuario for classificada como "nova pergunta",
  o sistema atualiza `pergunta_atual` e reinicia o ciclo, sem alterar a original.

## Execução

```bash
# adaptador local
python main.py --hitl on "Quantos pedidos existem no banco?"

# biblioteca instalada (entrypoint)
text-to-insight --hitl off "Quais categorias vendem mais?"

# com modelo OpenAI
set -a && source .env && set +a
python main.py --hitl off --model gpt-4o-mini --api-key-env OPENAI_API_KEY "Quantos pedidos existem?"

# testes locais com bancos do Spider 2 (SQLite sem FK explícita e PRAGMA nativo)
python main.py --hitl off --infer-fks on --use-schemacrawler off "Qual a soma dos resultados?"
```

O resultado e exibido no terminal em formato tabular sob o bloco `RESULTADO:`, junto com SQL gerada, feedback do critico e resposta natural.

## Estrutura relevante

```text
text_to_insight/
    InsightEngine.py
    cli.py
    runtime.py
    graph.py
    state.py
    model_selection.py
    nodes/
    routers/
tests/
    test_componentes.py
    test_nodes.py
    test_integracao.py
    test_main_engine_integracao.py
    test_real_api_smoke.py
```

## Testes

### Camadas

```bash
# camada 1 - componentes deterministicos
pytest tests/test_componentes.py -v -s

# camada 2 - nos com VCR (replay, sem gravar novas cassetes)
pytest tests/test_nodes.py -v -s --record-mode=none

# camada 2 - nos com VCR (gravar/atualizar cassetes)
pytest tests/test_nodes.py -v -s --record-mode=new_episodes

# camada 3 - integracao do grafo com VCR (replay, sem gravar)
pytest tests/test_integracao.py -v -s --record-mode=none

# camada 3 - integracao do grafo com VCR (gravar/atualizar cassetes)
pytest tests/test_integracao.py -v -s --record-mode=new_episodes

# integracao main + InsightEngine
pytest tests/test_main_engine_integracao.py -v -s
```

### Gravação de cassetes VCR (fluxo recomendado)

Use este fluxo quando mudar prompts, comportamento de nos ou quando adicionar testes com `@pytest.mark.vcr`:

```bash
# 1) Grave/atualize as cassetes
pytest tests/test_nodes.py tests/test_integracao.py -v -s --record-mode=new_episodes

# 2) Rode em replay para garantir determinismo
pytest tests/test_nodes.py tests/test_integracao.py -v -s --record-mode=none
```

Observacões:

- cassetes ficam em `tests/cassettes/`;
- `new_episodes` grava apenas chamadas que ainda nao existem no YAML;
- `none` falha se faltar cassette, garantindo execucao reproduzivel.

### Drift provider/modelo (opcional) para verificar se API real ainda responde conforme esperado:

```bash
pytest tests/test_real_api_smoke.py -v -s -m real_api
```

## CI hibrida

Arquivo: `.github/workflows/ci.yml`

- job padrao deterministico em PR/push (VCR + `--record-mode=none`);
- job manual `record-vcr-cassettes` em `workflow_dispatch` para gravar/atualizar cassetes com API real;
- job opcional real API em `workflow_dispatch` e `schedule`.

## Build e distribuicao

```bash
python -m build

python -m venv .venv-smoke
source .venv-smoke/bin/activate
pip install dist/*.whl
python -c "from text_to_insight import InsightEngine; print('wheel_ok')"
```

## Troubleshooting (diagnóstico de falhas) rápido

`ModuleNotFoundError`:
```bash
pip install -r requirements.txt
pip install -e .
```

`429 RESOURCE_EXHAUSTED`:
- aguardar reset de quota;
- preferir testes com VCR no dia a dia.

## Configuração do .env (variáveis de ambiente)
- `GOOGLE_API_KEY`: chave de API para Google Gemini (se usar modelo Gemini).
- `OPENAI_API_KEY`: chave de API para OpenAI (se usar modelo OpenAI).
- `SCHEMACRAWLER_BIN`: caminho para o binário do SchemaCrawler (se usar este recurso).
