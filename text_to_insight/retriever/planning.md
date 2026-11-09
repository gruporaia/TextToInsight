# Planejamento: Evolução do GraphRAG Heterogêneo (TextToInsight)

Este documento estabelece o roadmap de arquitetura e implementação para unificar e expandir o sistema de recuperação do `text_to_insight`.

O objetivo principal é transformar o atual grafo homogêneo de tabelas em um **Grafo Heterogêneo Unificado** residente exclusivamente dentro do pacote `text_to_insight/retriever`, eliminando a fragmentação de RAGs efêmeros e permitindo a assimilação de **regras de negócio documentais (PDFs/textos)** e **metadados granulares de colunas**.

---

## Sumário do Plano

- **1° Passo: Ingestão de Contexto Adicional (Regras de Negócio) e Grafo Híbrido**
  - Integração de regras de negócio adicionais em `engine.py`, `rag_logic.py` e `graph_logic.py`.
  - Introdução de Busca Híbrida (Dense Vector + BM25) para resolução de entidades léxicas e conceituais.
  - Conexão topológica entre regras documentais e tabelas no NetworkX.
- **2° Passo: Refactor Completo, Unificação do RAG de Colunas e Benchmark BIRD**
  - Migração da lógica de seleção de colunas (atualmente dispersa em `nodes/data_exploration.py`) para dentro de `retriever/`.
  - Inclusão das colunas como nós de 1ª classe no Grafo Heterogêneo.
  - Centralização de todo o ciclo de vida de busca, indexação e cache em um único motor (`SchemaGraphRAG`).
  - Criação de um Benchmark Automatizado baseado no dataset BIRD para mensuração isolada e contínua de **Recall** e **Precision** do RAG final (tabelas e regras de negócio/evidences necessárias).

---

# Arquitetura Detalhada

```
                                  GRAFO HETEROGÊNEO (NetworkX)
  ┌────────────────────────────────────────────────────────────────────────────────┐
  │                                                                                │
  │   [ 📄 Regra de Negócio ]                                                      │
  │           │                                                                    │
  │           │ :aplica_se_a                                                       │
  │           ▼                                                                    │
  │   [ 🗄️ Tabela: pedidos ] ──────:foreign_key──────► [ 🗄️ Tabela: clientes ]     │
  │           │                                                │                   │
  │           │ :tem_coluna                                    │ :tem_coluna       │
  │           ▼                                                ▼                   │
  │   [ 🏷️ Coluna: status ]                           [ 🏷️ Coluna: id ]            │
  │                                                                                │
  └────────────────────────────────────────────────────────────────────────────────┘
```

---

## 1° Passo: Contexto Adicional + Tabelas no GraphRAG

O objetivo desta etapa é permitir que o sistema absorva documentação corporativa (ex: manuais de regras, políticas de cálculo de KPIs, critérios de cancelamento) e correlacione essas diretrizes diretamente com as tabelas do banco de dados.

### 1.1 Etapa Offline (Ingestão, Schema Linking e Cache)
Executada na inicialização ou quando um novo banco/documento é fornecido. O resultado é persistido em cache para evitar custos repetidos de LLM ou embeddings.

1. **Introspecção do Banco de Dados (Já implementada):**
   - Extrai o DDL do banco via `nodes/schema.py` (PRAGMA SQLite ou SchemaCrawler).
   - Registra as tabelas e Foreign Keys iniciais.

2. **Ingestão e Chunking do Contexto Adicional (PDF/Texto):**
   - Extração do texto bruto com preservação de tópicos/seções (via leitor estruturado, ex: `pypdf`).
   - Segmentação do documento em blocos semânticos de regras (200 a 500 tokens) -> .md ajuda no split de seções.

3. **Schema Linking (Conexão Regras $\leftrightarrow$ Tabelas):**
   - **Extração Semântica Estruturada (LLM em Batch):** O LLM é o decisor semântico responsável por ler o trecho do documento, traduzir termos de negócio, gírias e sinônimos corporativos (ex: *"vendas faturadas"* $\rightarrow$ `orders`, *"comprador"* $\rightarrow$ `customers`) e mapear para o catálogo do banco.
     - Para bancos grandes (mais de 40 tabelas), aplica-se um pré-filtro léxico/BM25 para injetar apenas as tabelas candidatas mais prováveis no prompt do LLM.
   - **Validação Determinística (Guardrail Pós-LLM):** Não se usa Regex para adivinhar tabelas no texto livre (devido ao risco de homônimos e falsos positivos como *"in order to"*). O regex apenas valida programmaticamente se as `tabelas_alvo` emitidas no JSON de fato existem no catálogo do banco, descartando alucinações antes da montagem do grafo.
   - Formato estruturado gerado:
     ```json
     {
       "id": "rule_churn_90d", 
       "titulo": "Definição de Churn",
       "descricao": "Clientes sem compras há mais de 90 dias.",
       "formula_sql": "pedidos.data_pedido < DATE('now', '-90 days')", //se houver
       "tabelas_alvo": ["clientes", "pedidos"],
     }
     ```

