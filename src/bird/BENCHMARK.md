# Manual de Operação e Runbook de Experimentos BIRD - TextToInsight

Este manual técnico fornece o guia operacional definitivo para reproduzir os benchmarks do **BIRD (Big Bench for Large-scale Database Grounded Text-to-SQL Evaluation)** no repositório `TextToInsight`.

---

## 1. Visão Geral da Arquitetura de Avaliação BIRD

A suíte de avaliação BIRD foi desenvolvida com arquitetura modular desacoplada:

```mermaid
flowchart TD
    Setup["scripts/setup_bird_data.py"] -->|"Baixa e Normaliza"| Data["data/bird (dev.json + dev_databases)"]
    Data --> Loader["src/bird/data_loader.py"]
    Loader --> CLI["scripts/test_bird_eval.py (Orquestrador)"]
    CLI -->|"Cache de Grafo"| Engine["InsightEngine (Planner + Code Agent + Sandbox)"]
    Engine -->|"SQL Candidato"| Executor["src/bird/query_executor.py (SQLite Read-Only + Math)"]
    Data -->|"SQL Ouro"| Executor
    Executor -->|"Resultados"| Metrics["src/bird/metrics.py (EX Multiset / Counter)"]
    Metrics -->|"Linhas Canônicas"| Reporter["src/bird/csv_reporter.py"]
    Reporter -->|"16 colunas"| CSV["reports/bird/bird_eval_*.csv"]
    Reporter -->|"Resumo e Tabelas"| MD["reports/bird/bird_eval_*.md"]
    Reporter -->|"Formato Oficial"| JSON["reports/bird/bird_eval_*.json (predict_dev.json)"]
```

---

## 2. Preparação do Ambiente e Dados

### 2.1. Ativação do Ambiente Virtual
Certifique-se de que a virtualenv está ativada:
```bash
source .venv/bin/activate
```

### 2.2. Download e Extração do Dataset BIRD
O script automatizado `scripts/setup_bird_data.py` possui dependência zero da Standard Library do Python.
Para ambiente de desenvolvimento e testes locais, utilize o modo `--mini` (~800 MB comprimido):
```bash
python scripts/setup_bird_data.py --mini
```
*O que este comando faz:*
- Faz download do arquivo `minidev.zip` oficial do BIRD.
- Descompacta e normaliza o layout de pastas para `data/bird/`.
- Padroniza o arquivo de perguntas para `data/bird/dev.json` (500 perguntas dev).
- Extrai todos os 11 bancos de dados SQLite para `data/bird/dev_databases/<db_id>/<db_id>.sqlite`.

---

## 3. Testes Automatizados de Sanidade (Pytest)

Antes de rodar qualquer modelo ou gastar tokens de API, execute a suíte de testes unitários rápidos:
```bash
pytest tests/test_bird_eval.py -v
```
- **Tempo estimado:** ~0.5 segundos.
- **Isolamento:** Usa fixture sintética em diretório temporário (`tmp_path`). Não depende da internet e não consome API.
- **Cobertura:** 10 testes cobrindo `data_loader`, `query_executor` (read-only, math, timeout), `metrics` (EX, ordenação, multiset), `csv_reporter` e teste de integração da CLI.

---

## 4. Smoke Test Rápido com Custo Zero (`--dry-run`)

Para verificar que todo o encadeamento de arquivos (loader -> executor -> métricas -> CSV -> JSON -> Markdown) está funcionando sem gastar centavos de LLM:
```bash
python scripts/test_bird_eval.py --sample-size 3 --dry-run
```
Outputs gerados em uma subpasta dedicada `reports/bird/run_<timestamp>/`:
1. `bird_eval_<timestamp>.csv` (log completo de cada tentativa)
2. `bird_eval_<timestamp>.md` (relatório executivo formatado)
3. `bird_eval_<timestamp>.json` (predições oficiais para o avaliador BIRD)

---

## 5. Smoke Test Real com LLM (3 a 5 perguntas)

Executa o fluxo real com o modelo configurado no `.env` (ex: `gpt-4o-mini` ou `gemini-1.5-flash`):
```bash
python scripts/test_bird_eval.py --sample-size 3 --seed 42
```

---

## 6. Protocolos e Runbooks de Experimentos Científicos

