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

Cria um ambiente isolado, instala as dependências e registra o pacote local em modo editável.

```bash
python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
pip install -e ".[dev,postgres,mysql]"
```

- `python -m venv .venv`: cria um ambiente isolado.
- `source .venv/bin/activate`: ativa esse ambiente.
- `pip install -r requirements.txt`: instala as versões fixadas de runtime e das ferramentas de teste (é o que a CI usa).
- `pip install -e ".[dev,postgres,mysql]"`: instala o TextToInsight em modo editável, com as ferramentas de desenvolvimento e os drivers de PostgreSQL/MySQL (extras do `pyproject.toml`).

### Configuração

```bash
echo "GOOGLE_API_KEY=sua_chave" > .env
```

Banco de teste (Olist, ~130 MB), usado pela suíte de testes e pelos exemplos abaixo:

```bash
gh release download dados-teste -R gruporaia/TextToInsight -p olist_relational.db -D data/
```

###  Verificação Rápida (Smoke) de import

```bash
python -c "from text_to_insight import InsightEngine, Graph; print('OK')"
```

## Conexão com o banco

A biblioteca não abre conexões. Quem usa a `InsightEngine` cria uma Engine do
SQLAlchemy e passa em `db_engine`; a engine é injetada direto nos nós do grafo
(via `functools.partial` em `graph.py`) e nunca entra no estado, então nada
sobre a conexão vai para o checkpoint do LangGraph. O dialeto do prompt vem de
`engine.dialect.name`.

Quem abre conexões são os chamadores: o CLI (`text_to_insight/cli.py`, a partir
de `--db-path` ou `--db-url`), os scripts de avaliação em `scripts/` e os
testes (fixture `olist_engine` em `tests/conftest.py`). Todos abrem SQLite em
modo somente leitura (`sqlite:///file:...?mode=ro&uri=true`).

## Cache em disco

`text_to_insight/cache.py` concentra o que a biblioteca grava entre execuções,
numa pasta por usuário (via `platformdirs`) ou em `InsightEngine(cache_dir=...)`:

- `schemas/<hash>_enriched.txt`: schema enriquecido pelo LLM (`enrich_rag`),
  indexado pelo hash do schema. Só é lido quando `enrich_rag` está ligado, para
  que execuções sem enriquecimento nunca recebam descrições de execuções anteriores;
- `chroma_db/`: índice do ChromaDB do RAG e do seletor de colunas.

Nos testes, a fixture `cache_dir_isolado` (autouse) redireciona o cache para uma
pasta temporária.

## Contrato de execução

API estável da engine:

- `run(thread_id, query)` para iniciar;
- `resume(thread_id, user_response)` para retomar HITL;
- `get_insight(...)` mantido como API base.

Critérios mínimos:

- fluxo completo do grafo;
- HITL on/off;
- retomada por `thread_id`;
- gravacao de metricas em CSV;
- geracao opcional de graficos quando a visualizacao for relevante.

## Contrato HITL (perguntas)

- `pergunta_original`: pergunta inicial da thread, imutavel apos o primeiro set.
- `pergunta_atual`: pergunta corrente e fonte de verdade para o fluxo.
- Em HITL, se a resposta do usuario for classificada como "nova pergunta",
  o sistema atualiza `pergunta_atual` e reinicia o ciclo, sem alterar a original.

## Execução

```bash
# adaptador local
python main.py --db-path data/olist_relational.db --hitl on "Quantos pedidos existem no banco?"

# biblioteca instalada (entrypoint)
text-to-insight --db-path data/olist_relational.db --hitl off "Quais categorias vendem mais?"

# com modelo OpenAI
set -a && source .env && set +a
python main.py --db-path data/olist_relational.db --hitl off --model openai/gpt-4o-mini --api-key-env OPENAI_API_KEY "Quantos pedidos existem?"

# bancos sem FK declarada (ex.: Spider 2): inferência de FKs pelo nome das colunas
python main.py --db-path caminho/do/banco.sqlite --hitl off --infer-fks on "Qual a soma dos resultados?"

# PostgreSQL/MySQL via URL (ou variável DATABASE_URL)
python main.py --db-url postgresql://usuario:senha@host:5432/banco --hitl off "Quantos clientes existem?"
```

O resultado é exibido no terminal em formato tabular sob o bloco `RESULTADO:`, junto com SQL gerada, status de execução e resposta natural.


### Geração de gráficos

Quando o roteador de gráficos decide que a visualização é útil, o sistema gera um gráfico a partir do CSV de resultados.

