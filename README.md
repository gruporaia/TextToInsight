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

Sistema completo de avaliação que testa o agente contra o dataset Spider, rastreando cada tentativa (quando o crítico reprova e volta ao planejador).

**Nota**: Spider Dataset é opcional. Use apenas se quiser avaliar contra 1.034 exemplos reais.

### Setup

1. [Baixar Spider Dataset](https://drive.google.com/uc?export=download&id=1iYkIGr7MwuOvBkRkj4RKs6Ff3NMa7NMG)
2. Descompactar em `data/spider_data/`
3. Verificar estrutura:
   ```
   data/spider_data/spider_data/
   ├── dev.json
   ├── database/
   │   ├── concert_singer/
   │   ├── pets_1/
   │   └── ... (20 bancos no total)
   ```

### Como usar

```bash
# Teste simples com 10 perguntas (padrão)
python scripts/test_spider_eval.py

# Com customizações
python scripts/test_spider_eval.py \
  --sample-size 50 \
  --seed 42 \
  --db-filter concert_singer \
  --output reports/my_eval.csv

# Parâmetros
# --sample-size N       : Quantas perguntas testar (default: 10)
# --seed SEED           : Seed para reproducibilidade (default: 42)
# --db-filter DB_ID     : Filtrar por um banco específico
# --output PATH         : Caminho para salvar CSV (default: reports/spider_eval_TIMESTAMP.csv)
# --max-attempts N      : Máximo de tentativas por pergunta (default: 3)
```

### Saída

O script gera um CSV com 12 colunas, **uma linha por tentativa**:

```
id_exemplo | tentativa_numero | db_id | pergunta_usuario | query_ouro_spider | query_agente_tentativa | veredito_critico | feedback_critico_recebido | similarity_score_sql | resultado_exato_match | ...
1          | 1                | concert_singer | How many singers? | SELECT ... | SELECT ... | reprovado | Table "singers" does not... | 0.85   | -
1          | 2                | concert_singer | How many singers? | SELECT ... | SELECT ... | aprovado  | Aprovado | 1.00 | True
```

E um resumo final:

```
Total de perguntas: 10
Total de tentativas: 12
Perguntas aprovadas: 8
Taxa de aprovação: 80%
Taxa de sucesso na 1ª tentativa: 60%
Tentativas médias por pergunta: 1.2
Similarity score médio: 0.92
```

### Interpretação

- **id_exemplo**: ID da pergunta (mesmo para todas tentativas dela)
- **tentativa_numero**: 1ª, 2ª, 3ª tentativa...
- **veredito_critico**: Aprovado/Reprovado/Erro naquela tentativa
- **feedback_critico**: Motivo da reprovação (útil para debug)
- **similarity_score_sql**: 0-1, quanto a query do agente se parece com a ouro
- **resultado_exato_match**: Se o resultado executado foi exatamente igual

**Análise típica**:
- Taxa de 1ª tentativa baixa? → Agente está gerando queries incorretas inicialmente
- Similarity score alto mas veredito reprovado? → Queries sintaticamente parecidas mas semanticamente diferentes
- Muitas tentativas? → Crítico não está dando feedback útil ou agente não aprende com feedback

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