### 🔬 Experimento 1: Validação do SchemaGraphRAG (Steiner Tree)
**Objetivo Científico:** Comprovar a tese principal do projeto: o `SchemaGraphRAG` reduz o consumo de tokens de contexto (especialmente em bancos com muitas tabelas/colunas) mantendo ou aumentando a Execution Accuracy (EX).

```bash
# Rodada A: Com GraphRAG ATIVADO (Poda de tabelas via Steiner Tree)
python scripts/test_bird_eval.py \
  --sample-size 50 \
  --seed 123 \
  --rag on \
  --output reports/bird/exp1_rag_on.csv

# Rodada B: Com GraphRAG DESATIVADO (Schema completo injetado no prompt)
python scripts/test_bird_eval.py \
  --sample-size 50 \
  --seed 123 \
  --rag off \
  --output reports/bird/exp1_rag_off.csv
```

**Análise Comparativa dos Resultados:**
- Compare `tokens_input` médio por pergunta entre `exp1_rag_on.md` e `exp1_rag_off.md`.
- Verifique a coluna `Challenging` na tabela de estratificação por dificuldade (onde o ganho do RAG deve ser mais acentuado).
- Confirme que a métrica `Execution Accuracy (EX Final)` se manteve equivalente ou superior.

---

### 🔬 Experimento 2: Impacto das Evidências de Negócio (`--use-evidence`)
**Objetivo Científico:** Mensurar a importância das regras de negócio e fórmulas fornecidas no campo `evidence` do BIRD para a precisão do agente Text-to-SQL.

```bash
# Rodada A: COM evidência (Prompt com Hint)
python scripts/test_bird_eval.py \
  --sample-size 50 \
  --seed 123 \
  --use-evidence on \
  --output reports/bird/exp2_evidence_on.csv

# Rodada B: SEM evidência (Prompt cego de fórmulas de negócio)
python scripts/test_bird_eval.py \
  --sample-size 50 \
  --seed 123 \
  --use-evidence off \
  --output reports/bird/exp2_evidence_off.csv
```

---

### 🔬 Experimento 3: Avaliação Focada por Domínio de Banco (`--db-filter`)
Útil para depurar problemas específicos em bancos complexos com muitos esquemas:
```bash
# Exemplo 1: Avaliar apenas perguntas do banco financeiro
python scripts/test_bird_eval.py --db-filter financial --sample-size 20

# Exemplo 2: Avaliar apenas perguntas do banco escolar
python scripts/test_bird_eval.py --db-filter california_schools --sample-size 15
```

---

### 🔬 Experimento 4: Amostragem Estratificada por Dificuldade (`--stratify`)
Para garantir que amostras menores (ex: 30 perguntas) representem fielmente as proporções de `simple`, `moderate` e `challenging` do dataset BIRD:
```bash
python scripts/test_bird_eval.py --sample-size 30 --stratify --seed 42
```

---

## 7. Dicionário Completo de Métricas do BIRD

