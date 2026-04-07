# Guia de Desenvolvimento - Text-to-Insight

## Setup

### Pré-requisitos

- Python 3.10+
- conda (ou pip + venv)
- Git
- Chave de API do Google Gemini

### Instalação

```bash
# Criar e ativar ambiente
conda create -n textToInsight python=3.11
conda activate textToInsight

# Instalar dependências
pip install -r requirements.txt

# Instalar ferramentas de teste
pip install pytest pytest-timeout
```

### Configuração

Criar `.env` na raiz do projeto:

```
GOOGLE_API_KEY=sua_chave_aqui
```

Colocar o banco SQLite em `data/` (ex: `data/olist_relational.db`).

### Validar instalação

```bash
python -c "from src.graph import grafo_text_to_insight; print('OK')"
```

## Executando

```bash
# Com pergunta customizada
python main.py "Quantos pedidos existem no banco?"

# Com pergunta padrão
python main.py
```

## Testes

3 camadas, do mais rápido ao mais completo:

```bash
# Camada 1: Componentes (sem API, ~1s)
# Testa: validação SQL, execução SQL, schema, executor, routers
pytest tests/test_componentes.py -v -s

# Camada 2: Nós individuais (com API, ~30s)
# Testa cada nó isoladamente com estado manual
pytest tests/test_nodes.py -v -s

# Camada 3: Grafo completo (com API, ~1-2min)
# Testa o pipeline inteiro end-to-end
pytest tests/test_integracao.py -v -s

# Tudo de uma vez
pytest tests/ -v -s
```

Se um teste falha:
- Falha na camada 1 → lógica determinística quebrou
- Falha na camada 2 → o nó específico que falhou está com problema
- Camada 2 passa mas camada 3 falha → problema nos roteadores ou na conexão entre nós

## Testes do módulo Spider

```bash
# Testar data loader
python -c "from src.spider.data_loader import load_spider_dev_examples; ex = load_spider_dev_examples('data/spider_data/spider_data'); print(f'Carregado: {len(ex)} exemplos')"

# Testar query executor
python -c "from src.spider.query_executor import SpiderQueryExecutor; ex = SpiderQueryExecutor(); print(f'DBs encontrados: {len(ex.list_available_dbs())}')"

# Testar metrics
python -c "from src.spider.metrics import sql_similarity_score; print(f'Score: {sql_similarity_score(\\\"SELECT * FROM a\\\", \\\"select * from a\\\")}')"
```

## Arquitetura do módulo Spider

O módulo reutiliza o grafo existente para avaliar cada pergunta do Spider dataset:

### Fluxo por pergunta

```
1. Load: data_loader carrega dev.json (1.034 exemplos)
   └─> Cada exemplo tem: {question, query (ouro), db_id}

2. Query Ouro: SpiderQueryExecutor executa query original no banco
   └─> Captura resultado esperado (baseline)

3. Para cada tentativa (até 3x):
   a) Estado Inicial: pergunta + schema do banco
   b) Grafo Executa: planejador → schema → code_agent → sandbox → crítico
   c) Stream Acumula: full_estado coleta mudanças de cada nó
   d) Crítico Decide:
      - Se aprovado: extrai query_agente, compara com ouro
      - Se reprovado: volta ao planejador (retry)
   e) CSV Registra: 1 linha por tentativa com todos os dados

4. Resumo: CSVReporter calcula estatísticas (taxa aprovação, similarity média, etc)
```

### Pontos técnicos importantes

- **State Accumulation**: O stream() retorna deltas por nó, não estado total. Script mantém `full_estado = estado_inicial.copy()` e atualiza com cada output
- **Recursion Limit**: Config de `recursion_limit=30` permite planejador iterar até 3 vezes sem erro
- **Database Path**: Spider stores databases em `data/spider_data/spider_data/database/{db_id}/{db_id}.sqlite`
- **Read-only Mode**: SQLite conecta com `?mode=ro&uri=true` para evitar escrita
- **Normalization**: SQL é normalizado (UPPER, sem comentários, sem whitespace) antes de comparar similarity

### Customizando a avaliação

Para testar com diferentes LLMs ou ajustar prompts:

1. Editar `src/nodes/planner.py`, `code_agent.py`, `critic.py` conforme necessário
2. Script USA o mesmo grafo (`src/graph.py`), então mudanças se refletem automaticamente
3. Para testar especificamente um nó: reutilize o teste em `tests/test_nodes.py`

## Avaliação com Spider Dataset

O projeto inclui um sistema completo de avaliação contra o dataset Spider, que rastreia cada tentativa do agente quando o crítico reprova.

### Setup

```bash
# Baixar o dataset Spider
# Extrair em data/spider_data/
# Estrutura esperada:
# data/spider_data/spider_data/
#   ├── dev.json
#   ├── database/
#   │   ├── concert_singer/
#   │   ├── pets_1/
#   │   └── ...
```

### Executar avaliação

