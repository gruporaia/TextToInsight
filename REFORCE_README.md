# ReFoRCE - Text-to-Insight

## ⚡ Quick Start

```bash
cd TextToInsight && source venv/bin/activate

# Testar com 4 perguntas Spider2
python scripts/test_spider2_eval_reforce.py --sample-size 4

# Resultados em: results/spider2_reforce_*.csv
```

---

## 🏗️ Como Funciona

**ReFoRCE = Map 5 candidatos em paralelo → Votação por consenso → Exploração se ambíguo**

```
Pergunta
   ↓
[Planejador] Decide estratégia
   ↓
[Fan-out] Cria 5 Send objects (indice 0-4)
   ↓
[Sub-grafo × 5] Cada candidato:
   - llm_gera_sql_candidato (com prompt diversity)
   - sandbox_validacao_candidato (executa + retry até 3x)
   ↓
[Fan-in] Agrega 5 EstadoCandidato
   ↓
[Votação] Calcula MD5 de cada resultado:
   - Se ≥3 convergem → CONSENSO ✅
   - Senão → AMBÍGUO ⚠️
   ↓
[Exploração] Se ambíguo (max 2 rodadas):
   - Chain-of-Thought: analisa divergências
   - Refina pergunta se necessário
   - Retorna ao Fan-out
   ↓
[Resposta] Final (consenso ou fallback)
```

---

## 📁 Estrutura

### Core (`text_to_insight/`)

| Arquivo | O que faz |
|---------|----------|
| `state.py` | Define `EstadoCandidato` (1 candidato) e `EstadoTextToInsight` (estado global) |
| `graph.py` | Constrói StateGraph: Fan-out → Sub-grafo × 5 → Votação → Exploração |
| `nodes/voting_node.py` | Agrupa candidatos por hash MD5, aprova se ≥3 convergem |
| `nodes/exploration.py` | Chain-of-Thought para investigar divergências |
| `nodes/code_agent/code_agent.py` | `llm_gera_sql_candidato()`: gera SQL com 5 prompts diferentes |
| `nodes/sandbox.py` | `sandbox_validacao_candidato()`: executa + retry automático |
| `routers/edges.py` | `roteador_fan_out()`: cria 5 Send objects<br/>`roteador_votacao()`: roteia pós-votação |

### Avaliação (`scripts/`)

| Arquivo | O que faz |
|---------|----------|
| `test_spider2_eval_reforce.py` | Carrega Spider2-Lite, filtra 24 instâncias com gold SQL, executa agente, coleta artefatos |
| `setup_spider2_sqlite.py` | Converte JSONs → SQLite (30 bancos em `data/spider2-lite/resource/databases/sqlite/`) |

### Dados (`data/`)

```
data/spider2-lite/
├── spider2-lite.jsonl           # 547 perguntas
├── resource/databases/sqlite/   # 30 bancos SQLite (db_name/db_name.sqlite)
└── evaluation_suite/gold/
    ├── sql/                     # 24 arquivos local*.sql (gold queries)
    └── exec_result/             # 328 CSVs com resultados esperados
```

---

## 🧪 Como Testar

### 1. **Setup Inicial** (uma vez)
```bash
cd TextToInsight
source venv/bin/activate
python scripts/setup_spider2_sqlite.py  # Gera 30 SQLite bancos
```

### 2. **Testes Rápidos**

**Com 1 pergunta** (debug):
```bash
python scripts/test_spider2_eval_reforce.py --sample-size 1
```

**Com 4 perguntas** (validação):
```bash
python scripts/test_spider2_eval_reforce.py --sample-size 4 --seed 42
```

**Com 24 perguntas** (completo, todos com gold SQL):
```bash
python scripts/test_spider2_eval_reforce.py --sample-size 24
```

**Com filtro por banco**:
```bash
python scripts/test_spider2_eval_reforce.py --sample-size 5 --db-filter E_commerce
```

### 3. **Analisar Resultados**

```bash
# Ver CSV gerado
cat results/spider2_reforce_*.csv | head -3

# Colunas principais:
# - instance_id, db_id, pergunta
# - reforce_num_candidatos (5), reforce_num_validos (quantos ok)
# - reforce_status (consenso_encontrado | ambiguo | nao_votado)
# - reforce_rodadas_exploracao (0, 1, ou 2)
# - reforce_candidatos_resumo (JSON com índices + hashes)
```

