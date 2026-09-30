# Manual de Operacao e Avaliacao do Benchmark BIRD - TextToInsight

Este manual tecnico estabelece o guia operacional para reproduzir, configurar e avaliar o benchmark **BIRD (Big Bench for Large-scale Database Grounded Text-to-SQL Evaluation)** no repositorio TextToInsight.

---

## 1. Arquitetura da Pipeline de Avaliacao

A suite de avaliacao BIRD e composta por modulos desacoplados responsaveis pela preparacao de dados, execucao de queries, calculo de metricas e geracao de relatorios:

```mermaid
flowchart TD
    Setup["scripts/setup_bird_data.py"] -->|"Download e Normalizacao"| Data["data/bird (dev.json + dev_databases)"]
    Data --> Loader["src/bird/data_loader.py"]
    Loader --> CLI["scripts/test_bird_eval.py (Orquestrador)"]
    CLI -->|"Cache de Grafo"| Engine["InsightEngine (Planner + Code Agent + Sandbox)"]
    Engine -->|"SQL Candidato"| Executor["src/bird/query_executor.py (SQLite Read-Only)"]
    Data -->|"SQL de Referencia (Gold)"| Executor
    Executor -->|"Resultados de Execucao"| Metrics["src/bird/metrics.py (EX Multiset / Counter)"]
    Metrics -->|"Linhas Canonicas"| Reporter["src/bird/csv_reporter.py"]
    Reporter -->|"16 colunas"| CSV["reports/bird/.../*.csv"]
    Reporter -->|"Relatorio Sintetico"| MD["reports/bird/.../*.md"]
    Reporter -->|"Formato Oficial BIRD"| JSON["reports/bird/.../*.json (predict_dev.json)"]
```

---

## 2. Preparacao do Ambiente e Download dos Dados

### 2.1. Ativacao do Ambiente Virtual
Certifique-se de que o ambiente virtual do projeto esteja ativo antes de executar os scripts:
```bash
source .venv/bin/activate
```

### 2.2. Download e Estruturacao do Dataset BIRD
O script `scripts/setup_bird_data.py` utiliza exclusivamente a biblioteca padrao do Python para download, descompactacao e normalizacao do layout de pastas.

#### Modo Mini-Dev (Recomendado para Desenvolvimento Local)
O pacote Mini-Dev contem 500 perguntas selecionadas distribuidas em 11 bancos de dados SQLite (~800 MB comprimido):
```bash
python scripts/setup_bird_data.py --mini
```
Acoes executadas automaticamente:
- Download do arquivo comprimido oficial a partir do espelho da nuvem BIRD.
- Descompactacao e extracao para o diretorio `data/bird/`.
- Padronizacao do arquivo de perguntas para `data/bird/dev.json`.
- Extracao dos bancos SQLite para `data/bird/dev_databases/<db_id>/<db_id>.sqlite`.

#### Modo Full Dev Set (Benchmark Completo)
Para avaliacao sobre todas as 1.534 perguntas e 95 bancos de dados do conjunto de desenvolvimento:
```bash
python scripts/setup_bird_data.py
```

#### Opcoes Avancadas de Preparacao
- **Utilizar arquivo local pre-existente:** caso o arquivo zip ja tenha sido baixado manualmente, informe o caminho direto para evitar download redundante:
  ```bash
  python scripts/setup_bird_data.py --zip-path /caminho/para/dev.zip
  ```
- **Remover arquivo compactado apos extracao:** para economizar espaco em disco, adicione `--clean-zip`:
  ```bash
  python scripts/setup_bird_data.py --mini --clean-zip
  ```
- **Sobrescrever arquivos existentes:** caso precise forcar um novo download e extracao limpa:
  ```bash
  python scripts/setup_bird_data.py --mini --force
  ```

---

## 3. Testes Automatizados de Sanidade

Antes de iniciar execucoes com chamadas a modelos de linguagem, valide a integridade estrutural do ambiente.

