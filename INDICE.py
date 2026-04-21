"""
Índice de Estrutura do Projeto Text-to-Insight

╔════════════════════════════════════════════════════════════════════════════╗
║                        PROJETO TEXT-TO-INSIGHT                             ║
║        Supervisor/Hierarchical Agent com LangGraph + HITL + Métricas       ║
║        Autor: Jonas Melo | Versão: 0.2.0 Alpha | Status: Fluxo Ativo       ║
╚════════════════════════════════════════════════════════════════════════════╝

ESTRUTURA DE DIRETÓRIOS:
═══════════════════════

TextToInsight/                             (Raiz do projeto)
│
├── 📄 README.md                           ⭐ COMECE AQUI - Documentação Principal
├── 📄 ARQUITETURA.md                      Detalhamento técnico da arquitetura
├── 📄 DESENVOLVIMENTO.md                  Guia de setup e desenvolvimento local
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
    ├── model_selection.py                 Seleção de modelo/provedor LLM
    ├── utils.py                           Telemetria de tokens e latência
    │
    ├── 📁 nodes/                          Nós do grafo
    │   ├── __init__.py
    │   ├── planner.py                     🧠 Nó: Planejador (Supervisor)
    │   ├── response.py                    💬 Nó: Resposta Natural Final
    │   ├── schema.py                      📊 Nó: Extrator de Schema
    │   ├── 📁 code_agent/
    │   │   ├── code_agent.py              💻 Nó: Gerador de SQL
    │   │   └── code_sql.py                🔐 Validação + Execução SQL segura
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

✅ ESTRUTURA:          Projeto modular com src/, nós, roteadores e suíte de testes em 3 camadas
✅ ESTADO:             TypedDict EstadoTextToInsight com campos de SQL, HITL, resposta e telemetria
✅ 7 NÓS:              Planejador, EsperaHumana, Schema, AgenteCódigo, Sandbox, Crítico, Resposta
✅ 3 ROTEADORES:       Sandbox, Planejador e Crítico
✅ GRAFO COMPILADO:    StateGraph + MemorySaver + interrupt_before para HITL
✅ DOCUMENTAÇÃO:       3 guias: README, ARQUITETURA, DESENVOLVIMENTO
✅ TELEMETRIA:         Tokens (input/output/total), tentativas e latência em CSV
✅ TODA EM PT-BR:      Código, variáveis, docstrings, comentários

═══════════════════════════════════════════════════════════════════════════════

ESTATÍSTICAS:
═════════════

📊 Linhas de Código:          ~1.400+ (incluindo nós, roteadores e utilitários)
📚 Arquivos Python:           15+ (src/ + main.py + testes)
📖 Documentação:              3 guias principais
🔄 Fluxos de Grafo:           4+ cenários (normal, retry, HITL, bloqueado_hitl)
🧠 Tentativas max:            3 por padrão (configurável)
⏱️  Status possíveis:         10 tipados + operacionais (aguardando_input, bloqueado_hitl)

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
5. Testar o modo HITL: --hitl on e --hitl off

═══════════════════════════════════════════════════════════════════════════════

QUALIDADE DO CÓDIGO:
════════════════════

✓ Type hints completos (TypedDict, Literal, etc)
✓ Docstrings nas principais funções
✓ Comentários explicativos em código crítico
✓ Imports organizados
✓ Nomes descritivos em português
✓ Separação clara de responsabilidades
✓ Estrutura pronta para testes
✓ Métricas registradas por execução (tokens + latência)

═══════════════════════════════════════════════════════════════════════════════

Desenvolvido com LangGraph 0.2.0+ 🚀
"""

# Este é um arquivo de índice/documentação puro
if __name__ == "__main__":
    print(__doc__)
