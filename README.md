# Text-to-Insight

Sistema de agentes baseado em **LangGraph** que transforma perguntas em linguagem natural em consultas SQL, executa contra um banco SQLite real e valida os resultados automaticamente.

## Como funciona

```
Pergunta do usuário
        |
  [Planejador]  --  Decide a estratégia (LLM)
        |
    [Schema]    --  Extrai metadados do banco (SQLite)
        |
 [Agente Código] -- Gera SQL a partir da pergunta + schema (LLM)
        |
   [Executor]   --  Executa SQL no banco real (read-only)
        |
    [Crítico]   --  Avalia se o resultado responde à pergunta (LLM)
        |
   Aprovado? -- Sim --> FIM
             -- Não --> Volta ao Planejador (retry)
```

## Requisitos

- Python 3.10+
- Conta Google com API Key para Gemini

## Setup

```bash
# 1. Instalar dependências
pip install -r requirements.txt

# 2. Criar arquivo .env na raiz do projeto
echo "GOOGLE_API_KEY=sua_chave_aqui" > .env

# 3. Colocar o banco SQLite em data/
#    (ex: data/olist_relational.db)
```

## Uso

```bash
# Pergunta via linha de comando
python main.py "Quantos pedidos existem no banco?"

# Sem argumento usa pergunta padrão
python main.py
```

## Testes

O projeto possui 3 camadas de teste:

```bash
# Camada 1: Componentes isolados (sem API, rápido)
pytest tests/test_componentes.py -v -s

# Camada 2: Cada nó individualmente com API + banco real
pytest tests/test_nodes.py -v -s

# Camada 3: Grafo completo end-to-end
pytest tests/test_integracao.py -v -s
```

## Avaliação com Spider Dataset

O projeto inclui um módulo de avaliação (`src/spider/`) que testa o agente contra o **Spider Dataset** (1.034 perguntas reais em SQL, 20 bancos diferentes), rastreando cada tentativa quando o crítico reprova.

**Para detalhes técnicos sobre o módulo, arquitetura e fluxo de debug, consulte: [`src/spider/BENCHMARK.md`](src/spider/BENCHMARK.md)**

**Nota**: Spider Dataset é opcional.

### Setup rápido

```bash
# Baixar dataset Spider
python scripts/test_spider_eval.py --sample-size 10

# Com databse específico
python scripts/test_spider_eval.py --db-filter concert_singer --sample-size 50
```

**Parâmetros completose exemplos em [`src/spider/BENCHMARK.md`](src/spider/BENCHMARK.md)**

## Estrutura

```
TextToInsight/
├── main.py                              # Ponto de entrada
├── .env                                 # GOOGLE_API_KEY
├── requirements.txt                     # Dependências
├── data/
│   └── olist_relational.db              # Banco SQLite para análise
├── src/
│   ├── state.py                         # Estado compartilhado (TypedDict)
│   ├── graph.py                         # Grafo LangGraph compilado
│   ├── nodes/
│   │   ├── planner.py                   # Planejador (LLM)
│   │   ├── schema.py                    # Extração de schema (SQLite)
│   │   ├── code_agent/
│   │   │   ├── code_agent.py            # Geração de SQL (LLM)
│   │   │   └── code_sql.py              # Validação e execução de SQL
│   │   ├── sandbox.py                   # Executor de SQL (banco real)
│   │   └── critic.py                    # Avaliador de qualidade (LLM)
│   ├── routers/
│   │   └── edges.py                     # Roteadores condicionais
│   └── spider/                          # **NOVO**: Módulo Spider
│       ├── __init__.py
│       ├── data_loader.py               # Carregar exemplos de dev.json
│       ├── query_executor.py            # Executar queries no banco
│       ├── metrics.py                   # Similarity score, comparações
│       └── csv_reporter.py              # Salvar CSV por tentativa
├── scripts/
│   └── test_spider_eval.py              # **NOVO**: Script de avaliação
└── tests/
    ├── test_componentes.py              # Testes sem API
    ├── test_nodes.py                    # Testes por nó com API
    └── test_integracao.py               # Teste do grafo completo
```

## Dependências

```
langgraph>=0.2.0
langchain>=0.2.0
langchain-core>=0.2.0
langchain-google-genai>=2.0.0
python-dotenv>=1.0.0
```

## Stack

- **LangGraph** para orquestração do grafo de agentes
- **Google Gemini** (gemini-2.5-flash) para chamadas LLM
- **SQLite** como banco de dados (modo read-only)
- **pytest** para testes
