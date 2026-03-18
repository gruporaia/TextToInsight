"""
Índice de Estrutura do Projeto Text-to-Insight

╔════════════════════════════════════════════════════════════════════════════╗
║                        PROJETO TEXT-TO-INSIGHT                             ║
║              Supervisor/Hierarchical Agent com LangGraph                    ║
║        Autor: Jonas Melo | Versão: 0.1.0 Alpha | Status: Esqueleto        ║
╚════════════════════════════════════════════════════════════════════════════╝

ESTRUTURA DE DIRETÓRIOS:
═══════════════════════

projeto_raia/                              (Raiz do projeto)
│
├── 📄 README.md                           ⭐ COMECE AQUI - Documentação Principal
├── 📄 ARQUITETURA.md                      Detalhamento técnico da arquitetura
├── 📄 DESENVOLVIMENTO.md                  Guia de setup e desenvolvimentyo local
│
├── 🔧 pyproject.toml                      Metadados e dependências do projeto
├── 📋 requirements.txt                    Dependências pip
├── .gitignore                             Padrões ignorados pelo git
│
├── 🚀 main.py                             Script de execução principal
│
└── 📁 src/                                Código-fonte principal
    │
    ├── __init__.py                        Package root
    ├── state.py                           ⭐ TypedDict EstadoTextToInsight
    ├── graph.py                           ⭐ Grafo compilado (entry point)
    │
    ├── 📁 nodes/                          Nós do grafo
    │   ├── __init__.py
    │   ├── planner.py                     🧠 Nó: Planejador (Supervisor)
    │   ├── schema.py                      📊 Nó: Extrator de Schema
    │   ├── code_agent.py                  💻 Nó: Gerador de Código
    │   ├── sandbox.py                     🏖️  Nó: Executor Seguro
    │   └── critic.py                      🎯 Nó: Avaliador de Qualidade
    │
    └── 📁 routers/                        Roteadores Condicionais
        ├── __init__.py
        └── edges.py                       ➡️  Funções de roteamento

═══════════════════════════════════════════════════════════════════════════════

GUIA DE LEITURA RECOMENDADO:
════════════════════════════

1️⃣  Iniciante?
    └─► Leia: README.md → DESENVOLVIMENTO.md → main.py

2️⃣  Desenvolvedor?
    └─► Leia: ARQUITETURA.md → src/state.py → src/graph.py

3️⃣  Operacional?
    └─► Leia: DESENVOLVIMENTO.md → main.py → execute!

4️⃣  Estudo Profundo?
    └─► src/state.py → src/nodes/* → src/routers/edges.py → src/graph.py

═══════════════════════════════════════════════════════════════════════════════

O QUE FOI CRIADO:
═════════════════

✅ ESTRUTURA:          18 arquivos criados com tipagens, imports e estrutura completa
✅ ESTADO:             TypedDict EstadoTextToInsight com 7 campos essenciais
✅ 5 NÓS:              Planejador, Schema, AgenteCódigo, Sandbox, Crítico
✅ 2 ROTEADORES:       Roteador Sandbox, Roteador Planejador (+ Crítico integrado)
✅ GRAFO COMPILADO:    StateGraph com add_node, add_edge, add_conditional_edges
✅ DOCUMENTAÇÃO:       3 guias: README, ARQUITETURA, DESENVOLVIMENTO
✅ TODA EM PT-BR:      Código, variáveis, docstrings, comentários

═══════════════════════════════════════════════════════════════════════════════

ESTATÍSTICAS:
═════════════

📊 Linhas de Código:          ~1.200+ (sem testes)
📚 Arquivos Python:           10 (src/ + main.py)
📖 Documentação:              3 arquivos markdown (~2.000 linhas)
🔄 Fluxos de Grafo:           3+ cenários possíveis
🧠 Tentativas max:            3 por padrão (configurável)
⏱️  Status possíveis:         10+ diferentes (initiado, schema_obtido, codigo_ok, etc)

═══════════════════════════════════════════════════════════════════════════════

PRÓXIMOS PASSOS (NÃO IMPLEMENTADOS AGORA):
═══════════════════════════════════════════

❌ LLMs reais (OpenAI, Anthropic, etc)
❌ Banco de dados real (PostgreSQL, MySQL, etc)
❌ Docker/Containerização
❌ Cache de schemas
❌ Métricasde produção
❌ Observabilidade (LangSmith, DataDog, etc)
❌ Autenticação/Autorização
❌ Testes unitários (estrutura preparada)

═══════════════════════════════════════════════════════════════════════════════

PARA COMEÇAR:
═════════════

1. Ler README.md para entender o conceito
2. Executar: python main.py "Sua pergunta"
3. Rastrear logs nos outputs dos nós
4. Estudar ARQUITETURA.md para entender fluxos
5. Modificar nós mockados para suas necessidades

═══════════════════════════════════════════════════════════════════════════════

QUALIDADE DO CÓDIGO:
════════════════════

✓ Type hints completos (TypedDict, Literal, etc)
✓ Docstrings em todos os funções
✓ Comentários explicativos em código crítico
✓ Imports organizados
✓ Nomes descritivos em português
✓ Separação clara de responsabilidades
✓ Estrutura pronta para testes

═══════════════════════════════════════════════════════════════════════════════

Desenvolvido com LangGraph 0.2.0+ 🚀
"""

# Este é um arquivo de índice/documentação puro
if __name__ == "__main__":
    print(__doc__)