4. **Construção do Grafo Heterogêneo (`graph_logic.py`):**
   - Adiciona nós de tabelas: `graph.add_node("pedidos", tipo="tabela", ddl=...)`.
   - Adiciona arestas de Foreign Keys: `graph.add_edge("pedidos", "clientes", tipo="foreign_key", ...)`.
   - Adiciona nós de regras: `graph.add_node("rule_churn_90d", tipo="regra", dados=...)`.
   - Adiciona arestas semânticas: `graph.add_edge("rule_churn_90d", "pedidos", tipo="aplica_se_a")`.

5. **Indexação Híbrida e Desacoplada (`rag_logic.py`):**
   - **Desacoplamento Estrutural (Sem Colagem de Texto):** As tabelas **NÃO** são enriquecidas com o texto das regras dentro de seus chunks. Chunks de tabela contêm estritamente DDL limpo; chunks de regras contêm estritamente as diretrizes de negócio. A ponte entre elas é mantida **exclusivamente pelas arestas do Grafo**, evitando poluição semântica (*semantic drift*) dos vetores das tabelas e permitindo invalidação independente de cache.
   - **ChromaDB (Vetor Denso) -> Vira .bin na pasta chroma_db:** 
     - Índice de Tabelas: DDL estruturado de cada tabela.
     - Índice de Regras: Chunks individuais de cada regra (título, descrição, fórmula).
   - **BM25 (Léxico com Snake_Case Splitting):** 
     - Índice Léxico de Tabelas/Colunas: Identificadores técnicos tokenizados (`order_status` $\rightarrow$ `order`, `status`).
     - Índice Léxico de Regras: Siglas, termos de negócio e palavras-chave das regras.
   - **Persistência em Cache:** Grava `schema_{hash}.json` e `regras_vault_{hash}.json` vinculados ao hash do banco/documento -> Para NetworkX (grafo)

### 1.2 Etapa Online (Inferência em Runtime)
Executada quando o usuário envia uma consulta no fluxo do LangGraph (`nodes/retriever.py`).

1. **Busca Híbrida de Sementes (RRF):**
   - Executa busca paralela (ChromaDB + BM25) para a pergunta do usuário.
   - Funde os rankings via Reciprocal Rank Fusion (RRF), elegendo os nós-semente de maior relevância (podendo ser tabelas ou regras).

2. **Travessia e Ativação no Grafo (`graph_logic.py`):**
   - **Expansão Regra $\rightarrow$ Tabela:** Para cada regra ativada, o grafo percorre as arestas `aplica_se_a` e ativa as tabelas necessárias para o cálculo.
   - **Expansão Tabela $\rightarrow$ Regra:** Para cada tabela recuperada, o grafo busca regras de negócio vizinhas que definam filtros obrigatórios.

3. **Resolução de Caminhos Mínimos e Conexões (Árvore de Steiner Adaptada):**
   - Com o conjunto consolidado de tabelas terminais, o algoritmo de Árvore de Steiner (`nx.algorithms.approximation.steiner_tree`) identifica tabelas intermediárias de junção (pontes de Foreign Key) necessárias para conectar as ilhas relacionais.
   - Tabelas isoladas recuperadas por falso-positivo de busca textual que não possuem caminho de Foreign Key com as tabelas principais são podadas (*structural pruning*).

4. **Emissão de Contexto Unificado:**
   - O `engine.py` formata e devolve ao estado do LangGraph:
     - `contexto_rag_schema`: DDL mínimo das tabelas + JOINs recomendados.
     - `contexto_regras_negocio`: Instruções e fórmulas extraídas das regras de negócio ativadas.

---

## 2° Passo: Refactor Completo, Unificação do RAG de Colunas e Benchmark BIRD

O objetivo desta etapa é eliminar a fragmentação causada por `_exploration_selector_rag` em `nodes/data_exploration.py` (que cria coleções efêmeras `cols_{hash}` no ChromaDB durante o runtime) e centralizar todo o conhecimento relacional no mesmo Grafo Heterogêneo.

