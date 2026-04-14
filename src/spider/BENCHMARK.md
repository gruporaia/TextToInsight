# Spider Benchmark - Documentação Técnica

## Visão Geral

Módulo de avaliação automatizada do agente Text-to-Insight contra o **Spider Dataset** (1.034 perguntas em SQL, 20 bancos diferentes). Rastreia cada tentativa individualmente gerando métricas de qualidade.

## Arquitetura

```
scripts/test_spider_eval.py (Orquestrador)
    ├── data_loader.py       (Carrega dev.json)
    ├── query_executor.py    (Executa SQL)
    ├── metrics.py           (Calcula similarity/match)
    └── csv_reporter.py      (Salva results e resumo)
```

## Módulos

### `src/spider/data_loader.py`
Gerencia dataset Spider (1.034 exemplos JSON).

**Funções principais:**
- `load_spider_dev_examples(data_dir)` → Lê dev.json, retorna lista de dicts {question, query, db_id}
- `sample_examples(examples, sample_size, seed)` → Amostra reproducível com seed
- `filter_by_db_id(examples, db_id)` → Filtra pergunta de um único banco (ex: concert_singer)
- `get_unique_db_ids(examples)` → Retorna 20 db_ids únicos

---

### `src/spider/query_executor.py`
Executa queries SQL contra bancos SQLite do Spider.

**Classe: `SpiderQueryExecutor`**
- `execute_query(db_id, sql)` → Executa query, retorna {success, results, row_count, error, time_ms}
- `get_db_path(db_id)` → Resolve caminho `/data/spider_data/spider_data/database/{db_id}/{db_id}.sqlite`
- Usa SQLite em modo **read-only** (`?mode=ro&uri=true`)

**Por que isolado:** Abstrai detalhes de banco de dados, facilita testar com outro driver se necessário.

---

### `src/spider/metrics.py`
Compara queries geradas vs. queries ouro (baseline).

**Funções principais:**
- `sql_similarity_score(sql1, sql2)` → Valor 0-1 usando difflib.SequenceMatcher (normaliza UPPER/whitespace/comments)
- `results_exact_match(results1, results2)` → bool, compara linhas executadas normalizando tipos (NULL → None)
- `normalize_sql(sql)` → Transforma para comparação (rm whitespace, comments, UPPER)
- `build_comparison_row(id_exemplo, tentativa_numero, ...)` → Monta dict com 12 colunas para CSV

**Por que isolado:** Reutilizável em testes/análises extras, lógica de comparação centralizada.

---

### `src/spider/csv_reporter.py`
Gerencia saída CSV e estatísticas agregadas.

**Classe: `CSVReporter`**
- `__init__(filepath)` → Cria CSV com 12 headers (id_exemplo, tentativa_numero, db_id, pergunta, query_ouro, query_agente, tempo_ms, veredito, feedback, similarity_score, resultado_match, erro)
- `append_row(row_dict)` → Adiciona 1 linha por tentativa
- `generate_summary(rows)` → Retorna dict {total_perguntas, taxa_aprovacao, similarity_media, tentativas_media, ...}
- `generate_timestamped_filename(prefix)` → Returns `spider_eval_2026-04-11_15-30-42.csv`

**Por que isolado:** Padrão CSV fixo, resumo automático, reutilizável em análises.

---

### `scripts/test_spider_eval.py`
**O maestro do pipeline.** Orquestra todo o benchmark.

**Fluxo:**
1. **Parse args** → sample-size, seed, db-filter, output, max-attempts, data-dir
2. **Load dados** → data_loader carrega e filtra exemplos
3. **Para cada pergunta:**
   - Executar query ouro (baseline via query_executor)
   - Invocar grafo LangGraph com estado inicial
   - **Rastrear stream()** do grafo acumulando estado (`full_estado.update()`)
   - Quando nó crítico retorna:
     - Extrair sql_gerada, veredito, feedback do full_estado
     - Executar query_agente, calcular similarity/match (via metrics)
     - Salvar linha no CSV (via csv_reporter)
     - Se aprovado: next pergunta; se reprovado & tentativas < max: retry automático
4. **Gerar resumo** → reporter calcula estatísticas finais

**Responsabilidade única:** Não calcula metrics, não executa SQL, não salva CSV. Coordena os módulos.

**Configuração o grafo:**
```python
grafo.stream(estado_inicial, config={"recursion_limit": 30})
```
- `recursion_limit=30`: Permite planejador iterar até ~3 vezes sem erro de recursão

**State accumulation pattern:**
```python
full_estado = estado_inicial.copy()
for output in grafo.stream(...):
    for node_name, mudancas in output.items():
        full_estado.update(mudancas)  # Acumula deltas em estado completo
```
Necessário porque `stream()` retorna deltas por nó, não estado total.

---

## Uso

```bash
# Teste básico: 10 perguntas
python scripts/test_spider_eval.py

# Parametrizado
python scripts/test_spider_eval.py \
  --sample-size 50 \
  --seed 42 \
  --db-filter concert_singer \
  --output reports/eval.csv \
  --max-attempts 3
```

## Saída

**CSV:**
- 1 linha = 1 tentativa (mesma pergunta pode ter 1-3 linhas)
- 12 colunas: id_exemplo, tentativa_numero, db_id, pergunta, query_ouro, query_agente, tempo_ms, veredito, feedback, similarity_score, resultado_match, erro

**Resumo:**
- Total de perguntas avaliadas
- Taxa de aprovação (% respostas corretas)
- Taxa de sucesso 1ª tentativa (agente acerta de primeira?)
- Tentativas médias por pergunta
- Similarity score médio
- Tempo médio por tentativa

## Exemplo de Output

```
Total de perguntas: 50
Total de tentativas: 63
Perguntas aprovadas: 45
Taxa de aprovação: 90.0%
Taxa de sucesso na 1ª tentativa: 72.0%
Tentativas médias: 1.26
Similarity score médio: 0.953
Tempo médio: 12345 ms
✅ CSV salvo em: reports/spider_eval_2026-04-11_15-30-42.csv
```

## Estrutura do Estado (EstadoTextToInsight)

Usado por todos os módulos, definido em `src/state.py`:
```python
{
    "pergunta_usuario": str,
    "db_path": str,
    "contexto_schema": str,
    "sql_gerada": str,
    "linhas_resultado_preview": list,
    "total_linhas_resultado": int,
    "erro_execucao": str,
    "feedback_critico": str,
    "status": str,  # "aprovado", "reprovado", "erro"
    "tentativas_loop": int,
}
```

## Fluxo de Debug

| Erro | Provável causa | Debug |
|------|----------------|-------|
| `FileNotFoundError: dev.json` | Dataset não baixado | `wget` Spider dataset em data/spider_data/spider_data/ |
| `sqlite3.OperationalError: database is locked` | Mode não read-only | Verificar `query_executor.py`, deve ter `?mode=ro&uri=true` |
| `RecursionLimitError: Recursion limit of 30` | Grafo entrando em loop infinito | Aumentar `recursion_limit` ou debugar nó que não para |
| CSV vazio | Estado não acumulando | Verificar `full_estado.update()` no script (state accumulation pattern) |