---

## 🔄 Fluxo de Dados (Estados)

### EstadoCandidato
```python
{
    "indice": 0,                    # 0-4 (para prompt diversity)
    "pergunta": "...",              # Do usuário
    "schema": "...",                # Do banco
    "db_path": "...",               # Caminho SQLite
    "sql": "SELECT ...",            # Gerada pelo LLM
    "resultado_execucao": {...},    # Resultado da execução
    "valido": True/False,           # Rodou com sucesso?
    "assinatura_resultado": "abc...", # MD5 hash
    "erro": "",                     # Se erro
    "tentativas_refinamento": 1,    # Contador retry (max 3)
}
```

### EstadoTextToInsight (campos ReFoRCE)
```python
{
    # ... campos originais (pergunta, schema, etc) ...
    
    "candidatos": [EstadoCandidato] × 5,     # 5 candidatos
    "status_consenso": "consenso_encontrado", # ou "ambiguo" | "nao_votado"
    "rodadas_exploracao": 0,                  # 0, 1, ou 2
    "sql_vencedora": "SELECT ...",           # SQL aprovada
    "motivo_ambiguidade": "...",             # Por quê ambiguo?
}
```

---

## 📊 Interpretando Resultados

| Coluna | O que significa |
|--------|-----------------|
| `reforce_num_candidatos` | Sempre ≥ 5 (Map + exploração) |
| `reforce_num_validos` | Quantos rodaram sem erro |
| `reforce_status` | `consenso_encontrado`: SQL aprovada<br/>`ambiguo`: divergência, exploração acionada<br/>`nao_votado`: erro crítico |
| `reforce_rodadas_exploracao` | Quantas vezes explorador rodou (0-2) |
| `reforce_sql_vencedora` | SQL aprovada pelo consenso (ou vazia se ambiguo) |

**Exemplo linha CSV:**
```
local210,delivery_center,"Can you identify...","WITH february_orders...",,ERRO,0.0,0.0,0.0,0.0,3904,15924,ambiguo_precisa_exploracao,40,0,ambiguo,2,,[]
                                                                                                                    ↑                      ↑ ↑           ↑
                                                                                                                    5 map + 35 explore   0 valid   2 rodadas
```

---

## 🐛 Troubleshooting

| Erro | Causa | Solução |
|------|-------|---------|
| `Banco não encontrado` | SQLite não foi convertido | Rodar `python scripts/setup_spider2_sqlite.py` |
| `no such table: orders` | Schema incorreto no prompt | Melhorar `PROMPTS_DIVERSIDADE` em `code_agent.py` |
| `reforce_num_candidatos = 0` | Fan-out retorna lista vazia | Verificar `nodo_fan_out()` em `graph.py` |
| `Expected dict, got [Send(...)]` | Nó retorna Send objects em vez de dict | Verificar que nodos fazem `return estado_dict` |

---

## 🎯 Como Estender

### Adicionar novo método de consenso
Editar `nodes/voting_node.py` - função `nos_nodo_votacao()`:
```python
# Atual: >=3 de 5
# Novo exemplo: >=4 de 5 (mais rigoroso)
if max_grupo_count >= 4:
    # consenso
```

### Adicionar mais rodadas de exploração
Editar `nodes/voting_node.py`:
```python
max_rodadas = 2  # Mudar para 3, 4, etc
```

### Ajustar prompts de diversidade
Editar `nodes/code_agent/code_agent.py` - `PROMPTS_DIVERSIDADE[]`:
- Índice 0: Focus JOINs
- Índice 1: Aggregations
- Índice 2: Simplistic
- Índice 3: Optimize
- Índice 4: Clarity

---

## 📝 Checklist para Novo Dev

- [ ] `source venv/bin/activate`
- [ ] Rodar `python scripts/setup_spider2_sqlite.py`
- [ ] Testar com `--sample-size 1` (debug)
- [ ] Verificar coluna `reforce_status` no CSV
- [ ] Se `nao_votado`: debugar `nodo_fan_out()`
- [ ] Se `ambiguo`: melhorar `PROMPTS_DIVERSIDADE`
- [ ] Se tudo ok: rodar `--sample-size 24` completo

---

**Última atualização:** 28/05/2026 | Status: ✅ Operacional
