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
python -c "from src.graph import Graph; print('OK')"
```

## Executando

```bash
# Com pergunta customizada
python main.py "Quantos pedidos existem no banco?"

# Com pergunta padrão
python main.py
```

### Tutorial da flag `--hitl`

A CLI agora aceita o toggle `--hitl {on,off}` para controlar o modo Human-in-the-Loop.

#### 1) Comportamento padrão (sem informar flag)

Se você não passar `--hitl`, o modo fica **ativado automaticamente** (`on`).

```bash
python main.py "Quantos pedidos existem no banco?"
```

#### 2) Forçar HITL ligado

Use quando quiser interação humana no terminal caso o planejador peça esclarecimentos.

```bash
python main.py --hitl on "Quais foram os principais fatores de queda no lucro?"
```

Quando o fluxo precisar de ajuda humana, o terminal pergunta e aguarda input:

```text
[HITL]: <pergunta do agente>
[RESPOSTA USUARIO]:
```

#### 3) Desligar HITL

Use para execução não interativa (scripts, pipelines, CI, etc.).

```bash
python main.py --hitl off "Quais foram os principais fatores de queda no lucro?"
```

Se o grafo chegar em `espera_humana` com `--hitl off`:
- o sistema **não** chama `input()`;
- encerra a execução com status `bloqueado_hitl`;
- registra erro explicando que havia necessidade de intervenção humana com HITL desativado.

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

## Estrutura de arquivos

```
TextToInsight/
├── main.py                        # Ponto de entrada CLI
├── .env                           # GOOGLE_API_KEY (não commitar)
├── requirements.txt
├── data/
│   └── olist_relational.db        # Banco SQLite
├── src/
│   ├── state.py                   # EstadoTextToInsight (TypedDict)
│   ├── graph.py                   # Grafo LangGraph
│   ├── model_selection.py         # Seleção de modelo/provedor LLM
│   ├── utils.py                   # Tokens e métricas CSV
│   ├── nodes/
│   │   ├── planner.py             # Planejador (Gemini)
│   │   ├── schema.py              # Extração de schema (SQLite)
│   │   ├── code_agent/
│   │   │   ├── code_agent.py      # Geração SQL (Gemini)
│   │   │   └── code_sql.py        # Validação + execução SQL
│   │   ├── sandbox.py             # Executor SQL (banco real)
│   │   ├── critic.py              # Avaliador (Gemini)
│   │   └── response.py            # Resposta natural final
│   └── routers/
│       └── edges.py               # Roteadores condicionais
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

**Validação de import do grafo falha com `grafo_text_to_insight`**
O módulo atual expõe a classe `Graph` (não um singleton global). Use o comando de validação da seção Setup.