- CSV completo: `results/`
- Gráficos salvos: `graphs/`

Nos benchmarks Spider, use `--with-graphs` para ativar geração de gráficos e salvamento de CSV.

## Estrutura relevante

```text
text_to_insight/
    InsightEngine.py
    cli.py
    runtime.py
    graph.py
    state.py
    cache.py
    model_selection/
    nodes/
    retriever/
    routers/
scripts/          # avaliação em benchmarks (Spider, Spider 2.0, BIRD)
src/              # componentes dos benchmarks (loaders, executores, métricas)
docs/
tests/
    test_componentes.py
    test_nodes.py
    test_integracao.py
    test_main_engine_integracao.py
    test_biblioteca_integracao.py
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

# integracao do pacote como biblioteca (sem chamadas de API, testa HITL e run/resume)
pytest tests/test_biblioteca_integracao.py -v -s
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

#### Quando usar `--record-mode=rewrite`

Use `rewrite` quando quiser **substituir completamente** as cassetes existentes, por exemplo, após uma mudança grande de prompt que torna as respostas gravadas incompatíveis com os testes atuais.

```bash
pytest tests/test_nodes.py tests/test_integracao.py -v -s --record-mode=rewrite
```

> ⚠️ **Atenção:** `rewrite` apaga e regrava **todas** as cassetes do escopo, mesmo as que ainda funcionariam.
> Use `new_episodes` na dúvida, ele só grava o que está faltando e preserva o restante.

Resumo dos modos disponíveis:

| Modo | O que faz | Quando usar |
|---|---|---|
| `new_episodes` | Grava só chamadas sem cassete; preserva as existentes | Fluxo normal: adicionou testes ou mudou um nó |
| `none` | Nunca chama a API; falha se faltar cassete | CI e revisão local para garantir determinismo |
| `rewrite` | Apaga e regrava todas as cassetes do escopo | Mudança grande de prompt que invalidou as respostas antigas |



### Escolha de provider/modelo para gravar novas cassetes

Os testes de API real usam a fixture compartilhada em `tests/conftest.py` para resolver automaticamente o provider e o modelo. A ordem de prioridade e:

1. `TEXT_TO_INSIGHT_TEST_MODEL` define o modelo explicitamente.
2. Se `TEXT_TO_INSIGHT_TEST_PROVIDER` estiver definido, ele força o provider (`google` ou `openai`).
3. Se o modelo não deixar o provider obvio, o provider precisa ser informado.
4. Sem overrides, o sistema tenta `GOOGLE_API_KEY` e depois `OPENAI_API_KEY`.

Resumo do funcionamento local:

- `pytest tests/test_nodes.py tests/test_integracao.py -v -s --record-mode=none` faz replay apenas; se faltar cassette, o teste falha.
- `pytest tests/test_nodes.py tests/test_integracao.py -v -s --record-mode=new_episodes` grava só as chamadas que ainda nao existem no YAML.
- `pytest tests/test_nodes.py tests/test_integracao.py -v -s --record-mode=rewrite` regrava tudo do escopo.
- o fixture monta o nome do cassette a partir do teste e adiciona sufixo quando o provider/modelo sai do padrao Gemini; por isso `gpt-4o-mini` usa cassettes com `__openai-gpt-4o-mini` e `gemini-2.5-flash` usa os nomes sem sufixo.

Para gravar novas cassetes de forma previsível, escolha explicitamente provider e modelo:

```bash
TEXT_TO_INSIGHT_TEST_PROVIDER=google \
TEXT_TO_INSIGHT_TEST_MODEL=gemini-2.5-flash \
pytest tests/test_nodes.py tests/test_integracao.py -v -s --record-mode=new_episodes
```

```bash
TEXT_TO_INSIGHT_TEST_PROVIDER=openai \
TEXT_TO_INSIGHT_TEST_MODEL=gpt-4o-mini \
pytest tests/test_nodes.py tests/test_integracao.py -v -s --record-mode=new_episodes
```

Se voce definir as variaveis em linhas separadas no shell, use `export` para que o `pytest` e os processos filhos enxerguem os valores. Sem `export`, a atribuição fica só no shell atual e a suíte de testes não herda a configuração.

Uso recomendado:

```bash
export TEXT_TO_INSIGHT_TEST_PROVIDER=openai
export TEXT_TO_INSIGHT_TEST_MODEL=gpt-4o-mini
pytest tests/test_nodes.py tests/test_integracao.py -v -s --record-mode=new_episodes
```

Tambem funciona em uma linha só, sem `export`:

```bash
TEXT_TO_INSIGHT_TEST_PROVIDER=openai TEXT_TO_INSIGHT_TEST_MODEL=gpt-4o-mini \
pytest tests/test_nodes.py tests/test_integracao.py -v -s --record-mode=new_episodes
```

Se o provider/modelo sair do padrão Gemini, os nomes dos cassetes ganham sufixo automático para evitar colisão com as cassetes existentes.

### Drift provider/modelo (opcional) para verificar se API real ainda responde conforme esperado:

```bash
pytest tests/test_real_api_smoke.py -v -s -m real_api
```

### Benchmark Spider (1.0)

Requer o dataset Spider em `spider_data/` (com `dev.json` e `database/`).

```bash
python scripts/test_spider_eval.py --sample-size 10 --seed 42 --data-dir spider_data
```

Opções úteis: `--db-filter`, `--question-filter`, `--model`, `--with-graphs`, `--report-dir`.

### Benchmark Spider 2.0 Lite

Requer o dataset Spider 2.0 Lite em `spider2-lite/` e os bancos SQLite em
`spider2-lite/resource/databases/spider2-localdb`.

```bash
python scripts/test_spider2_eval.py --sample-size 10 --seed 42 \
    --data-dir spider2-lite \
    --sqlite-dir spider2-lite/resource/databases/spider2-localdb
