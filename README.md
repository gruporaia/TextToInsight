# Text-to-Insight

Biblioteca Python que transforma perguntas em linguagem natural em consultas SQL seguras, executadas no seu banco, com resposta final em linguagem natural (e gráfico, quando fizer sentido).

O fluxo é um grafo de agentes em [LangGraph](https://github.com/langchain-ai/langgraph): lê o schema do banco, seleciona as tabelas e regras de negócio relevantes (GraphRAG híbrido multilíngue: embeddings ONNX + BM25 bilíngue PT/EN + grafo de FKs), calcula estatísticas das colunas, gera a SQL, executa em modo somente leitura, corrige a si mesmo em caso de erro e responde. Opcionalmente pausa para pedir esclarecimentos ao usuário (human-in-the-loop).

## Instalação

```bash
pip install text-to-insight
```

SQLite funciona sem nada extra. Para PostgreSQL ou MySQL, instale o driver junto:

```bash
pip install "text-to-insight[postgres]"   # psycopg2
pip install "text-to-insight[mysql]"      # pymysql
```

Modelos suportados: Gemini (`GOOGLE_API_KEY`) e OpenAI (`OPENAI_API_KEY`).

## Uso como biblioteca

A biblioteca **não abre conexões com o banco**. Você cria uma [Engine do SQLAlchemy](https://docs.sqlalchemy.org/en/20/core/engines.html), passa para a `InsightEngine` e continua sendo dono dela (pool, credenciais, `dispose()`). A engine nunca entra no estado do grafo, então nada sobre a conexão vai para o checkpoint do LangGraph.

```python
from sqlalchemy import create_engine
from text_to_insight import InsightEngine

# SQLite em modo somente leitura
db = create_engine("sqlite:///file:dados/loja.db?mode=ro&uri=true")
# PostgreSQL: create_engine("postgresql+psycopg2://usuario:senha@host:5432/banco")

engine = InsightEngine(
    api_key="...",
    model="gemini-2.5-flash",      # ou "gpt-5-mini", "openai/gpt-4o", ...
    db_engine=db,
    hitl=True,
)

resultado = engine.run(thread_id="sessao_1", query="Quantos pedidos existem no banco?")

if resultado.get("status") == "AWAITING_USER":
    print(resultado["message"])  # pergunta do agente para o usuário
    resultado = engine.resume(thread_id="sessao_1", user_response="Considere só pedidos entregues.")

print(resultado["sql_gerada"])
print(resultado["resposta_natural"])

db.dispose()
```

Toda SQL gerada passa por uma validação (apenas `SELECT`/`WITH`, uma única instrução, sem comandos de escrita ou de leitura de arquivos) e roda com timeout. Mesmo assim, use um usuário de banco com permissão só de leitura.

### Opções da `InsightEngine`

| Parâmetro | Padrão | O que faz |
|---|---|---|
| `db_engine` | obrigatório | Engine do SQLAlchemy do banco a consultar. |
| `hitl` | `False` | Permite que o agente pause e peça esclarecimentos (`AWAITING_USER`). |
| `use_rag` | `True` | Seleciona tabelas e regras relevantes via GraphRAG híbrido (embeddings multilíngues ONNX + BM25 bilíngue + Árvore de Steiner sobre FKs) quando o schema é grande ou há `docs_dir`. |
| `use_bm25` | `True` | Ativa a busca léxica (BM25 bilíngue PT/EN com Snowball + RSLP) no retriever híbrido. |
| `use_embedding` | `True` | Ativa a busca vetorial (ChromaDB + ONNX `multilingual-e5-small` local por padrão; aceita override via `TEXT_TO_INSIGHT_EMBEDDING_MODEL`). |
| `steiner_apenas_tabelas` | `True` | Restringe o cálculo de caminhos de JOIN (Árvore de Steiner) apenas a arestas físicas de chave estrangeira entre tabelas. |
| `nao_expandir_regras_tabelas` | `True` | Evita expandir automaticamente todos os trechos vinculados às tabelas retornadas (`Tabela → Regra`), mantendo apenas regras recuperadas pelo RAG (reduz tamanho do prompt com dicionários longos). |
| `use_fk_boost` | `True` | Ativa o **Graph-Aware FK Boost** (Stage 2a) após o RRF e antes da Árvore de Steiner, promovendo tabelas-pai/filho de FK das âncoras com penalidade anti-hub (`1/sqrt(grau)`). |
| `use_llm_pruner` | `True` | Ativa o **LLM Pruner** (Stage 2b) após o FK-Boost para podar tabelas e regras irrelevantes antes do `code_agent` (desativado automaticamente em retries para ampliar recall). Os tokens entram em `tokens_total` e `tokens_by_node["retriever_pruner"]`. |
| `pruner_model` | `"gpt-6-luna"` | Modelo usado pelo LLM Pruner (aceita override por `pruner_api_key` e `pruner_pool_k`). |
| `docs_dir` | `None` | Caminho para pasta (ou arquivo `.md`) com regras de negócio e dicionário de dados para o retriever. |
| `enrich_rag` | `False` | Gera, com o LLM, descrições curtas das colunas antes do RAG. O resultado fica em cache. |
| `use_data_exploration` | `True` | Inclui estatísticas das colunas no prompt (SQLite e PostgreSQL; outros bancos pulam esta etapa). |
| `use_exploration_selector` | `"off"` | Escolhe quais colunas explorar: `"off"`, `"llm"` ou `"rag"` (usando o mesmo embedder multilíngue). |
| `use_cot` | `True` | Pede raciocínio passo a passo antes da SQL. |
| `inferir_fks_virtuais` | `False` | Infere chaves estrangeiras por nome (`*_id`) quando o banco não as declara. |
| `enable_graphs` | `True` | Gera um gráfico (matplotlib) quando a visualização for útil. |
| `model_kwargs` | `None` | Parâmetros do LLM, ex.: `{"temperature": 0}`. |
| `cache_dir` | pasta de cache do usuário | Onde ficam o schema enriquecido, os trechos de regras extraídos e o índice do ChromaDB. |

Por padrão o cache fica em `~/.cache/text-to-insight` (Linux), `~/Library/Caches/text-to-insight` (macOS) ou `%LOCALAPPDATA%\text-to-insight\Cache` (Windows), incluindo os pesos do modelo ONNX em `onnx_models/` (configurável por `TEXT_TO_INSIGHT_MODEL_CACHE_DIR`). O índice do RAG e o cache de regras usam hash do schema, dos documentos `.md` e do modelo de embedding: se o banco, a documentação ou o embedder mudarem, a reindexação ocorre automaticamente.

### Resultado

`run` e `resume` devolvem o estado final do grafo. Os campos mais úteis:

- `status`: `"aprovado"`, `"exec_erro"`, `"AWAITING_USER"` ou `"bloqueado_hitl"`;
- `sql_gerada`, `linhas_resultado_completo`, `total_linhas_resultado`;
- `resposta_natural`: resposta em linguagem natural;
- `caminho_csv_resultado`, `caminho_grafico`: caminhos do CSV com o resultado completo e do gráfico, quando gerado;
- `tokens_input`, `tokens_output`, `tokens_total`, `tokens_by_node`: uso de tokens.

API pública: `text_to_insight.InsightEngine`, `text_to_insight.Graph`, `text_to_insight.EstadoTextToInsight`.

## Contexto adicional e regras de negócio (`docs_dir`)

Você pode fornecer documentação do seu domínio (regras de negócio, glossário, definições de KPIs e dicionário de dados) apontando uma pasta — ou um único arquivo `.md` — no parâmetro `docs_dir` (ou `--docs-dir` na CLI). Todos os arquivos `.md` dentro da pasta são lidos recursivamente pelo `docs_linker` e integrados ao GraphRAG.

Isso é especialmente útil quando as **perguntas e a documentação estão em português**, enquanto **tabelas e colunas do banco estão em inglês**.

### Estrutura recomendada dos arquivos `.md`

```text
docs-dir/
├── regras_negocio.md
└── dicionario.md
```

Para que o retriever ligue automaticamente cada trecho às tabelas corretas no grafo (validando contra o catálogo real do banco):

- **Regras de negócio:** escreva itens numerados ou em lista com título em negrito e cite tabelas/colunas entre crases (`` `tabela` ``) ou qualificadas (`tabela.coluna`):
  ```markdown
  ## Regras de negócio

  1. **Cliente ativo**: cliente com pedido nos últimos 90 dias em `orders.created_at`.
  2. **Pedido válido**: ignore registros com `status = 'cancelled'` na tabela `orders`.
  ```
- **Descrições de tabela ou coluna:** use títulos Markdown com o nome exato da tabela ou coluna (`` #### `orders` `` ou `` ### `orders.status` ``). O retriever usa essas descrições na busca híbrida (embeddings multilíngues + BM25 bilíngue) para localizar a tabela certa, preservando o DDL original no prompt do LLM.

### Como usar

**Como biblioteca:**
```python
engine = InsightEngine(
    api_key="...",
    model="gemini-2.5-flash",
    db_engine=db,
    docs_dir="./docs-dir",
)
```

**Via linha de comando:**
```bash
text-to-insight --db-path dados/loja.db --docs-dir ./docs-dir \
    "Quantos clientes ativos fizeram pedidos no último trimestre?"
```

<!-- ### Como o retriever usa esse contexto

1. **Busca híbrida multilíngue (Stage 1):** combina embeddings locais ONNX (`multilingual-e5-small`, espaço cosseno com prefixos assimétricos `query:` / `passage:`) e BM25 bilíngue (PT/EN sem dupla contagem), fundidos via RRF ponderado (`0.55` denso / `0.45` BM25).
2. **Graph-Aware FK Boost (Stage 2a):** reordena o pool de tabelas candidatas usando as arestas de chave estrangeira (`filho → pai` com peso maior e penalidade anti-hub `1/sqrt(grau)`).
3. **LLM Pruner (Stage 2b):** usa `gpt-6-luna` por padrão para selecionar apenas as tabelas e regras necessárias à pergunta, contabilizando os tokens na própria biblioteca (`tokens_by_node["retriever_pruner"]` e totais finais). Em retries do grafo (`tentativas_loop > 0`), o pruner é desativado automaticamente para priorizar recall.
4. **Grafo relacional / Steiner Tree (Stage 3):** regras relevantes ativam suas tabelas-alvo (com guardrail para regras amplas), a Árvore de Steiner percorre as chaves estrangeiras para incluir tabelas intermediárias de `JOIN`, e a documentação compacta de colunas (`tbl:<tabela>` / `col:<tabela>.<col>`, incluindo descrições, valores possíveis e restrições de uso) é injetada inline apenas nas tabelas finais selecionadas.
5. **Cache automático:** os trechos extraídos e os vetores ficam salvos em `cache_dir` com hash do schema + conteúdo dos `.md` + modelo de embedding (`schema_<modelo>_<hash>`). -->

## Uso via linha de comando

```bash
export GOOGLE_API_KEY=...

# arquivo SQLite (aberto somente leitura)
text-to-insight --db-path dados/loja.db --model gemini-2.5-flash --api-key-env GOOGLE_API_KEY \
    "Quais categorias vendem mais?"

# com pasta de documentação / regras de negócio
text-to-insight --db-url postgresql://usuario:senha@host:5432/banco --docs-dir ./docs-dir --hitl off \
    "Qual o ticket médio por estado?"
```

`text-to-insight --help` lista as demais opções (`--docs-dir`, `--hitl`, `--enrich-rag`, `--infer-fks`, `--cot`, `--data-exploration`, `--exploration-selector`, `--temperature`, `--max-tokens`).

## Desenvolvimento

Para contribuir a partir de um clone, siga o [guia de desenvolvimento e testes](docs/DESENVOLVIMENTO.md). Ele usa o uv para instalar o código local e as mesmas versões de dependências que os testes do GitHub usam.

- [Arquitetura](https://github.com/gruporaia/TextToInsight/blob/main/docs/ARQUITETURA.md)
- Benchmarks: [BIRD](https://github.com/gruporaia/TextToInsight/blob/main/docs/BENCHMARK_BIRD.md), [Spider 1.0 e 2.0](https://github.com/gruporaia/TextToInsight/blob/main/docs/BENCHMARK_SPIDER.md)

Desenvolvido pelo grupo RAIA (USP). Licença MIT.
