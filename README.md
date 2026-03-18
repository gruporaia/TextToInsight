# Text-to-Insight: Sistema de Agentes Supervisor/Hierárquico

## Visão Geral

**Text-to-Insight** é um framework robusto baseado em **LangGraph** que implementa um padrão de **Supervisor/Hierarchical Agent**. O sistema transforma perguntas em código Python executável, gerenciando todo o ciclo de vida através de múltiplos agentes especializados.

### Fluxo Principal

O ciclo central do sistema segue este padrão:

```
Planejador → Esquema → Agente de Código → Sandbox → Crítico
     ↑                                                    ↓
     └────────────────────────────────────────────────────┘
           (Feedback e iteração conforme necessário)
```

---

## Arquitetura

### 1. **Componentes Principais**

#### `src/state.py`
Define o **EstadoTextToInsight**, um TypedDict que centraliza as informações compartilhadas entre todos os nós:

- **pergunta_usuario**: Pergunta ou solicitação do usuário
- **contexto_schema**: Metadados do banco de dados
- **codigo_gerado**: Código Python gerado
- **saida_terminal**: Resultado da execução
- **feedback_critico**: Avaliação crítica do código
- **status**: Estado atual da execução
- **tentativas_loop**: Contador de iterações

#### `src/nodes/` - Nós Especializados

1. **planejador.py** 🧠
   - Orquestra a estratégia geral
   - Decide se deve buscar schema ou proceder direto
   - Analisa feedback anterior
   - Determina próximas etapas

2. **schema.py** 📊
   - Busca contexto e metadados do banco de dados
   - Fornece estrutura de tabelas
   - Enriquece estado com informações estruturais

3. **code_agent.py** 💻
   - Gera código Python baseado no plano
   - Utiliza pergunta do usuário e contexto do schema
   - Maneja histórico de tentativas anteriores

4. **sandbox.py** 🏖️
   - Executa código em ambiente seguro e isolado
   - Captura stdout/stderr
   - Simula status de execução (sucesso/erro)
   - Progressivamente melhor com tentativas

5. **critic.py** 🎯
   - Avalia qualidade da solução
   - Verifica se responde à pergunta original
   - Fornece feedback construtivo
   - Emite veredito (aprovado/reprovado)

#### `src/routers/edges.py` - Roteadores Condicionais

1. **roteador_sandbox()**
   - Após execução do código
   - Condições:
     - Erro + tentativas < 3 → Planejador
     - Sucesso → Crítico
     - Muitas tentativas → Planejador

2. **roteador_planejador()**
   - Após planejamento
   - Condições:
     - Schema vazio → Esquema
     - Código reprovado → Agente de Código
     - Pronto → Agente de Código
     - Aprovado → Fim

---

## Fluxo de Execução Detalhado

### Cenário 1: Fluxo Ideal (Primeira Tentativa)

```
START
  ↓
[PLANEJADOR] → status="pronto_codificacao"
  ↓
[ESQUEMA] → contexto_schema preenchido
  ↓
[AGENTE_CODIGO] → código_gerado (tentativa 1)
  ↓
[SANDBOX] → status="codigo_ok" ✓
  ↓
[CRÍTICO] → status="aprovado" ✓
  ↓
END
```

### Cenário 2: Fluxo com Erro e Recuperação

```
START
  ↓
[PLANEJADOR] → status="pronto_codificacao"
  ↓
[ESQUEMA] → contexto_schema preenchido
  ↓
[AGENTE_CODIGO] → código_gerado (tentativa 1)
  ↓
[SANDBOX] → status="erro_codigo" ✗
  ↓
[PLANEJADOR] → status="revisando_estrategia" (reconsidera)
  ↓
[AGENTE_CODIGO] → código_gerado (tentativa 2 - melhorado)
  ↓
[SANDBOX] → status="codigo_ok" ✓
  ↓
[CRÍTICO] → status="aprovado" ✓
  ↓
END
```

### Cenário 3: Múltiplas Iterações

