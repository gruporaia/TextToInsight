# Text-to-Insight

Sistema de agentes baseado em **LangGraph** que transforma perguntas em linguagem natural em consultas SQL, executa contra um banco SQLite real, valida os resultados automaticamente e gera uma resposta final em linguagem natural.

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
   Aprovado? -- Sim --> [Resposta Natural] --> FIM
             -- Não --> Volta ao Planejador (retry)
```

Fluxo paralelo:
- Se faltar contexto humano: Planejador -> Espera Humana -> Planejador

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

# Forçando HITL ligado
python main.py --hitl on "Quais categorias vendem mais?"

# Execução não interativa
python main.py --hitl off "Quais categorias vendem mais?"

# Sem argumento usa pergunta padrão
python main.py
```

Por padrão, o sistema roda com `--hitl on`.

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
│   ├── model_selection.py               # Seleção de provedor/modelo LLM
│   ├── utils.py                         # Métricas de tokens e latência
│   ├── nodes/
│   │   ├── planner.py                   # Planejador (LLM)
│   │   ├── schema.py                    # Extração de schema (SQLite)
│   │   ├── code_agent/
│   │   │   ├── code_agent.py            # Geração de SQL (LLM)
│   │   │   └── code_sql.py              # Validação e execução de SQL
│   │   ├── sandbox.py                   # Executor de SQL (banco real)
│   │   ├── critic.py                    # Avaliador de qualidade (LLM)
│   │   └── response.py                  # Resposta final em linguagem natural
│   └── routers/
│       └── edges.py                     # Roteadores condicionais
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
langchain-openai
python-dotenv>=1.0.0
pytest>=9.0.2
pytest-recording>=0.13.0
pytest-timeout>=2.3.0
```

## Stack

- **LangGraph** para orquestração do grafo de agentes
- **Google Gemini** (gemini-2.5-flash, padrão atual) para chamadas LLM
- **OpenAI Chat Models** suportados via seletor de modelo
- **SQLite** como banco de dados (modo read-only)
- **pytest + VCR** para testes determinísticos com gravação de chamadas