### 3.1. Testes Unitarios Locais
Execute a suite de testes unitarios da integracao BIRD via pytest:
```bash
pytest tests/test_bird_eval.py -v
```
Caracteristicas da suite:
- Nao consome chamadas de rede nem tokens de LLM.
- Executa em menos de 1 segundo utilizando fixtures sinteticas isoladas.
- Cobre data loader, executor seguro SQLite com timeout, metricas de comparacao de dados (Execution Accuracy agnostica a ordem e aliases) e geracao de CSV/relatorios.

### 3.2. Smoke Test com Custo Zero (Modo Simulado)
Para testar o encadeamento de ponta a ponta sem consumir credito de APIs:
```bash
python scripts/test_bird_eval.py --sample-size 3 --dry-run
```
O modo `--dry-run` simula as respostas do agente mantendo a execucao real do banco SQLite, calculo de metricas e escrita dos arquivos de relatorio.

---

## 4. Guia de Execucao e Parametros da CLI

O orquestrador de avaliacao e acionado via `scripts/test_bird_eval.py`.

### 4.1. Catalogo de Parametros Suportados

| Parametro | Tipo / Valores | Padrao | Descricao |
|---|---|---|---|
| `--data-dir` | Caminho | `data/bird` | Diretorio raiz onde o dataset BIRD esta armazenado. |
| `--sample-size` | Inteiro | `None` (todas) | Quantidade de perguntas a serem avaliadas. |
| `--seed` | Inteiro | `42` | Semente para garantir amostragem reproduzivel de perguntas. |
| `--db-filter` | Texto | `None` | Restringe a avaliacao a um banco especifico (ex: `financial`). |
| `--difficulty` | `simple`, `moderate`, `challenging` | `None` | Filtra perguntas por nivel de complexidade. |
| `--stratify` | Flag | Desativado | Mantem a proporcao original de dificuldades na amostra. |
| `--model` | Texto | Variável `OPENAI_MODEL` ou `gpt-4o-mini` | Identificador do modelo LLM a ser utilizado. |
| `--use-evidence` | `on`, `off` | `on` | Controla injecao de formulas e dicas de negocio no prompt. |
| `--rag` | `on`, `off` | `on` | Ativa selecao dinamica de tabelas via SchemaGraphRAG. |
| `--enrich-rag` | `on`, `off` | `off` | Ativa enriquecimento de colunas relevantes no RAG. |
| `--infer-fks` | Flag | Desativado | Habilita inferencia heuristica de chaves estrangeiras virtuais. |
| `--cot` | `on`, `off` | `on` | Ativa raciocinio explicito passo a passo (Chain of Thought). |
| `--data-exploration` | `on`, `off` | `on` | Permite que o agente consulte amostras de valores nas tabelas. |
| `--timeout` | Inteiro | `30` | Tempo limite em segundos para execucao de queries no SQLite. |
| `--dry-run` | Flag | Desativado | Simula a geracao de SQL sem custo de API. |
| `--output` | Caminho | `None` (gera timestamp) | Define o arquivo CSV ou a pasta de destino dos relatorios. |

### 4.2. Cenarios Comuns de Execucao

#### Execucao Basica Reduzida
Para rodar uma amostra rapida com reprodutibilidade controlada:
```bash
python scripts/test_bird_eval.py --sample-size 5 --seed 42
```

#### Amostragem Estratificada por Dificuldade
Garante que a amostra represente proporcionalmente perguntas simples, moderadas e desafiadoras:
```bash
python scripts/test_bird_eval.py --sample-size 30 --stratify --seed 123
```

#### Avaliacao Focada em um Unico Banco de Dados
Ideal para depurar esquemas especificos ou analisar o comportamento do modelo em um dominio pontual:
```bash
python scripts/test_bird_eval.py --db-filter financial --sample-size 10
```

#### Testes Comparativos de Componentes (Ablation Studies)
Permite isolar o impacto de modulos como RAG ou injecao de evidencias:
```bash
# Teste com modulo ativado
python scripts/test_bird_eval.py --sample-size 20 --seed 42 --rag on --output reports/bird/teste_rag_on

# Teste com modulo desativado
python scripts/test_bird_eval.py --sample-size 20 --seed 42 --rag off --output reports/bird/teste_rag_off
```