```
START
  ↓
[PLANEJADOR] → schema vazio
  ↓
[ESQUEMA] → contexto_schema preenchido
  ↓
[AGENTE_CODIGO] → código_gerado (tentativa 1)
  ↓
[SANDBOX] → status="codigo_ok" ✓
  ↓
[CRÍTICO] → status="reprovado" (qualidade insuficiente)
  ↓
[PLANEJADOR] → status="revisando_estrategia"
  ↓
[AGENTE_CODIGO] → código_gerado (tentativa 2)
  ↓
[SANDBOX] → status="codigo_ok" ✓
  ↓
[CRÍTICO] → status="aprovado" ✓
  ↓
END
```

---

## Estrutura de Arquivos

```
projeto_raia/
├── README.md                  # Este arquivo
├── src/
│   ├── __init__.py
│   ├── state.py              # TypedDict EstadoTextToInsight
│   ├── graph.py              # Grafo compilado principal
│   ├── nodes/
│   │   ├── __init__.py
│   │   ├── planner.py        # Nó Planejador
│   │   ├── schema.py         # Nó Schema
│   │   ├── code_agent.py     # Nó Agente de Código
│   │   ├── sandbox.py        # Nó Sandbox
│   │   └── critic.py         # Nó Crítico
│   └── routers/
│       ├── __init__.py
│       └── edges.py          # Roteadores Condicionais
└── requirements.txt          # Dependências do projeto
```

---

## Dependências

```
langgraph>=0.1.0
langchain>=0.1.0
python>=3.10
```

---

## Uso

### Exemplo Básico

```python
from src.graph import grafo_text_to_insight

# Estado inicial
estado = {
    "pergunta_usuario": "Qual é o produto mais vendido?",
    "contexto_schema": "",
    "codigo_gerado": "",
    "saida_terminal": "",
    "feedback_critico": "",
    "status": "iniciado",
    "tentativas_loop": 0,
}

# Executar grafo
resultado = grafo_text_to_insight.invoke(estado)

# Acessar resultados
print(f"Código gerado:\n{resultado['codigo_gerado']}")
print(f"Saída:\n{resultado['saida_terminal']}")
print(f"Status final: {resultado['status']}")
```

---

## Padrão de Design

### Supervisor/Hierarchical Agent

Este padrão implementa:

✅ **Separação de Responsabilidades**: Cada nó tem uma função específica  
✅ **Roteamento Inteligente**: Decisões condicionais baseadas em estado  
✅ **Iteração Automática**: Recuperação de erros sem intervenção manual  
✅ **Escalabilidade**: Fácil adicionar novos nós e roteadores  
✅ **Rastreabilidade**: Histórico completo de tentativas  

---

## Mockeds e Simulações

⚠️ **Nota**: Este é um **esqueleto/protótipo**. As seguintes funcionalidades são simuladas:

- ❌ LLMs reais (OpenAI, Anthropic, etc.)
- ❌ Banco de dados real
- ❌ Execução real de código Python
- ❌ Docker/containerização
- ✅ Estrutura e fluxo de grafo
- ✅ Tipagens e interfaces
- ✅ Roteamento condicional
- ✅ Estado compartilhado

---

## Próximas Etapas (Implementação Completa)

Para transformar este esqueleto em produção:

1. **Substituir LLMs Mockados**
   - Adicionar chamadas reais a ChatGPT/Claude
   - Implementar prompts eficazes para cada nó

2. **Integrar Banco de Dados Real**
   - Conectar ao PostgreSQL/MySQL
   - Obter schemas reais dinamicamente

3. **Sandbox Seguro**
   - Usar Docker ou containers
   - Implementar timeouts e limites de recursos
   - Capturar saída real (stdout/stderr)

4. **Persistência**
   - Armazenar histórico de execuções
   - Rastrear métricas de sucesso

5. **Testes**
   - Testes unitários para cada nó
   - Testes integrados do grafo

---

## Contribuição

Este é um projeto educacional. Para melhorias, sugestões ou correções, abra uma issue ou pull request.

---

## Licença

Documentação e código disponível sob licença aberta. Verifique LICENSE para detalhes.

---

**Desenvolvido com ❤️ usando LangGraph**
