# Arquitetura de Agentes - Text-to-Insight

## Visão de Conjunto

O **Text-to-Insight** implementa um padrão de **Supervisor/Hierarchical Agent** onde um supervisor central (Planejador) orquestra múltiplos agentes especializados, cada um responsável por uma fase específica do pipeline de processamento de perguntas em código executável.

```
┌─────────────────────────────────────────────────────────┐
│         ENTRADA: PERGUNTA DO USUÁRIO                     │
└──────────────────┬──────────────────────────────────────┘
                   │
                   ▼
        ┌──────────────────────┐
        │   PLANEJADOR         │ ◄─── Supervisor Central
        │   (Orquestrador)     │
        └──────────┬───────────┘
                   │
       ┌───────────┼───────────┐
       │           │           │
       ▼           ▼           ▼
    [Schema]  [Código]   [Feedback]
       │           │           │
       └───────────┼───────────┘
                   │
                   ▼
        ┌──────────────────────┐
        │  SANDBOX             │
        │ (Execução Segura)    │
        └──────────┬───────────┘
                   │
                   ▼
        ┌──────────────────────┐
        │  CRÍTICO             │
        │ (Avaliação QA)       │
        └──────────┬───────────┘
                   │
       ┌───────────┴───────────┐
       │                       │
    [OK]                   [Revisar]
       │                       │
       ▼                       ▼
     [FIM]  ◄────────────[Cicle Feedback]
```

---

## Detalhamento de Componentes

### 1. **Nó: Planejador** (Supervisor)

**Responsabilidades:**
- Analisar entrada do usuário
- Decidir estratégia geral de execução
- Gerenciar estado de execução
- Orchestrar fluxo entre nós

**Entradas:**
- `pergunta_usuario`: Pergunta original
- `contexto_schema`: Status de disponibilidade de schema
- `feedback_critico`: Feedback anterior (se houver)

**Saídas:**
- `status`: Um de: `aguardando_schema`, `pronto_codificacao`, `revisando_estrategia`

**Lógica Simplificada:**
```python
if not schema:
    status = "aguardando_schema"  # Precisa buscar schema
elif feedback_critico:
    status = "revisando_estrategia"  # Há feedback anterior
else:
    status = "pronto_codificacao"  # Pode gerar código
```

---

### 2. **Nó: Esquema** (Schema Fetcher)

**Responsabilidades:**
- Conectar ao banco de dados
- Extrair metadados
- Fornecer contexto estrutural

**Entradas:**
- `pergunta_usuario`: Para determinar que dados buscar

**Saídas:**
- `contexto_schema`: String com metadata do banco
- `status`: `schema_obtido`

**Em Produção:**
- Conectaria a um PostgreSQL/MySQL real
- Usaria introspection do banco
- Cacheria resultados

---

### 3. **Nó: Agente de Código** (Code Generator)

**Responsabilidades:**
- Gerar código Python funcional
- Usar pergunta + schema
- Considerar tentativas anteriores

**Entradas:**
- `pergunta_usuario`: O que deve ser respondido
- `contexto_schema`: Estrutura dos dados
- `tentativas_loop`: Número de tentativas

**Saídas:**
- `codigo_gerado`: String com código Python
- `status`: `codigo_gerado`
- `tentativas_loop`: Incrementado

**Em Produção:**
- Chamaria LLM (GPT-4, Claude, etc.)
- Usaria prompts otimizados
- Validaria sintaxe antes de retornar

---

### 4. **Nó: Sandbox** (Safe Executor)

**Responsabilidades:**
- Executar código de forma segura e isolada
- Capturar output e erros
- Simular ambiente controlado

**Entradas:**
- `codigo_gerado`: Código a ser executado
- `tentativas_loop`: Para logging

**Saídas:**
- `saida_terminal`: Output da execução
- `status`: `codigo_ok` ou `erro_codigo`

**Em Produção:**
- Usaria Docker containers
- Implementaria timeouts
- Limitaria recursos (CPU, memória)
- Capturaria stdout/stderr real

---

### 5. **Nó: Crítico** (Quality Reviewer)

**Responsabilidades:**
- Avaliar qualidade do código gerado
- Verificar se responde à pergunta
- Fornecer feedback construtivo

**Entradas:**
- `pergunta_usuario`: Para validar resposta
- `codigo_gerado`: Codigo gerado
- `saida_terminal`: Resultado da execução
- `tentativas_loop`: Para ajustar limiar de aceitação

**Saídas:**
- `feedback_critico`: Avaliação detalhada
- `status`: `aprovado` ou `reprovado`

**Em Produção:**
- Chamaria LLM para análise semântica
- Implementaria métricas de qualidade
- Rastrearia histórico de decisões

---

## Roteadores Condicionais

### **Roteador: Após Sandbox**

```python
def roteador_sandbox(estado) -> Literal["critico", "planejador"]:
    if estado["status"] == "erro_codigo" and estado["tentativas"] < 3:
        return "planejador"  # Reconsidera estratégia
    elif estado["status"] == "codigo_ok":
        return "critico"     # Avalia resultado
    else:
        return "planejador"  # Reinicia
```