### 2.1 Etapa Offline (Grafo em Nível de Colunas)
As colunas deixam de ser strings soltas dentro do DDL e passam a ser cidadãs de primeira classe no grafo.

1. **Indexação Granular de Colunas no Grafo:**
   - Cada coluna torna-se um nó: `graph.add_node("pedidos.status", tipo="coluna", data_type="TEXT")`.
   - Arestas de pertencimento: `graph.add_edge("pedidos", "pedidos.status", tipo="tem_coluna")`.
   - Arestas de FK a nível de coluna: `graph.add_edge("pedidos.cliente_id", "clientes.id", tipo="fk_link")`.
   - Arestas de dependência de regras: `graph.add_edge("rule_churn_90d", "pedidos.status", tipo="utiliza_coluna")`.

2. **Índice Unificado de Colunas:**
   - As colunas de todas as tabelas são indexadas de forma persistente (BM25 + Vetor) junto com o schema, indexando nomes, tipos, comentários de enriquecimento e estatísticas básicas de perfilamento.

### 2.2 Etapa Online (Consulta Unificada em Uma Única Passada)
A consulta deixa de rodar em dois momentos desconexos (primeiro tabelas, depois colunas em runtime) e passa a ser resolvida em uma única travessia topológica.

1. **Eliminação do ChromaDB Efêmero em `data_exploration.py`:**
   - O nó `exploration_selector` é refatorado: em vez de criar coleções dinâmicas `cols_{hash}` em disco, ele apenas lê o campo `colunas_para_explorar` já pré-computado com precisão pelo motor unificado do `retriever/`.
2. **Priorização Guiada por Regras:**
   - Colunas vinculadas a regras de negócio ativadas recebem prioridade máxima de exploração (ex: distribuição de valores distintos em `pedidos.status`).

### 2.3 Benchmark Automatizado de Retrieval (Baseado no Dataset BIRD)

Para validar empiricamente a eficácia do RAG/GraphRAG antes de submeter o sistema ao custo de geração e execução de SQL pelo LLM, será desenvolvido um pipeline de benchmark automatizado dedicado a mensurar o **Recall** e a **Precision** das entidades recuperadas.

O benchmark utilizará os dados do **BIRD (Parte de Train do benchmark)**, poderiamos tambem reaproveitar a infraestrutura já construída em `src/bird/data_loader.py`.

#### A. Extração do Ground Truth (Gabarito) por Exemplo:
Para cada pergunta do BIRD, são extraídos automaticamente os três gabaritos de recuperação:
1. **Tabelas Ouro (`Gold Tables`):** Extraídas via regex da consulta SQL de referência.
2. **Regras de Negócio Ouro (`Gold Evidence`):** Extraídas do campo `evidence` do BIRD. No setup do benchmark, o conjunto de todas as evidences do banco de dados é pré-indexado como nós de regra no Grafo Heterogêneo.
3. **Colunas Ouro (`Gold Columns`):** Extraídas das cláusulas `SELECT`, `WHERE`, `JOIN`, `GROUP BY` e `ORDER BY` da consulta SQL de referência.

#### B. Execução Isolada (Zero Custo de Tokens LLM na Inferência):
O benchmark executa estritamente a etapa de recuperação:
```python
resultado = schema_graph_rag.retrieve(exemplo["question"], top_k=top_k)
tabelas_recuperadas = set(resultado["tables"])
regras_recuperadas = set([r["id"] for r in resultado["business_rules"]])
colunas_recuperadas = set(resultado["recommended_columns"])
```

#### C. Métricas Formais Computadas:
- **Table Recall & Precision:**
  $$\text{Recall}_{\text{tabela}} = \frac{|\text{Tabelas Recuperadas} \cap \text{Tabelas Gold}|}{|\text{Tabelas Gold}|}$$
  $$\text{Precision}_{\text{tabela}} = \frac{|\text{Tabelas Recuperadas} \cap \text{Tabelas Gold}|}{|\text{Tabelas Recuperadas}|}$$
  *(Table Recall baixo inviabiliza a geração da query SQL correta; Table Precision baixa polui o contexto e aumenta o custo de tokens).*

- **Rule / Evidence Recall & Precision:**
  $$\text{Recall}_{\text{regra}} = \frac{|\text{Regras Recuperadas} \cap \text{Evidences Gold}|}{|\text{Evidences Gold}|}$$
  *(Avalia se o RAG identificou a diretriz ou fórmula de negócio necessária para a pergunta).*

