# Arquitetura - Text-to-Insight

## Visão geral

O sistema usa um grafo de agentes (LangGraph) com 7 nós e 3 roteadores condicionais. Quatro nós podem usar LLM (Planejador, Agente de Código, Crítico e Resposta), um usa introspecção SQLite, um executa SQL diretamente e um funciona como breakpoint estrutural para HITL.

```
START
  |
[Planejador] ---------> decide estratégia (LLM)
  |
  |-- schema vazio? --> [Schema] --> extrai metadados do banco
  |                        |
  |                        v
  |--------------------- volta ao Planejador
  |
  |-- precisa ajuda? --> [Espera Humana] --> interrompe fluxo (HITL)
  |                        |
  |                        v
  |--------------------- volta ao Planejador
  |
  |-- pronto? --------> [Agente Código] --> gera SQL (LLM)
  |                        |
  |                        v
  |                    [Executor] --> executa SQL no banco real
  |                        |
  |                        v
  |                    [Crítico] --> avalia resultado (LLM)
  |                        |
  |                   aprovado? -- Sim --> [Resposta] --> END
  |                              -- Não --> volta ao Planejador
  |
  |-- aprovado? ------> END
```

## Nós

### Planejador (`src/nodes/planner.py`)

Decide a próxima etapa do fluxo.

- **Sem schema**: retorna `"aguardando_schema"` (determinístico, sem API)
- **Com schema**: chama Gemini para decidir entre `"pronto_codificacao"`, `"revisando_estrategia"` ou `"necessita_ajuda"` (quando HITL está ativo)
- **Já aprovado**: mantém `"aprovado"`

Entrada: `pergunta_usuario`, `historico_conversa`, `contexto_schema`, `feedback_critico`, `status`, `erro_execucao`, `tentativas_loop`
Saída: `status`, `espera_humana`, `pergunta_ao_usuario`, `tentativas_loop`, métricas de token

### Espera Humana (`src/graph.py`)

Nó estrutural para Human-in-the-Loop (HITL).

- Não transforma dados
- Existe para permitir interrupção controlada via `interrupt_before=["espera_humana"]`
- Após coleta de input no terminal, o fluxo retorna ao Planejador

Entrada: estado atual completo
Saída: estado inalterado

### Schema (`src/nodes/schema.py`)

Extrai metadados estruturais do banco SQLite via introspecção (PRAGMA).

- Lista tabelas, colunas, tipos, constraints e foreign keys
- Conexão read-only (`?mode=ro`)
- Sem chamada de API

Entrada: `db_path`
Saída: `contexto_schema`, `status`

### Agente de Código (`src/nodes/code_agent/code_agent.py`)

Gera SQL a partir da pergunta + schema usando Gemini.

- Prompt inclui schema completo, pergunta do usuário e feedback anterior (se retry)
- Extrai SQL pura da resposta (remove markdown se presente)
- Incrementa `tentativas_loop`

Entrada: `pergunta_usuario`, `contexto_schema`, `feedback_critico`
Saída: `sql_gerada`, `status`, `tentativas_loop`, métricas de token

Utilidades auxiliares em `code_sql.py`:
- `validar_sql_segura()`: bloqueia INSERT, UPDATE, DELETE, DROP, etc. Permite apenas SELECT e WITH.
- `executar_sql_sqlite()`: executa SQL em modo read-only, retorna resultado estruturado.

### Executor (`src/nodes/sandbox.py`)

Valida e executa a SQL gerada contra o banco real.

- Usa `executar_sql_sqlite()` de `code_sql.py`
- Retorna preview de até 30 linhas + total de linhas
- Sem chamada de API

Entrada: `sql_gerada`, `db_path`
Saída: `linhas_resultado_preview`, `total_linhas_resultado`, `saida_terminal`, `erro_execucao`, `status`

### Crítico (`src/nodes/critic.py`)

Avalia se o resultado responde à pergunta original usando Gemini.

- Se houve erro de execução: reprova direto sem chamar API
- Se execução OK: chama Gemini com pergunta + SQL + preview dos resultados
- Retorna veredito (`"aprovado"` / `"reprovado"`) e feedback textual

Entrada: `pergunta_usuario`, `sql_gerada`, `linhas_resultado_preview`, `status`
Saída: `feedback_critico`, `status`, métricas de token

### Resposta (`src/nodes/response.py`)

Gera resposta final em linguagem natural quando o resultado foi aprovado.

- Só executa geração textual quando `status == "aprovado"`
- Em `pytest`/`CI`, usa fallback determinístico sem API
- Em execução normal, usa LLM para sumarizar pergunta + resultado

Entrada: `pergunta_usuario`, `sql_gerada`, `linhas_resultado_preview`, `total_linhas_resultado`, `saida_terminal`, `status`
Saída: `resposta_natural`, `status`, métricas de token

## Roteadores (`src/routers/edges.py`)

### Após Executor (`roteador_sandbox`)
- `exec_ok` → Crítico
- `exec_erro` + tentativas < 3 → Planejador
- Caso contrário → Planejador

### Após Planejador (`roteador_planejador`)
- `espera_humana=True` → Espera Humana
- Schema vazio → Schema
- `pronto_codificacao` ou `revisando_estrategia` → Agente Código
- `aprovado` → END
- Default → Planejador

### Após Crítico (`roteador_critico`, definido em `graph.py`)
- `aprovado` → Resposta
- Caso contrário → Planejador

## Estado compartilhado (`src/state.py`)

```python
# Campos obrigatórios
pergunta_usuario: str       # Pergunta em linguagem natural
db_path: str                # Caminho para o SQLite

# Campos preenchidos pelos nós
contexto_schema: str        # Schema textual extraído
sql_gerada: str             # SQL produzida pelo agente
linhas_resultado_preview: list[dict]  # Amostra de até 30 linhas
total_linhas_resultado: int # Total de linhas retornadas
erro_execucao: str          # Mensagem de erro (se houver)
saida_terminal: str         # Saída resumida da execução
feedback_critico: str       # Feedback do crítico
espera_humana: bool         # Flag para interrupção HITL
pergunta_ao_usuario: str    # Pergunta que será exibida no terminal
historico_conversa: list[tuple[str, str]]  # Histórico humano/agente
resposta_natural: str       # Resposta final em linguagem natural
status: StatusExecucao      # Estágio atual do fluxo
tentativas_loop: int        # Contador de tentativas

# Telemetria
tokens_input: int
tokens_output: int
tokens_total: int
```

Status possíveis: `iniciado`, `aguardando_schema`, `schema_obtido`, `pronto_codificacao`, `sql_gerada`, `exec_ok`, `exec_erro`, `revisando_estrategia`, `aprovado`, `reprovado`.

Status operacionais adicionais em runtime: `aguardando_input` (pedido de HITL) e `bloqueado_hitl` (quando a CLI roda com `--hitl off` e há necessidade de intervenção humana).

## Fluxo típico

```
1. START → Planejador (schema vazio → "aguardando_schema")
2. → Schema (extrai metadados do SQLite)
3. → Planejador reavalia estratégia
4. → (Opcional) Espera Humana se houver ambiguidade/falta de contexto
5. → Agente Código (gera SQL com Gemini)
6. → Executor (executa SQL no banco)
7. → Crítico (avalia resultado com Gemini)
8. → Se aprovado: Resposta → END
  Se reprovado: volta ao Planejador com feedback
```
