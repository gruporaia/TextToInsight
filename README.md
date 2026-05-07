# Text-to-Insight

Biblioteca Python para transformar perguntas em linguagem natural em SQL executada com seguranca em SQLite, com avaliacao automatica e resposta final em linguagem natural.

O namespace oficial do pacote e `text_to_insight`.

## Contrato minimo

O runtime padrao garante:

- fluxo completo do grafo (planejador -> schema -> agente de codigo -> executor -> critico -> resposta);
- HITL ligado e desligado;
- retomada por `thread_id`;
- persistencia de metricas em `data/metricas_execucao.csv`.

## Instalacao

```bash
pip install -r requirements.txt
pip install -e .
```

Configure a chave da API:

```bash
echo "GOOGLE_API_KEY=sua_chave_aqui" > .env
```

## Uso como biblioteca

```python
from text_to_insight import InsightEngine

engine = InsightEngine(
    api_key="...",
    model="gemini-2.5-flash",
    db_path="data/olist_relational.db",
    hitl=True,
)

resultado = engine.run(
    thread_id="sessao_1",
    query="Quantos pedidos existem no banco?",
)

if resultado.get("status") == "AWAITING_USER":
    resultado = engine.resume(
        thread_id="sessao_1",
        user_response="Pode assumir status entregue.",
    )
```

API publica congelada do pacote:

- `text_to_insight.InsightEngine`
- `text_to_insight.Graph`
- `text_to_insight.EstadoTextToInsight`

Imports antigos via `src` nao sao mais suportados.

## Uso via CLI

O arquivo `main.py` e um adaptador fino da CLI da biblioteca.

```bash
# via adaptador local
python main.py --hitl on "Quais categorias vendem mais?"

# modo nao interativo
python main.py --hitl off "Quais categorias vendem mais?"

# via entrypoint instalado pelo pacote
text-to-insight --hitl on "Quantos pedidos existem no banco?"
```

### Saida do terminal

Apos a execucao, o resultado da query e exibido no bloco `RESULTADO` em formato tabular:

```
----------------------------------------------------------------------
RESULTADO:
----------------------------------------------------------------------
+------------+
|   COUNT(*) |
+============+
|      99441 |
+------------+
Total de linhas retornadas: 1
```

O template de apresentacao usa `tabulate` para montar as linhas da query:
- Ate 5 linhas: exibe a tabela completa
- Acima de 5 linhas: mostra as 3 primeiras, omite as intermediarias, exibe as 2 ultimas
- Resultado completo e exportado em CSV em `results/` automaticamente

## Testes

Camadas atuais:

```bash
# Camada 1 - componentes deterministicos (sem API)
pytest tests/test_componentes.py -v -s

# Camada 2 - nos individuais com VCR (replay, sem gravar)
pytest tests/test_nodes.py -v -s --record-mode=none

# Camada 2 - nos individuais com VCR (gravar/atualizar cassetes)
pytest tests/test_nodes.py -v -s --record-mode=new_episodes

# Camada 3 - integracao do grafo com VCR (replay, sem gravar)
pytest tests/test_integracao.py -v -s --record-mode=none

# Camada 3 - integracao do grafo com VCR (gravar/atualizar cassetes)
pytest tests/test_integracao.py -v -s --record-mode=new_episodes

# Integracao dedicada main + InsightEngine
pytest tests/test_main_engine_integracao.py -v -s
```

Fluxo recomendado de gravacao VCR:

```bash
# gravar/atualizar
pytest tests/test_nodes.py tests/test_integracao.py -v -s --record-mode=new_episodes

# validar replay deterministico
pytest tests/test_nodes.py tests/test_integracao.py -v -s --record-mode=none
```

As cassetes ficam em `tests/cassettes/`.

Teste opcional com API real (drift provider/modelo):

```bash
pytest tests/test_real_api_smoke.py -v -s -m real_api
```

## CI hibrida

Workflow em `.github/workflows/ci.yml`:

- `tests-vcr`: job padrao em PR/push com execucao deterministica (`--record-mode=none`);
- `record-vcr-cassettes`: job manual em `workflow_dispatch` para gravar/atualizar cassetes e publicar artifact;
- `tests-real-api`: job opcional manual/noturno com `GOOGLE_API_KEY` real para detectar drift.

## Validacao de distribuicao

```bash
python -m build

python -m venv .venv-smoke
source .venv-smoke/bin/activate
pip install dist/*.whl

python -c "from text_to_insight import InsightEngine, Graph; print('import_ok')"
```