As métricas calculadas em [`src/bird/metrics.py`](file:///home/gabyl/projetos/raia/TextToInsight/src/bird/metrics.py) e agregadas em [`src/bird/csv_reporter.py`](file:///home/gabyl/projetos/raia/TextToInsight/src/bird/csv_reporter.py) fornecem uma visão multidimensional de acurácia, robustez e eficiência computacional:

| Métrica | Nome Técnico | O que Mede | Como é Calculada / Critério |
|---|---|---|---|
| **Execution Accuracy (EX)** | `execution_match` / `ex_final_rate` | Acurácia semântica real da resposta. | Executa a SQL gerada pelo agente e a SQL ouro no SQLite. Normaliza tipos e compara o **multiset de tuplas** (`Counter`). Retorna `True` se o resultado de dados for 100% idêntico, independente da sintaxe SQL utilizada. |
| **Pass@1 (First Attempt EX)** | `ex_pass_at_1_rate` | Precisão "zero-shot" na 1ª tentativa. | Percentual de perguntas que obtiveram `execution_match == True` logo na tentativa 1, antes de qualquer ciclo de feedback do sandbox. |
| **Sandbox Success Rate** | `sandbox_success_rate` | Robustez sintática e integridade do schema. | Percentual de queries geradas que executaram no SQLite sem qualquer erro de sintaxe, coluna inexistente ou tabela inválida (`status == 'exec_ok'` e erro vazio). |
| **Self-Correction Recovery Rate** | `self_correction_rate` | Eficiência do loop de auto-correção. | Dentre as perguntas cuja 1ª tentativa falhou (erro SQL ou divergência de dados), mede quantas foram recuperadas e acertaram na tentativa final graças ao feedback do erro devolvido pelo Sandbox ao Planejador. |
| **SQL Text Similarity** | `similarity_score` | Proximidade textual sintática (0.0 a 1.0). | Similaridade via `difflib.SequenceMatcher` após normalizar espaços, quebras de linha, comentários e maiúsculas/minúsculas. Útil para diagnóstico complementar de formatação. |
| **Tokens Input / Output** | `tokens_input`, `tokens_output`, `tokens_total` | Custo computacional e contexto. | Extraído diretamente da chamada do LLM. Essencial para avaliar a redução de tokens proporcionada pelo `SchemaGraphRAG` (Steiner Tree). |
| **Latência por Pergunta** | `time_ms` / `avg_time_ms_per_question` | Tempo de resposta fim-a-fim. | Medição em milissegundos do ciclo completo (planejador + agente de código + sandbox). |

---

## 8. Como Avaliar e Diagnosticar os Resultados em `reports/bird/`

Cada execução cria automaticamente um diretório isolado com carimbo de data/hora:
```text
reports/bird/run_<YYYY-MM-DD_HH-MM-SS>/
├── bird_eval_<timestamp>.md    <-- 1º: Relatório Executivo (visão macro)
├── bird_eval_<timestamp>.csv   <-- 2º: Log Detalhado (autópsia cirúrgica linha a linha)
└── bird_eval_<timestamp>.json  <-- 3º: Predições canônicas oficiais (predict_dev.json)
```

### 8.1. Passo 1: Leitura do Relatório Executivo (`.md`)
Abra o arquivo `.md` para checar:
1. **Configuração da Execução:** Modelo utilizado, status do RAG, do CoT e das Evidências.
2. **Resumo Executivo:** EX Final global, Pass@1, Taxa de Sucesso no Sandbox e Taxa de Recuperação do Self-Correction.
3. **Estratificação por Dificuldade:**
   - `Simple`: O agente deve atingir alta acurácia (> 60-70%).
   - `Moderate`: Avalia JOINs intermediários e agregações (`GROUP BY`).
   - `Challenging`: Testa JOINs múltiplos, subqueries complexas ou CTEs.
4. **Tabela de Recursos:** Média de tokens por pergunta e tempo médio de resposta.

---

### 8.2. Passo 2: Diagnóstico Cirúrgico no CSV (`.csv`)
O arquivo CSV contém **16 colunas oficiais** detalhando cada tentativa de cada pergunta:

| Coluna | Descrição para Análise |
|---|---|
| `question_id` | Identificador único da pergunta no benchmark BIRD. |
| `attempt_number` | Número da tentativa (`1`, `2` ou `3`). Se for `> 1`, passou pelo loop de auto-correção. |
| `db_id` | Nome do banco de dados (ex: `financial`, `california_schools`). |
| `difficulty` | Grau de dificuldade (`simple`, `moderate`, `challenging`). |
| `question` | Pergunta original em linguagem natural feita pelo usuário. |
| `evidence` | Dica de negócio ou fórmula associada (quando `--use-evidence on`). |
| `gold_sql` | Consulta SQL oficial de referência (gabarito). |
| `agent_sql` | Consulta SQL gerada pelo agente TextToInsight. |
| `time_ms` | Latência da tentativa em milissegundos. |
| `execution_status` | Status do sandbox: `exec_ok`, `exec_erro`, `aprovado`. |
| `similarity_score` | Similaridade sintática textual entre `agent_sql` e `gold_sql` (0 a 1). |
| `execution_match` | `True` se o resultado de dados bateu exatamente com o gabarito; `False` caso contrário. |
| `error` | Mensagem de erro capturada pelo SQLite/Sandbox quando a query quebrou. |
| `tokens_input` | Quantidade de tokens de entrada (pergunta + schema + histórico). |
| `tokens_output` | Quantidade de tokens gerados (raciocínio CoT + SQL). |
| `tokens_total` | Consumo total de tokens na tentativa. |

#### Filtros recomendados para análise (via Pandas, Excel ou terminal):
- **Onde o agente errou?**
  Filtrar `execution_match == False` na última tentativa de cada `question_id`.
- **Houve erro de sintaxe/schema?**
  Filtrar `execution_status == 'exec_erro'`. Analisar a coluna `error` para checar se o agente inventou colunas ou errou o dialeto SQLite.
- **A auto-correção funcionou?**
  Filtrar perguntas com múltiplas tentativas (`attempt_number > 1`) e verificar se a tentativa final conseguiu `execution_match == True`.

---

### 8.3. Matriz de Decisão e Engenharia de Correção

| Padrão Identificado nos Resultados | Causa Raiz Típica | Ação de Engenharia Recomendada |
|---|---|---|
| **Sandbox Success alto (~95%), mas EX baixo (<50%)** | O modelo gera SQL sintaticamente válido, mas erra a lógica de negócio, cláusulas `WHERE` ou agregações. | Ativar `--use-evidence on`. Se já estiver ativado, revisar a clareza do prompt de regras de negócio em `code_agent.py`. |
| **Sandbox Success baixo (<70%) com erros de tabela/coluna** | Alucinação de nomes de tabelas/colunas por excesso ou falta de contexto no schema. | Ativar `--rag on` (SchemaGraphRAG) para podar tabelas irrelevantes e focar apenas no subgrafo mínimo de conexões via Steiner Tree. |
| **Queda drástica apenas em perguntas `Challenging`** | Dificuldade com raciocínio analítico para consultas complexas (JOINs múltiplos ou CTEs). | Garantir `--cot on` (Chain of Thought ativado) para forçar o modelo a decompor a lógica dentro de `<thought>...</thought>`. |
| **Self-Correction Recovery Rate baixa (~0%)** | O agente recebe a mensagem de erro do Sandbox, mas insiste na mesma consulta errada. | Ajustar a função `_formatar_historico_tentativas` em `code_agent.py` para enfatizar o erro anterior e instruir o modelo a tentar uma abordagem diferente. |
| **Custo de `tokens_input` excessivamente alto (> 4.000 tokens/q)** | Schema completo sendo injetado desnecessariamente no prompt. | Ativar o `SchemaGraphRAG` (`--rag on`) para reduzir o schema injetado em até 70%. |

---

## 9. Submissão e Validação no Harness Oficial do BIRD

O `BirdCSVReporter` gera automaticamente o arquivo `predict_dev.json` no formato canônico aceito pelo script `evaluation.py` oficial da DAMO Academy:
```json
{
  "1471": "SELECT count(*) FROM customers\t----- ----- -----\tdebit_card_specializing",
  "1472": "SELECT CustomerID FROM customers ...\t----- ----- -----\tdebit_card_specializing"
}
```

Para rodar o avaliador oficial do BIRD sobre as predições geradas pelo `TextToInsight`:
```bash
python evaluation/evaluation.py \
  --db_root_path data/bird/dev_databases/ \
  --predicted_sql_path reports/bird/run_<timestamp>/bird_eval_<timestamp>.json \
  --ground_truth_sql_path data/bird/dev.json \
  --num_cpus 8
```

---

## 10. Guia de Extensão Futura (PR #15 - MySQL e PostgreSQL via SQLAlchemy)

Quando o **PR #15** for mergeado com o suporte a conexões de rede via SQLAlchemy:

1. **Os dados já estão presentes em `data/bird`:**
   - Dumps SQL de MySQL em `data/bird/MINIDEV_mysql/BIRD_dev.sql`.
   - Dumps SQL de PostgreSQL em `data/bird/MINIDEV_postgresql/BIRD_dev.sql`.
2. **Resolução de Conexões em `src/bird/data_loader.py`:**
   - As funções `load_bird_dev_examples(dialect="mysql")` e `get_database_uri(dialect="mysql")` já estão prontas.
   - Basta configurar as variáveis de ambiente com o IP/Porta do servidor ou container Docker:
     ```bash
     export BIRD_MYSQL_URI="mysql+pymysql://root:root@localhost:3306/{db_id}"
     export BIRD_POSTGRES_URI="postgresql+psycopg2://postgres:postgres@localhost:5432/{db_id}"
     ```
3. **Execução no `src/bird/query_executor.py`:**
   - O executor já possui a assinatura preparada:
     ```python
     executor = BirdQueryExecutor(data_dir="data/bird", backend="sqlalchemy")
     ```
   - O método `_execute_sqlalchemy` instanciará a engine correspondente sem alterar nenhuma linha dos testes de SQLite nem dos orquestradores.
