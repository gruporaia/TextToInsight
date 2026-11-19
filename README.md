# Text-to-Insight

Biblioteca Python que transforma perguntas em linguagem natural em consultas SQL seguras, executadas no seu banco, com resposta final em linguagem natural (e gráfico, quando fizer sentido).

O fluxo é um grafo de agentes em [LangGraph](https://github.com/langchain-ai/langgraph): lê o schema do banco, seleciona as tabelas relevantes (GraphRAG), calcula estatísticas das colunas, gera a SQL, executa em modo somente leitura, corrige a si mesmo em caso de erro e responde. Opcionalmente pausa para pedir esclarecimentos ao usuário (human-in-the-loop).

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
| `use_rag` | `True` | Seleciona só as tabelas relevantes quando o schema é grande (GraphRAG). |
| `enrich_rag` | `False` | Gera, com o LLM, descrições curtas das colunas antes do RAG. O resultado fica em cache. |
| `use_data_exploration` | `True` | Inclui estatísticas das colunas no prompt (SQLite e PostgreSQL; outros bancos pulam esta etapa). |
| `use_exploration_selector` | `"off"` | Escolhe quais colunas explorar: `"off"`, `"llm"` ou `"rag"`. |
| `use_cot` | `True` | Pede raciocínio passo a passo antes da SQL. |
| `inferir_fks_virtuais` | `False` | Infere chaves estrangeiras por nome (`*_id`) quando o banco não as declara. |
| `enable_graphs` | `True` | Gera um gráfico (matplotlib) quando a visualização for útil. |
| `model_kwargs` | `None` | Parâmetros do LLM, ex.: `{"temperature": 0}`. |
| `cache_dir` | pasta de cache do usuário | Onde ficam o schema enriquecido e o índice do ChromaDB. |
| `verificador` | `"off"` | Confere a SQL e o resultado depois da execução. Com `"avisar"`, acrescenta um aviso no fim da resposta quando algo parece errado. |
| `verificador_jev` | `True` | Com o verificador ligado, usa o Jev (TypeSafe) como verificador principal. Precisa de `TYPESAFE_API_KEY`; sem ela, rodam só as regras. |
| `verificador_colunas` | `"nota"` | Colunas a mais na resposta: `"nota"` vira só uma observação, `"erro"` vira alerta (útil em benchmarks como o BIRD). |
| `verificador_limiar_jev` | `0.0` | Probabilidade mínima para o Jev sozinho virar alerta. Abaixo disso, o que ele viu entra só como observação. Com `0.0`, vale o nível mais provável, como antes. |

Por padrão o cache fica em `~/.cache/text-to-insight` (Linux), `~/Library/Caches/text-to-insight` (macOS) ou `%LOCALAPPDATA%\text-to-insight\Cache` (Windows). O schema enriquecido é indexado pelo conteúdo do schema: se o banco mudar, ele é gerado de novo.

### Verificador

Com `verificador="avisar"`, o sistema confere a resposta depois que a SQL roda. As regras procuram coisas como resultado vazio ou zerado, filtro com um valor que não existe no banco, colunas que a pergunta não pediu e registro escolhido com data no futuro. Algumas delas fazem consultas curtas no banco, só de leitura e com tempo limite.

O verificador não muda a resposta. Quando acha um problema, acrescenta um aviso no fim de `resposta_natural` e guarda os detalhes em `alertas_verificador` e `verificacao`. Filtros que a pergunta não pediu viram uma dúvida ("confira se é isso que você quer"), não um alerta.

Com o Jev ligado, a pergunta, a SQL, o schema e até 5 linhas do resultado vão para a API do Jev. Se os dados forem sensíveis, use `verificador_jev=False`.

### Resultado

`run` e `resume` devolvem o estado final do grafo. Os campos mais úteis:

- `status`: `"aprovado"`, `"exec_erro"`, `"AWAITING_USER"` ou `"bloqueado_hitl"`;
- `sql_gerada`, `linhas_resultado_completo`, `total_linhas_resultado`;
- `resposta_natural`: resposta em linguagem natural;
- `caminho_csv_resultado`, `caminho_grafico`: caminhos do CSV com o resultado completo e do gráfico, quando gerado;
- `tokens_input`, `tokens_output`, `tokens_total`, `tokens_by_node`: uso de tokens.

API pública: `text_to_insight.InsightEngine`, `text_to_insight.Graph`, `text_to_insight.EstadoTextToInsight`.

## Uso via linha de comando

```bash
export GOOGLE_API_KEY=...

# arquivo SQLite (aberto somente leitura)
text-to-insight --db-path dados/loja.db --model gemini-2.5-flash --api-key-env GOOGLE_API_KEY \
    "Quais categorias vendem mais?"

# qualquer banco via URL (também lida de DATABASE_URL)
text-to-insight --db-url postgresql://usuario:senha@host:5432/banco --hitl off \
    "Qual o ticket médio por estado?"
```

`text-to-insight --help` lista as demais opções (`--hitl`, `--enrich-rag`, `--infer-fks`, `--cot`, `--data-exploration`, `--exploration-selector`, `--verificador`, `--verificador-jev`, `--verificador-colunas`, `--verificador-limiar-jev`, `--temperature`, `--max-tokens`).

## Desenvolvimento

- [Guia de desenvolvimento e testes](https://github.com/gruporaia/TextToInsight/blob/main/docs/DESENVOLVIMENTO.md)
- [Arquitetura](https://github.com/gruporaia/TextToInsight/blob/main/docs/ARQUITETURA.md)
- Benchmarks: [BIRD](https://github.com/gruporaia/TextToInsight/blob/main/docs/BENCHMARK_BIRD.md), [Spider 1.0 e 2.0](https://github.com/gruporaia/TextToInsight/blob/main/docs/BENCHMARK_SPIDER.md)

Desenvolvido pelo grupo RAIA (USP). Licença MIT.