---

## 5. Gerenciamento e Personalizacao dos Relatorios (`reports/bird/`)

### 5.1. Os Tres Artefatos Gerados por Bateria
A cada bateria de testes, o sistema consolida as saidas em tres formatos complementares:

1. **Arquivo Detalhado (`.csv`):**
   Registra linha a linha cada tentativa de cada pergunta avaliada. Quando o agente aciona o loop de auto-correcao, cada tentativa e registrada como uma linha individual contendo o mesmo `question_id` e o respectivo `attempt_number` (1, 2, 3...), garantindo ordenacao estrita e contigua por `(question_id, attempt_number)`. Contem 16 colunas tecnicas com SQL gerada, SQL gabarito, status de sandbox, mensagens de erro capturadas, tempo de resposta e tokens consumidos.
2. **Relatorio Sintetico e Diagnostico (`.md`):**
   Documento executivo e tecnico em Markdown. Alem das metricas globais consolidadas e consumo de recursos, inclui:
   - **Secao 5 (Casos de Auto-Correcao Bem-Sucedida):** destaca perguntas que falharam na primeira tentativa mas convergiram nas tentativas seguintes, exibindo a evolucao das queries, o feedback recebido e a tabela Markdown com amostra do resultado conferido.
   - **Secao 6 (Analise Detalhada de Falhas):** diagnostico minucioso de cada divergencia de dados ou erro de sandbox, apresentando a query gabarito, o historico de tentativas do agente e **tabelas comparativas Markdown com as primeiras 10 linhas de dados retornadas por cada consulta** (Ouro vs. Agente) e o total de linhas.
3. **Predicoes Oficiais BIRD (`.json`):**
   Arquivo formatado no padrao canonico do benchmark (`predict_dev.json`), contendo sempre a consulta final candidata gerada, pronto para ser submetido diretamente ao script oficial de validacao do BIRD.

### 5.2. Personalizacao do Diretorio e Nome de Destino via `--output`
O parametro `--output` aceita duas abordagens:

#### Opcao A: Especificar o Nome da Pasta de Destino (Recomendado)
Ao passar o caminho de um diretorio, os tres arquivos serao gerados dentro dele mantendo o timestamp da execucao:
```bash
python scripts/test_bird_eval.py --sample-size 20 --output reports/bird/bateria_validacao_inicial
```
Estrutura gerada:
```text
reports/bird/bateria_validacao_inicial/
├── bird_eval_2026-09-29_21-30-00.csv
├── bird_eval_2026-09-29_21-30-00.md
└── bird_eval_2026-09-29_21-30-00.json
```

#### Opcao B: Especificar o Nome Exato do Arquivo Base
Ao passar um caminho terminando em `.csv`, o sistema utilizara o mesmo nome base para gerar o `.md` e o `.json` correspondentes:
```bash
python scripts/test_bird_eval.py --sample-size 20 --output reports/bird/benchmark_v1.csv
```
Estrutura gerada:
```text
reports/bird/
├── benchmark_v1.csv
├── benchmark_v1.md
└── benchmark_v1.json
```

#### Comportamento Padrao (Sem `--output`)
Quando o parametro `--output` for omitido, o sistema cria automaticamente uma subpasta com data e hora:
```text
reports/bird/run_YYYY-MM-DD_HH-MM-SS/
├── bird_eval_YYYY-MM-DD_HH-MM-SS.csv
├── bird_eval_YYYY-MM-DD_HH-MM-SS.md
└── bird_eval_YYYY-MM-DD_HH-MM-SS.json
```

---

## 6. Dicionario de Metricas e Interpretacao

As metricas sao calculadas em `src/bird/metrics.py` e agregadas em `src/bird/csv_reporter.py`.

### 6.1. Metricas Oficiais e Operacionais