```bash
# Teste simples com 10 perguntas
python scripts/test_spider_eval.py

# Com customizações
python scripts/test_spider_eval.py \
  --sample-size 50 \
  --seed 42 \
  --db-filter concert_singer \
  --output reports/spider_eval.csv \
  --max-attempts 5

# Parâmetros:
# --sample-size N       : Quantas perguntas testar (default: 10)
# --seed SEED           : Seed para reproducibilidade (default: 42)
# --db-filter DB_ID     : Filtrar por um banco (ex: concert_singer)
# --output PATH         : Caminho CSV (default: reports/spider_eval_TIMESTAMP.csv)
# --max-attempts N      : Máx tentativas por pergunta (default: 3)
```

### Entender os resultados

O script gera um CSV com **uma linha por tentativa**:

| Coluna | Significado |
|--------|-------------|
| tentativa_numero | 1ª, 2ª, 3ª tentativa desta pergunta |
| veredito_critico | aprovado/reprovado/erro |
| feedback_critico | Motivo da reprovação (para debug) |
| similarity_score_sql | 0-1, similaridade com query ouro |
| resultado_exato_match | True se resultado foi idêntico |
| tempo_agente_ms | Quanto tempo levou aquela tentativa |

Resumo final:
```
Total de perguntas: 100
Perguntas aprovadas: 82
Taxa de aprovação: 82.0%
Taxa de sucesso na 1ª tentativa: 75.0%
Tentativas médias por pergunta: 1.23
Similarity score médio: 0.945
Tempo médio por tentativa: 12500 ms
```

**Análise**:
- Taxa de 1ª tentativa baixa? → Agente gerando queries incorretas inicialmente
- Similarity alto mas veredito reprovado? → Queries diferentes semanticamente
- Muitas tentativas? → Crítico ou agente não aprendendo com feedback

## Estrutura de arquivos

```
TextToInsight/
├── main.py                        # Ponto de entrada CLI
├── .env                           # GOOGLE_API_KEY (não commitar)
├── requirements.txt
├── data/
│   ├── olist_relacional.db        # Banco SQLite (dados de teste)
│   └── spider_data/               # Dataset Spider (opcional)
│       └── spider_data/
│           ├── dev.json
│           └── database/
├── src/
│   ├── state.py                   # EstadoTextToInsight (TypedDict)
│   ├── graph.py                   # Grafo LangGraph
│   ├── nodes/
│   │   ├── planner.py             # Planejador (Gemini)
│   │   ├── schema.py              # Extração de schema (SQLite)
│   │   ├── code_agent/
│   │   │   ├── code_agent.py      # Geração SQL (Gemini)
│   │   │   └── code_sql.py        # Validação + execução SQL
│   │   ├── sandbox.py             # Executor SQL (banco real)
│   │   └── critic.py              # Avaliador (Gemini)
│   ├── spider/                    # **Módulo de Avaliação Spider**
│   │   ├── data_loader.py         # Carregar dev.json
│   │   ├── query_executor.py      # Executar queries
│   │   ├── metrics.py             # Similarity score, comparações
│   │   └── csv_reporter.py        # Gerar CSV por tentativa
│   └── routers/
│       └── edges.py               # Roteadores condicionais
├── scripts/
│   └── test_spider_eval.py        # Script de avaliação Spider
├── reports/
│   └── spider_eval_*.csv          # Resultados das avaliações
└── tests/
    ├── test_componentes.py        # Sem API
    ├── test_nodes.py              # Com API, nó a nó
    └── test_integracao.py         # Com API, grafo completo
```

## Criando um novo nó

1. Criar `src/nodes/novo_no.py`:

```python
from ..state import EstadoTextToInsight

def nos_nodo_novo(estado: EstadoTextToInsight) -> dict:
    # ler do estado
    valor = estado.get("algum_campo", "")

    # processar

    # retornar atualizações
    return {
        "campo_atualizado": resultado,
        "status": "novo_status",
    }
```

2. Registrar em `src/nodes/__init__.py`
3. Adicionar ao grafo em `src/graph.py`
4. Criar teste em `tests/`

## Criando um novo roteador

```python
from typing import Literal
from ..state import EstadoTextToInsight

def roteador_novo(estado: EstadoTextToInsight) -> Literal["no_a", "no_b"]:
    if estado.get("status") == "condicao":
        return "no_a"
    return "no_b"
```

Registrar com `add_conditional_edges()` em `graph.py`.

## Git Flow

```
main          ← versão estável (v0.0.1)
  └── dev     ← desenvolvimento
       └── feature_<nome>  ← features individuais
```

Branches de hotfix saem direto de `main`.

## Variáveis de ambiente

| Variável | Descrição |
|---|---|
| `GOOGLE_API_KEY` | Chave da API do Google Gemini |

## Troubleshooting

**`ModuleNotFoundError: No module named 'langgraph'`**
```bash
pip install -r requirements.txt
```

**`429 RESOURCE_EXHAUSTED`**
Quota da API Gemini esgotada. Aguardar reset ou verificar em https://ai.dev/rate-limit

**Grafo entra em loop infinito**
O sistema limita a 3 tentativas via `roteador_sandbox`. Se persistir, verificar se o status retornado pelos nós é um valor válido de `StatusExecucao`.
