# Arquitetura - Text-to-Insight

## Visão geral

O sistema usa um grafo de agentes (LangGraph) com 5 nós e 3 roteadores condicionais. Três nós usam LLM (Gemini), um usa introspecção SQLite e um executa SQL diretamente.

```
START
  |
[Planejador] ---------> decide estratégia (LLM)
  |
  |-- schema vazio? --> [Schema] --> extrai metadados do banco
  |                        |
  |                        v
  |-- pronto? --------> [Agente Código] --> gera SQL (LLM)
  |                        |
  |                        v
  |                    [Executor] --> executa SQL no banco real
  |                        |
  |                        v
  |                    [Crítico] --> avalia resultado (LLM)
  |                        |
  |                   aprovado? -- Sim --> END
  |                              -- Não --> volta ao Planejador
  |
  |-- aprovado? ------> END
```

## Nós

### Planejador (`src/nodes/planner.py`)

Decide a próxima etapa do fluxo.

- **Sem schema**: retorna `"aguardando_schema"` (determinístico, sem API)
- **Com schema**: chama Gemini para decidir entre `"pronto_codificacao"` ou `"revisando_estrategia"`
- **Já aprovado**: mantém `"aprovado"`

Entrada: `pergunta_usuario`, `contexto_schema`, `feedback_critico`, `status`
Saída: `status`

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
Saída: `sql_gerada`, `status`, `tentativas_loop`

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
Saída: `feedback_critico`, `status`

## Roteadores (`src/routers/edges.py`)

### Após Executor (`roteador_sandbox`)
- `exec_ok` → Crítico
- `exec_erro` + tentativas < 3 → Planejador
- Caso contrário → Planejador

### Após Planejador (`roteador_planejador`)
- Schema vazio → Schema
- `pronto_codificacao` ou `revisando_estrategia` → Agente Código
- `aprovado` → END

### Após Crítico (`roteador_critico`, definido em `graph.py`)
- `aprovado` → END
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
status: StatusExecucao      # Estágio atual do fluxo
tentativas_loop: int        # Contador de tentativas
```

Status possíveis: `iniciado`, `aguardando_schema`, `schema_obtido`, `pronto_codificacao`, `sql_gerada`, `exec_ok`, `exec_erro`, `revisando_estrategia`, `aprovado`, `reprovado`.

## Fluxo típico

```
1. START → Planejador (schema vazio → "aguardando_schema")
2. → Schema (extrai metadados do SQLite)
3. → Agente Código (gera SQL com Gemini)
4. → Executor (executa SQL no banco)
5. → Crítico (avalia resultado com Gemini)
6. → Se aprovado: END
     Se reprovado: volta ao passo 1 com feedback
```