- **Column Recall & Precision:**
  $$\text{Recall}_{\text{coluna}} = \frac{|\text{Colunas Recomendadas} \cap \text{Colunas Gold}|}{|\text{Colunas Gold}|}$$

- **F1-Score Harmônico:** Média harmônica ponderada entre Precision e Recall para cada dimensão.

---

# Estrutura de Dados e Tipagem do Grafo

### Modelagem de Nós (`NetworkX MultiGraph`)

| Tipo de Nó (`tipo`) | ID Padrão | Atributos Principais |
| :--- | :--- | :--- |
| `"tabela"` | `tbl:{nome_tabela}` | `nome`, `ddl_original`, `descricao`, `tamanho_linhas` |
| `"coluna"` | `col:{tabela}.{coluna}` | `nome`, `tabela_pai`, `tipo_dado`, `is_pk`, `is_null` |
| `"regra"` | `rule:{id_regra}` | `titulo`, `descricao`, `formula_sql`, `origem_documento` |

### Modelagem de Arestas

| Tipo de Aresta (`tipo`) | Origem $\rightarrow$ Destino | Significado |
| :--- | :--- | :--- |
| `"foreign_key"` | `tbl:A` $\rightarrow$ `tbl:B` | Relacionamento referencial para cálculo de JOINs (Steiner Tree). |
| `"tem_coluna"` | `tbl:A` $\rightarrow$ `col:A.coluna` | Vínculo hierárquico entre tabela e coluna. |
| `"aplica_se_a"` | `rule:R` $\rightarrow$ `tbl:A` | A regra de negócio exige a inclusão desta tabela. |
| `"utiliza_coluna"`| `rule:R` $\rightarrow$ `col:A.coluna` | A regra filtra ou calcula com esta coluna específica. |

---

# Módulos Impactados no Repositório

### Dentro de `text_to_insight/retriever/`
1. `graph_logic.py`:
   - Evolução de `SchemaGraph` para suportar nós heterogêneos (`tipo="tabela" | "coluna" | "regra"`).
   - Métodos de busca: `get_rules_for_tables()`, `get_columns_for_subgraph()`, e adaptação da Árvore de Steiner para navegar entre as tabelas ancoradas pelas regras.
2. `rag_logic.py`:
   - Implementação de busca híbrida com classe `BM25Retriever` + `ChromaRetriever`.
   - Função de fusão via RRF (*Reciprocal Rank Fusion*).
3. `engine.py`:
   - Atualização da classe `SchemaGraphRAG` para unificar a API pública:
     ```python
     def retrieve(self, query: str, top_k: int = 5) -> dict:
         """
         Retorna:
         {
             "tables": [...],
             "relations": [...],
             "business_rules": [...],
             "recommended_columns": {"tbl": ["col1", "col2"]}
         }
         """
     ```

### Fora de `retriever/`
4. `nodes/retriever.py`:
   - Grava `contexto_regras_negocio` e `colunas_para_explorar` no estado compartilhado do LangGraph.
5. `nodes/data_exploration.py`:
   - Remoção de `_exploration_selector_rag` e da collection efêmera `cols_{hash}`. O nó consome a seleção refinada enviada pelo retriever.
6. `nodes/code_agent/code_agent.py`:
   - Inclusão do bloco `=== REGRAS DE NEGÓCIO IDENTIFICADAS ===` no prompt final de geração de SQL.
7. `state.py`:
   - Adição do campo `contexto_regras_negocio: str` no `EstadoTextToInsight`.

---

Indicações musicais para o desenvolvimento:

Development: In On It - Superheaven

Bug fix: Telefone Mudo - Trio Parada Dura

Refactoring: Duality - Slikpnot

---

# Critérios de Sucesso e Verificação

- [ ] **Métricas Alvo no Benchmark Automatizado BIRD:**
  - $\text{Table Recall} \ge 95\%$ no dev set do BIRD (garantia de que as tabelas necessárias para o SQL estejam quase sempre presentes).
  - $\text{Table Precision} \ge 65\%$ (controle de ruído para evitar estouro da janela de contexto).
  - $\text{Rule/Evidence Recall} \ge 90\%$ nas perguntas que contêm regras de negócio no BIRD.
- [ ] **Acurácia em Consultas com Regras Implícitas:** Perguntas contendo termos de negócio sem nomes técnicos de tabelas (ex: *"qual o churn trimestral?"*) ativam as regras corretas e puxam as tabelas exatas por navegação no grafo.
