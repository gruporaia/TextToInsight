# 🚀 TextToInsight ReFoRCE - Implementação Completa

## 📋 Status: ✅ 100% Implementado

Todas as 9 fases foram completadas com sucesso!

## 🏗️ Arquitetura ReFoRCE

**ReFoRCE** = **Re**finement + **Fo**rconsensus + **R**elaxation + **C**ontention resolution + **E**xploration

### 1️⃣ **MAP-REDUCE (Parallelização de Candidatos)**
- **Fan-out**: Planejador → 5 Send objects em paralelo
- **Sub-grafo**: gerador_candidato com self-refinement retry (max 3 tentativas)
- **Fan-in**: Agregação automática via reducer de LangGraph

### 2️⃣ **SELF-REFINEMENT (Retry Automático)**
- Detecta erros retry-able (sintaxe, timeout)
- Reexecuta llm_gera_sql_candidato com contexto acumulado
- Máximo 3 tentativas por candidato

### 3️⃣ **CONSENSUS VOTING (Reduce)**
- Calcula hash (MD5) de cada resultado
- Aprova se ≥3 de 5 candidatos convergem
- Salva SQL vencedora

### 4️⃣ **EXPLORATION (Ambiguidade)**
- Se sem consenso: Chain-of-Thought LLM analysis
- Refinamento de pergunta se necessário
- Máximo 2 rodadas (depois fallback)

### 5️⃣ **FALLBACK PATHS**
- Consenso → salvar_csv
- Ambiguo → explorador (retry Fan-out com pergunta refinada)
- Exploracao≥2 rodadas → resposta

---

## 📁 Estrutura de Arquivos Modificados

### Core ReFoRCE Components
```
text_to_insight/
├── state.py                          # ✅ EstadoCandidato + reducers
├── graph.py                          # ✅ Refatorado com sub-grafo + Fan-out/Fan-in
├── nodes/
│   ├── voting_node.py                # ✅ Consenso voting logic
│   ├── exploration.py                # ✅ Chain-of-Thought analysis
│   ├── code_agent/code_agent.py      # ✅ llm_gera_sql_candidato + PROMPTS_DIVERSIDADE
│   ├── sandbox.py                    # ✅ sandbox_validacao_candidato + retry logic
│   └── __init__.py                   # ✅ Exports votacao + explorador
├── routers/
│   ├── edges.py                      # ✅ roteador_fan_out + roteador_votacao
│   └── __init__.py                   # ✅ Exports novos roteadores
```

### Testes & Avaliação
```
tests/
├── test_reforce_basic.py             # ✅ Testes unitários dos componentes ReFoRCE
scripts/
├── test_spider2_eval_reforce.py      # ✅ Avaliação Spider2 com coleta de artefatos
```

---

## 🧪 Como Usar

### 1. Ativar Ambiente Virtual
```bash
cd /home/jonasmelo/projectsandstudies/TextToInsight
source venv/bin/activate
```

### 2. Rodar Testes Básicos
```bash
python tests/test_reforce_basic.py
```

**Saída esperada:**
```
======================================================================
FASE 8: TESTES BÁSICOS DA ARQUITETURA ReFoRCE
======================================================================

[TEST] roteador_fan_out - criação de Send objects
  ✅ 5 Send objects criados corretamente
  ✅ Cada Send contém EstadoCandidato com contexto
...
✅ TODOS OS TESTES PASSARAM!
```

### 3. Avaliar contra Spider 2.0 Lite

#### 3a. Com amostra pequena (5 perguntas):
```bash
python scripts/test_spider2_eval_reforce.py --sample-size 5 --seed 42
```

#### 3b. Com dataset completo (547 perguntas):
```bash
python scripts/test_spider2_eval_reforce.py --sample-size 547 --seed 42
```

#### 3c. Filtrar por banco específico:
```bash
python scripts/test_spider2_eval_reforce.py --db-filter E_commerce --sample-size 10
```

### 4. Analisar Resultados

Resultados salvos em: `results/spider2_reforce_YYYYMMDD_HHMMSS.csv`