### **Roteador: Após Planejador**

```python
def roteador_planejador(estado) -> Literal["esquema", "agente_codigo", "fim"]:
    if not estado["contexto_schema"]:
        return "esquema"     # Busca schema
    elif estado["status"] == "pronto_codificacao":
        return "agente_codigo"  # Gera código
    else:
        return "agente_codigo"  # Default
```

### **Roteador: Após Crítico**

```python
def roteador_critico(estado) -> Literal["planejador", "fim"]:
    if estado["status"] == "aprovado":
        return "fim"         # Conclusão
    else:
        return "planejador"  # Volta para revisão
```

---

## Estruturas de Dados

### **EstadoTextToInsight (TypedDict)**

```python
class EstadoTextToInsight(TypedDict):
    pergunta_usuario: str          # Pergunta original do usuário
    contexto_schema: str           # Metadados do banco (pode ser vazio)
    codigo_gerado: str             # Código Python gerado
    saida_terminal: str            # Output da execução
    feedback_critico: str          # Feedback da avaliação
    status: str                    # Estado atual
    tentativas_loop: int           # Contador de iterações
```

**Transições de Status:**

```
iniciado
├── planejador → aguardando_schema / pronto_codificacao
├── esquema → schema_obtido
├── agente_codigo → codigo_gerado
├── sandbox → codigo_ok / erro_codigo
├── critico → aprovado / reprovado
└── [fim ou loop]
```

---

## Padrões de Fluxo

### **Caso 1: Sucesso Imediato**

```
▶ START
  │
  ├─► PLANEJADOR (status=pronto_codificacao)
  │   │
  │   └─► AGENTE_CODIGO (código gerado)
  │       │
  │       └─► SANDBOX (status=codigo_ok)
  │           │
  │           └─► CRITICO (status=aprovado)
  │               │
  │               └─► END
```

### **Caso 2: Erro com Recuperação**

```
▶ START
  │
  ├─► PLANEJADOR
  │   │
  │   └─► AGENTE_CODIGO (tentativa 1)
  │       │
  │       └─► SANDBOX (status=erro_codigo)
  │           │
  │           ├─► [RoteadorSandbox] → PLANEJADOR
  │           │   │
  │           │   └─► AGENTE_CODIGO (tentativa 2)
  │           │       │
  │           │       └─► SANDBOX (status=codigo_ok)
  │           │           │
  │           │           └─► CRITICO (status=aprovado)
  │           │               │
  │           │               └─► END
```

### **Caso 3: Necessidade de Schema**

```
▶ START
  │
  ├─► PLANEJADOR (status=aguardando_schema)
  │   │
  │   ├─► [RoteadorPlanejador] → ESQUEMA
  │   │   │
  │   │   └─► AGENTE_CODIGO
  │   │       │
  │   │       └─► SANDBOX
  │   │           │
  │   │           └─► CRITICO → END
```

---

## Princípios de Design

### ✅ **Separação de Responsabilidades**
Cada nó tem uma função única e bem definida.

### ✅ **Roteamento Flexível**
Roteadores condicionais permite fluxos dinâmicos baseado em estado.

### ✅ **Recuperação Automática**
Erros disparam feedback que leva a revisão automática.

### ✅ **Rastreabilidade**
Cada tentativa é registrada em `tentativas_loop`.

### ✅ **Extensibilidade**
Novos nós podem ser adicionados sem afetar fluxo existente.

### ✅ **Type-Safe**
TypedDict garante consistência de dados entre nós.

---

## Extensões Futuras

1. **Múltiplos Especialistas**: Adicionar nós para validação SQL, análise semântica, otimização.

2. **Persistência**: Armazenar histórico de execuções para análise.

3. **Feedback Humano**: Incluir nó para intervenção humana em casos críticos.

4. **Paralelização**: Executar múltiplas estratégias em paralelo e selecionar a melhor.

5. **Observabilidade**: Integração com LangSmith para rastreamento completo.

---

## Performance e Escalabilidade

| Aspecto | Consideração |
|---------|-------------|
| **Latência** | Minimizar chamadas ao LLM; cachear schemas |
| **Throughput** | Usar async/await para I/O; paralelizar nós onde possível |
| **Custos** | Limitar tentativas; otimizar prompts |
| **Confiabilidade** | Retry logic; timeouts; fallbacks |

---

## Segurança

⚠️ **Considerações Críticas:**

- [ ] Sanitizar código antes de executar
- [ ] Limitar acesso ao banco de dados
- [ ] Implementar autenticação e autorização
- [ ] Logar todas as consultas geradas
- [ ] Validar permissões antes de executar

---

## Conclusão

O **Text-to-Insight** fornece um framework robusto e extensível para combinar múltiplos agentes em um padrão Supervisor/Hierárquico. A arquitetura modular permite fácil manutenção, teste e evolução do sistema.