```

Opções úteis: `--db-filter`, `--question-filter`, `--model`, `--with-graphs`, `--report-dir`.

### Benchmark BIRD (Big Bench for Large-scale Database Grounded Text-to-SQL Evaluation)

Requer o dataset BIRD em `data/bird/` (baixe e extraia via `python scripts/setup_bird_data.py --mini`).

```bash
# 1) Testes unitários do harness BIRD (< 1s, sem consumo de API)
pytest tests/test_bird_eval.py -v

# 2) Smoke test rápido sem custo de LLM (dry-run)
python scripts/test_bird_eval.py --sample-size 3 --dry-run

# 3) Avaliação real com modelo configurado no .env
python scripts/test_bird_eval.py --sample-size 20 --seed 42 --rag on

# 4) Experimento de ablação: SchemaGraphRAG (Steiner Tree)
python scripts/test_bird_eval.py --sample-size 50 --seed 123 --rag on --output reports/bird/rag_on.csv
python scripts/test_bird_eval.py --sample-size 50 --seed 123 --rag off --output reports/bird/rag_off.csv
```

Opções úteis: `--db-filter`, `--difficulty` (`simple`, `moderate`, `challenging`), `--stratify`, `--use-evidence` (`on`/`off`), `--dry-run`, `--output`.
Consulte o manual operacional completo em [BENCHMARK_BIRD.md](BENCHMARK_BIRD.md). Spider 1.0 e 2.0: [BENCHMARK_SPIDER.md](BENCHMARK_SPIDER.md).


## CI hibrida

Arquivo: `.github/workflows/ci.yml`

- job padrão determinístico em PR/push roda em matriz com as duas combinações de cassette: `openai / gpt-4o-mini` e `google / gemini-2.5-flash`, sempre em `--record-mode=none`;
- job manual `record-vcr-cassettes` em `workflow_dispatch` tambem roda as duas combinações e pode gravar/atualizar os dois conjuntos de cassetes;
- job opcional real API em `workflow_dispatch` e `schedule`.

## Build e distribuição

Esse teste garante que o pacote pode ser construído e instalado a partir do wheel, simulando o processo de distribuição real a um usuário novo instalando o pacote pela primeira vez. Se algo estiver faltando no wheel (como arquivos, dependências ou configurações), esse teste deve falhar, indicando que o pacote não está pronto para distribuição.

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
pip install -e ".[dev,postgres,mysql]"
```

`No module named 'psycopg2'` / `'pymysql'`: instale o extra do banco, ex.: `pip install "text-to-insight[postgres]"`.

`429 RESOURCE_EXHAUSTED`:
- aguardar reset de quota;
- preferir testes com VCR no dia a dia.

## Configuração do .env (variáveis de ambiente)
- `GOOGLE_API_KEY`: chave de API para Google Gemini (se usar modelo Gemini).
- `OPENAI_API_KEY`: chave de API para OpenAI (se usar modelo OpenAI).
- `DATABASE_URL`: URL do banco usada pelo CLI quando `--db-url` não é informado.