| Metrica | Identificador Tecnico | O que Avalia | Metodo de Calculo / Criterio |
|---|---|---|---|
| **Execution Accuracy (EX Final)** | `execution_match` / `ex_final_rate` | Acuracia real do resultado de dados. | Executa a SQL gerada pelo agente e a SQL gabarito no banco. Normaliza os tipos primitivos e compara o multiset de tuplas (`Counter`). Retorna verdadeiro quando os dados retornados sao identicos, independentemente da sintaxe SQL adotada. |
| **Pass@1 (First Attempt EX)** | `ex_pass_at_1_rate` | Precisao direta na primeira tentativa. | Percentual de perguntas resolvidas corretamente na tentativa 1, antes de qualquer iteracao do loop de auto-correcao. |
| **Taxa de Sucesso no Sandbox** | `sandbox_success_rate` | Validade sintatica e consistencia com o schema. | Percentual de consultas que executam no banco sem disparar erro de sintaxe, coluna inexistente ou tabela invalida (`status == 'exec_ok'` ou `'aprovado'`). |
| **Taxa de Auto-Correcao (Self-Correction)** | `self_correction_rate` | Eficacia do feedback de erro. | Entre as perguntas que falharam na primeira tentativa, indica quantas foram corrigidas com sucesso nas tentativas subsequentes apos receberem a mensagem de erro do sandbox. |
| **Similaridade Sintatica de SQL** | `similarity_score` | Proximidade de texto entre as consultas. | Calculada via `difflib.SequenceMatcher` apos normalizacao de espacos, quebras de linha e caixa de caracteres. Serve como metrica auxiliar de inspecao sintatica. |
| **Consumo de Tokens** | `tokens_input`, `tokens_output`, `tokens_total` | Custo computacional de contexto e resposta. | Quantidade exata de tokens processados pelo provedor LLM. Permite mensurar a eficiencia de tecnicas de reducao de contexto. |
| **Latencia por Pergunta** | `time_ms` / `avg_time_ms_per_question` | Tempo de resposta total. | Duracao em milissegundos para concluir todo o ciclo de geracao e execucao da pergunta. |

---

## 7. Guia de Diagnostico e Analise de Falhas

### 7.1. Estrutura das 16 Colunas do CSV

| Coluna | Descricao e Utilidade de Analise |
|---|---|
| `question_id` | Identificador da pergunta no benchmark. |
| `attempt_number` | Numero da tentativa (`1`, `2` ou `3`). Valores maiores que 1 indicam ativacao do loop de correcao. |
| `db_id` | Identificador do banco de dados em teste. |
| `difficulty` | Complexidade da pergunta (`simple`, `moderate`, `challenging`). |
| `question` | Pergunta original formulada em linguagem natural. |
| `evidence` | Formula ou regra de negocio fornecida no dataset. |
| `gold_sql` | Consulta SQL oficial de referencia (gabarito). |
| `agent_sql` | Consulta SQL gerada pelo sistema TextToInsight. |
| `time_ms` | Tempo de execucao da tentativa em milissegundos. |
| `execution_status` | Estado final retornado pelo executor (`exec_ok`, `exec_erro`, `aprovado`). |
| `similarity_score` | Indice de similaridade de texto com o gabarito (0.0 a 1.0). |
| `execution_match` | Booleano indicando equivalencia de dados com o gabarito (`True`/`False`). |
| `error` | Descricao detalhada do erro retornado pelo SQLite quando a query falha. |
| `tokens_input` | Quantidade de tokens no prompt de entrada. |
| `tokens_output` | Quantidade de tokens na resposta gerada. |
| `tokens_total` | Soma de tokens de entrada e saida. |

### 7.2. Roteiro Pratico de Inspecao de Resultados

1. **Inspecao Macro no Relatorio `.md`:**
   - Verifique a taxa `Execution Accuracy (EX Final)` global e por dificuldade.
   - Avalie `Pass@1 (First Attempt EX)` e `Self-Correction Recovery Rate` para entender a precisao direta vs. a capacidade de recuperacao sob feedback de erro.
   - Inspecione a media de tokens por pergunta para monitorar os custos operacionais.