**Colunas principais:**
- `instance_id`, `db_id`, `pergunta`
- `query_ouro`, `query_agente`
- `match_exato`, `f1_score`, `precision`, `recall`, `similarity_sql`
- **ReFoRCE artifacts:**
  - `reforce_num_candidatos` (sempre 5)
  - `reforce_num_validos` (quantos rodaram com sucesso)
  - `reforce_status` (consenso_encontrado | ambiguo | nao_votado)
  - `reforce_rodadas_exploracao` (0, 1, or 2)
  - `reforce_candidatos_resumo` (JSON com índices/hashes)

---

## 🔍 Exemplo de Execução Completa

```bash
# 1. Preparar ambiente
cd /home/jonasmelo/projectsandstudies/TextToInsight
source venv/bin/activate

# 2. Testar componentes ReFoRCE
python tests/test_reforce_basic.py

# 3. Avaliar com 10 perguntas Spider2
python scripts/test_spider2_eval_reforce.py --sample-size 10 --seed 42

# 4. Verificar resultados
ls -lh results/spider2_reforce_*.csv
head -5 results/spider2_reforce_*.csv
```

---

## 📊 Métricas Coletadas

### Por Pergunta
- **Exatidão**: Match exato, F1-score, Precision, Recall
- **Similaridade SQL**: Usando SequenceMatcher (0-1)
- **ReFoRCE**:
  - Candidatos válidos gerados
  - Status de consenso
  - Rodadas de exploração acionadas

### Agregado
- Taxa de exact match
- F1-score médio
- Taxa de consenso ReFoRCE
- Tokens consumidos
- Tempo de execução

---

## 🎯 Próximos Passos (Opcional)

1. **Ablação Study**: Comparar ReFoRCE vs single-shot vs critic-only
2. **Fine-tuning**: Treinar prompts de diversidade com SFT/DPO
3. **Profiling**: Analisar overhead de parallelização vs overhead de LLM
4. **Deployment**: Containerizar com Docker para produção

---

## 📝 Referência de Estados

### EstadoCandidato
```python
{
    "indice": 0,                          # 0-4
    "pergunta": "...",                    # Contexto
    "schema": "...",                      # Contexto
    "db_path": "...",                     # Contexto
    "historico_tentativas": [...],        # Contexto
    
    "sql": "SELECT ...",                  # Resultado
    "resultado_execucao": {...},          # Resultado
    "valido": True/False,                 # Resultado
    "assinatura_resultado": "abc...",     # Hash MD5
    "erro": "",                           # Se erro retry-able
    "tentativas_refinamento": 1,          # Contador
}
```

### EstadoTextToInsight (ReFoRCE fields)
```python
{
    # ... campos originais ...
    
    # ReFoRCE
    "candidatos": [EstadoCandidato, ...],           # 5 candidatos
    "status_consenso": "consenso_encontrado",       # ou "ambiguo"
    "rodadas_exploracao": 0,                        # 0, 1, or 2
    "sql_vencedora": "SELECT ...",                  # SQL aprovada
    "motivo_ambiguidade": "...",                    # Se ambiguo
}
```

---

## ✨ Componentes-Chave

### Nós (Nodes)
- `nos_nodo_votacao`: Consensus voting
- `nos_nodo_explorador`: Chain-of-Thought analysis

### Roteadores (Routers)
- `roteador_fan_out()`: Cria 5 Send objects
- `roteador_votacao()`: Roteia post-consensus
- `roteador_planejador_reforcado()`: Integra Fan-out

### Funções Utilitárias
- `llm_gera_sql_candidato()`: Gera SQL com diversidade
- `sandbox_validacao_candidato()`: Executa + retry
- `calcular_assinatura_resultado()`: MD5 hash para consenso

---

## 🐛 Troubleshooting

### Erro: "cannot import name 'roteador_fan_out'"
**Solução:** Verificar que `routers/__init__.py` exporta os novos roteadores ✅

### Erro: "EstadoCandidato missing field 'pergunta'"
**Solução:** Verificar que `roteador_fan_out()` popula contexto antes de Send ✅

### Grafo não converge
**Solução:** Verificar `graph.py` linha de `gerador_candidato` → `votacao` ✅

---

## 📞 Suporte

Para dúvidas ou problemas:
1. Verificar logs do grafo (ativar `print` em nodes)
2. Rodar `test_reforce_basic.py` para validar componentes
3. Inspecionar `resultado.get("candidatos")` no código

---

**Última atualização:** 27/05/2026
**Versão:** ReFoRCE v1.0 - Production Ready ✅
