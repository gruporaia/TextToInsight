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
pip install -e .
```

- `python -m venv .venv`: cria um ambiente isolado.
- `source .venv/bin/activate`: ativa esse ambiente.
- `pip install -r requirements.txt`: instala dependências de runtime e ferramentas de teste usadas no projeto.
- `pip install -e .`: instala o TextToInsight em modo editável usando os metadados do `pyproject.toml`.

### Configuração

```bash
echo "GOOGLE_API_KEY=sua_chave" > .env
```

Banco SQLite esperado por padrão: `data/olist_relational.db`.

###  Verificação Rápida (Smoke) de import

```bash
python -c "from text_to_insight import InsightEngine, Graph; print('OK')"
```

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
python main.py --hitl on "Quantos pedidos existem no banco?"

# biblioteca instalada (entrypoint)
text-to-insight --hitl off "Quais categorias vendem mais?"

# com modelo OpenAI
set -a && source .env && set +a
python main.py --hitl off --model gpt-4o-mini --api-key-env OPENAI_API_KEY "Quantos pedidos existem?"

# testes locais com bancos do Spider 2 (SQLite sem FK explícita e PRAGMA nativo)
python main.py --hitl off --infer-fks on --use-schemacrawler off "Qual a soma dos resultados?"
```

O resultado é exibido no terminal em formato tabular sob o bloco `RESULTADO:`, junto com SQL gerada, feedback do crítico e resposta natural.

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
pip install -e .
```

`429 RESOURCE_EXHAUSTED`:
- aguardar reset de quota;
- preferir testes com VCR no dia a dia.

## Configuração do .env (variáveis de ambiente)
- `GOOGLE_API_KEY`: chave de API para Google Gemini (se usar modelo Gemini).
- `OPENAI_API_KEY`: chave de API para OpenAI (se usar modelo OpenAI).
- `SCHEMACRAWLER_BIN`: caminho para o binário do SchemaCrawler (se usar este recurso).