2. **Inspecao Direta das Tabelas de Dados e Tentativas no `.md`:**
   - **Casos de Auto-Correcao (Secao 5 do `.md`):** visualize como a query evoluiu da tentativa 1 (com erro) para a tentativa final corrigida, e confirme o resultado conferido na tabela de dados amostral.
   - **Diagnostico de Falhas (Secao 6 do `.md`):** compare diretamente as primeiras 10 linhas da tabela de dados da Query Ouro versus a Query Agente. Isso revela imediatamente diferencas de filtros (ex: `ABS(longitude)` vs `MAX(Longitude)`), colunas ausentes ou granularidade de agrupamento.
3. **Filtragem Focada no Arquivo `.csv`:**
   - **Perguntas incorretas:** filtre por `execution_match == False` na ultima tentativa de cada `question_id`.
   - **Erros de sintaxe ou schema:** filtre por `execution_status == 'exec_erro'` e examine a coluna `error`.
   - **Efetividade da auto-correcao:** filtre por `attempt_number > 1` para observar se a query final convergiu para `execution_match == True`.

### 7.3. Matriz de Diagnostico de Falhas

| Padrao de Comportamento | Causa Raiz Provavel | Acao Recomendada |
|---|---|---|
| **Taxa de sandbox elevada, mas EX baixo** | A consulta e sintaticamente valida, mas erra a logica de negocio, filtros ou funcoes de agregacao. | Garantir `--use-evidence on`. Se ja estiver ativo, revisar a interpretacao das regras de negocio nos prompts de instrucao. |
| **Falhas de execucao por coluna ou tabela inexistente** | Alucinacao de entidades de banco por falta ou excesso de informacoes de schema. | Assegurar que o schema retrieval esteja ativado (`--rag on`) para isolar as tabelas e relacoes relevantes. |
| **Queda acentuada de acerto apenas em `challenging`** | Dificuldade com raciocinio analitico de multiplos passos (JOINs multiplos, subconsultas complexas ou CTEs). | Garantir `--cot on` para obrigar o modelo a detalhar as etapas de resolucao antes de emitir a consulta final. |
| **Baixa taxa de recuperacao no loop de auto-correcao** | O modelo recebe o erro do sandbox, mas repete a mesma abordagem ou nao repara o ponto apontado no traceback. | Revisar o historico de tentativas repassado ao agente gerador para reforcar a instrucao de desvio da abordagem anterior. |
| **Consumo excessivo de `tokens_input`** | Injecao desnecessaria do schema integral do banco de dados no contexto de entrada. | Utilizar a poda dinamica de esquema com `--rag on`. |

---

## 8. Validacao no Harness Oficial do BIRD

O arquivo `.json` gerado pelo orquestrador e compativel com o script oficial `evaluation.py` do repositorio original do BIRD (DAMO Academy / HKU).

Para executar o avaliador oficial de referencia:
```bash
python evaluation/evaluation.py \
  --db_root_path data/bird/dev_databases/ \
  --predicted_sql_path reports/bird/<pasta_da_execucao>/bird_eval_<timestamp>.json \
  --ground_truth_sql_path data/bird/dev.json \
  --num_cpus 8
```

---

## 9. Suporte a Multiplos Dialetos (SQLite, MySQL, PostgreSQL)

A arquitetura do modulo `src/bird/` foi projetada para suportar avaliacao multi-dialeto:

1. **Arquivos de Esquema e Dumps:**
   - Dumps SQL compativeis com MySQL ficam localizados em `data/bird/MINIDEV_mysql/`.
   - Dumps SQL compativeis com PostgreSQL ficam localizados em `data/bird/MINIDEV_postgresql/`.
2. **Variaveis de Ambiente de Conexao:**
   O carregador e o executor resolvem as conexoes por meio de templates de URI compativeis com SQLAlchemy:
   ```bash
   export BIRD_MYSQL_URI="mysql+pymysql://usuario:senha@localhost:3306/{db_id}"
   export BIRD_POSTGRES_URI="postgresql+psycopg2://usuario:senha@localhost:5432/{db_id}"
   ```
3. **Execucao Parametrizada no Loader:**
   As funcoes em `src/bird/data_loader.py` aceitam o parametro `dialect`:
   ```python
   examples = load_bird_dev_examples(data_dir="data/bird", dialect="mysql")
   ```
