# Arquitetura - Text-to-Insight

## Visao geral

O sistema combina:

- um grafo LangGraph com 8 nos base (alem dos nos opcionais de RAG e exploracao, inclui salvamento de CSV e geracao de graficos);
- uma camada de runtime compartilhada (`text_to_insight/runtime.py`);
- duas interfaces de entrada: biblioteca (`InsightEngine`) e CLI (`main.py` -> `text_to_insight/cli.py`).
- um modulo de benchmark para Spider 1.0, Spider 2.0 Lite e BIRD (`scripts/` + `src/spider/` + `src/bird/`).

Fluxo principal:

```text
START
  -> Planejador
  -> Schema / Retriever (quando necessario)
  -> Agente de Codigo
  -> Executor (Sandbox)
  -> Salvar CSV
  -> Roteador Grafico
  -> Gerador Grafico (quando aplicavel)
  -> Resposta
END
```

Loop de auto-correcao em caso de erro SQL no Sandbox:
```text
Executor (Sandbox) --[exec_erro e tentativas < 3]--> Planejador -> Agente de Codigo
```


Fluxo alternativo HITL:

```text
Planejador -> Espera Humana (interrupt_before) -> Planejador
```

## Camadas

### 1) Orquestração do grafo

Arquivo: `text_to_insight/graph.py`

Responsavel por:

- criar os nós;
- definir arestas fixas e condicionais;
- compilar com `MemorySaver`;
- interromper antes de `espera_humana` para suportar HITL.

### 2) Runtime compartilhado

Arquivo: `text_to_insight/runtime.py`

Responsavel por:

- construir estado inicial;
- executar loop de stream até fim/pausa;
- tratar bloqueio HITL quando `hitl=False`;
- registrar resposta humana na thread;
- persistir metricas em CSV;
- exibir resultado final em formato padrão.

Funções principais:

- `_montar_saida_resultado_terminal(resultado)`: template reutilizavel que transforma linhas brutas da query em texto formatado para o terminal, usando `tabulate` para renderizar a tabela. Suporta fallback para amostra quando resultado completo nao estiver disponivel.
- `salvar_resultado_csv(resultado, pasta)`: exporta o resultado completo em CSV com timestamp em `results/`.
- `exibir_resultado_console(resultado)`: orquestra a exibicao completa do resultado: SQL, saida da execucao, tabela formatada, feedback e resposta natural.

### 3) API publica da biblioteca

Arquivos:

- `text_to_insight/InsightEngine.py`
- `text_to_insight/__init__.py`

Contrato estavel:

- `run(thread_id, query)`;
- `resume(thread_id, user_response)`;
- `get_insight(...)` como base de compatibilidade.

API publica exportada:

- `InsightEngine`
- `Graph`
- `EstadoTextToInsight`

### 4) Adaptador CLI

Arquivos:

- `text_to_insight/cli.py`
- `main.py`

`main.py` e um wrapper fino da CLI do pacote.

## Nos do grafo

### Planejador (`text_to_insight/nodes/planner.py`)

- sem schema: `aguardando_schema` (deterministico)
- com schema: decide entre `pronto_codificacao`, `revisando_estrategia` ou `necessita_ajuda`
- opcionalmente ativa HITL (`espera_humana=True`)

### Espera Humana (`text_to_insight/graph.py`)

- no estrutural sem transformacao de estado
- usado como ponto de interrupcao para retomada por `thread_id`

### Schema (`text_to_insight/nodes/schema.py`)

- introspeccao SQLite real em modo read-only
- extrai tabelas, colunas e relacionamentos

### Agente de Codigo (`text_to_insight/nodes/code_agent/code_agent.py`)

- gera SQL com LLM a partir de pergunta + schema + feedback

### Executor (`text_to_insight/nodes/sandbox.py`)

- valida SQL e executa via `code_sql.py`
- devolve preview + total de linhas completas
- em caso de erro de sintaxe/execucao, define `status: exec_erro` e captura `erro_execucao`

### Resposta (`text_to_insight/nodes/response.py`)

- gera resposta natural final ao usuario a partir do resultado obtido e define status aprovado

### Salvar CSV (`text_to_insight/nodes/csv_saver.py`)

- salva o resultado completo em `results/` e registra `caminho_csv_resultado`

### Gerador de Graficos (`text_to_insight/nodes/graph_generator.py`)

- gera codigo matplotlib via LLM, executa em subprocesso e salva imagem em `graphs/`
- registra `grafico_gerado` e `caminho_grafico` no estado

## Roteadores

Arquivo: `text_to_insight/routers/edges.py`

- `roteador_sandbox`: inspeciona o resultado do executor:
  - se `exec_ok`: prossegue para `salvar_csv` (ou `resposta`);
  - se `exec_erro` e `tentativas < 3`: roteia de volta para o `planejador` com o erro para correcao pelo agente de codigo;
  - se `tentativas >= 3`: encerra o ciclo enviando para `salvar_csv` / `resposta`.
- `roteador_planejador`: decide entre schema/retriever, codificacao, HITL ou fim
- `roteador_grafico`: decide entre gerar grafico ou ir direto para resposta

## Estado compartilhado

Arquivo: `text_to_insight/state.py`

Campos obrigatorios:

- `pergunta_original`
- `pergunta_atual`
- `db_path`

Campos principais do fluxo:

- `contexto_schema`, `contexto_rag_schema`, `sql_gerada`, `linhas_resultado_preview`, `linhas_resultado_completo`, `total_linhas_resultado`
- `erro_execucao`, `saida_terminal`, `resposta_natural`
- `status`, `tentativas_loop`, `historico_conversa`, `espera_humana`, `pergunta_ao_usuario`, `historico_tentativas`
- `caminho_csv_resultado`, `grafico_gerado`, `caminho_grafico`
- telemetria: `tokens_input`, `tokens_output`, `tokens_total`

## Benchmarks

- BIRD Benchmark: `scripts/test_bird_eval.py` + `src/bird/` (loader, executor read-only, metrics, csv_reporter, BENCHMARK.md)
- Spider 1.0: `scripts/test_spider_eval.py`
- Spider 2.0 Lite: `scripts/test_spider2_eval.py`
- Componentes Spider: `src/spider/` (loader, executor, metrics, csv_reporter, analise_empirica, BENCHMARK.md)


## HITL e perguntas

- `pergunta_original` e a pergunta inicial da thread (imutavel apos o primeiro set).
- `pergunta_atual` e a pergunta corrente e fonte de verdade para todo o fluxo.
- Em HITL, se a resposta do usuario for classificada como "nova pergunta",
  o sistema atualiza `pergunta_atual` e reinicia o ciclo (sem alterar a original).

## Status operacionais

No runtime/engine podem aparecer:

- `AWAITING_USER` (pausa HITL aguardando resposta);
- `bloqueado_hitl` (quando HITL esta desligado e o planejamento pede input humano).

## Politica VCR

Os testes marcados com `@pytest.mark.vcr` usam cassetes em `tests/cassettes/`.

- no fluxo padrao de PR/CI: `--record-mode=none` (somente replay deterministico);
- para gravar ou atualizar cassetes: `--record-mode=new_episodes`;
- apos gravacao: execute novamente com `--record-mode=none` para validar reproducibilidade.

Na CI existe um job manual `record-vcr-cassettes` (workflow_dispatch) para gravação/atualização controlada.