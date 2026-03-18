# Guia de Desenvolvimento - Text-to-Insight

## Setup Local

### Pré-requisitos

- Python 3.10 ou superior
- pip ou conda
- Git

### Instalação

#### 1. Clonar ou Acessar o Repositório

```bash
cd ~/ProjectsAndStudies/projeto_raia
```

#### 2. Criar Ambiente Virtual

```bash
# Com venv
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# ou
venv\Scripts\activate  # Windows

# Com conda
conda create -n text-to-insight python=3.10
conda activate text-to-insight
```

#### 3. Instalar Dependências

```bash
pip install -r requirements.txt

# Ou para desenvolvimento (com ferramentas de teste):
pip install -e ".[dev]"
```

#### 4. Validar Instalação

```bash
# Testar import básico
python -c "from src.graph import grafo_text_to_insight; print('✓ Import bem-sucedido')"
```

---

## Estrutura de Diretórios

```
projeto_raia/
├── src/                          # Código-fonte principal
│   ├── __init__.py              # Package root
│   ├── state.py                 # Definição do estado
│   ├── graph.py                 # Grafo compilado
│   ├── nodes/                   # Nós do grafo
│   │   ├── __init__.py
│   │   ├── planner.py           # Planejador
│   │   ├── schema.py            # Extrator de schema
│   │   ├── code_agent.py        # Gerador de código
│   │   ├── sandbox.py           # Executor seguro
│   │   └── critic.py            # Avaliador de qualidade
│   └── routers/                 # Roteadores condicionais
│       ├── __init__.py
│       └── edges.py             # Funções de roteamento
├── main.py                      # Script de execução
├── requirements.txt             # Dependências
├── pyproject.toml              # Configuração do projeto
├── README.md                   # Documentação principal
├── ARQUITETURA.md              # Detalhes de arquitetura
├── DESENVOLVIMENTO.md          # Este arquivo
└── .gitignore                  # Exclusões do git
```

---

## Executando o Grafo

### Execução Básica

```bash
# Com pergunta padrão
python main.py

# Com pergunta customizada
python main.py "Qual é o total de vendas por produto?"

# Com múltiplas palavras (use aspas)
python main.py "Vender os clientes com maior volume no último trimestre"
```

### Execução Programática

```python
from src.graph import grafo_text_to_insight

# Estado inicial
estado = {
    "pergunta_usuario": "Sua pergunta aqui",
    "contexto_schema": "",
    "codigo_gerado": "",
    "saida_terminal": "",
    "feedback_critico": "",
    "status": "iniciado",
    "tentativas_loop": 0,
}

# Invocar grafo
resultado = grafo_text_to_insight.invoke(estado)

# Acessar resultados
print(f"Status: {resultado['status']}")
print(f"Código: {resultado['codigo_gerado']}")
print(f"Saída: {resultado['saida_terminal']}")
```

---

## Desenvolvendo Novos Nós

### Template para Novo Nó

```python
"""
Descrição do nó e sua responsabilidade.
"""

from ..state import EstadoTextToInsight


def nos_nodo_novo_componente(estado: EstadoTextToInsight) -> dict:
    """
    Breve descrição do nó.
    
    Detalhe o que faz, entradas e saídas.
    
    Args:
        estado (EstadoTextToInsight): Estado atual.
    
    Returns:
        dict: Atualizações do estado.
    """
    
    # Extrair informações do estado
    algum_valor = estado.get("alguma_chave", "default")
    
    print(f"[NOVO_COMPONENTE] Processando...")
    
    # Lógica aqui
    resultado = "algum_processamento"
    
    return {
        "chave_atualizada": resultado,
        "status": "novo_status",
    }
```

### Registrando Novo Nó

1. Criar arquivo em `src/nodes/novo_componente.py`
2. Adicionar import em `src/nodes/__init__.py`
3. Adicionar ao grafo em `src/graph.py`:

```python
construtor_grafo.add_node("novo_componente", nos_nodo_novo_componente)
```

---

## Desenvolvendo Novos Roteadores

### Template para Novo Roteador

```python
from typing import Literal
from ..state import EstadoTextToInsight


def roteador_novo(estado: EstadoTextToInsight) -> Literal["node1", "node2", "node3"]:
    """
    Decide para qual nó enviar o estado baseado em condições.
    
    Args:
        estado: Estado atual.
    
    Returns:
        Nome do nó de destino.
    """
    
    condicao = estado.get("alguma_chave", "")
    
    if condicao == "caso1":
        return "node1"
    elif condicao == "caso2":
        return "node2"
    else:
        return "node3"
```

### Registrando no Grafo

```python
construtor_grafo.add_conditional_edges(
    "node_origem",
    roteador_novo,
    {
        "node1": "node1",
        "node2": "node2",
        "node3": "node3",
    }
)
```

---

## Testes

### Criar Testes Simples

```python
# tests/test_planejador.py

from src.nodes import nos_nodo_planejador
from src.state import EstadoTextToInsight


def test_planejador_sem_schema():
    estado = {
        "pergunta_usuario": "Alguma pergunta",
        "contexto_schema": "",
        "codigo_gerado": "",
        "saida_terminal": "",
        "feedback_critico": "",
        "status": "iniciado",
        "tentativas_loop": 0,
    }
    
    resultado = nos_nodo_planejador(estado)
    
    assert resultado["status"] == "aguardando_schema"


def test_planejador_com_schema():
    estado = {
        "pergunta_usuario": "Alguma pergunta",
        "contexto_schema": "Some schema",
        "codigo_gerado": "",
        "saida_terminal": "",
        "feedback_critico": "",
        "status": "iniciado",
        "tentativas_loop": 0,
    }
    
    resultado = nos_nodo_planejador(estado)
    
    assert resultado["status"] == "pronto_codificacao"
```

### Executar Testes

```bash
# Com pytest
pytest tests/

# Com cobertura
pytest --cov=src tests/
```

---

## Debugging

### Print Debugging

Os nós já possuem prints para rastrear execução:

```bash
python main.py "Sua pergunta"
# Verá logs de cada nó
```

### Visualizar Grafo

```python
from src.graph import grafo_text_to_insight
from IPython.display import Image, display

# Gerar imagem do grafo
image = grafo_text_to_insight.get_graph(xray=True).draw_mermaid_png()
display(Image(image))
```

### Usar Debugger

```bash
# Com Python debugger
python -m pdb main.py "Sua pergunta"

# Com VS Code: Configure launch.json
```

---

## Linting e Formatação

### Formatar Código

```bash
# Com black
black src/ tests/ main.py

# Com isort (organizar imports)
isort src/ tests/ main.py
```

### Verificar Qualidade

```bash
# Com flake8
flake8 src/ tests/

# Com mypy (type checking)
mypy src/
```

---

## Variáveis de Ambiente

Criar arquivo `.env` (não committear):

```
OPENAI_API_KEY=sk-...
DATABASE_URL=postgresql://...
LOG_LEVEL=DEBUG
```

Usar em código:

```python
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
```

---

## Contribuindo

### Fluxo de Contribuição

1. **Fork/Branch**
   ```bash
   git checkout -b feature/minha-feature
   ```

2. **Desenvolver**
   - Adicionar código
   - Adicionar testes
   - Documentar mudanças

3. **Verificar Qualidade**
   ```bash
   black src/ && isort src/ && flake8 src/ && mypy src/
   pytest tests/
   ```

4. **Commit e Push**
   ```bash
   git add .
   git commit -m "feat: descrição da mudança"
   git push origin feature/minha-feature
   ```

5. **Pull Request**
   - Descrever mudanças
   - Referenciar issues

---

## Roadmap de Desenvolvimento

### Fase 1: Esqueleto ✅
- [x] Criar estrutura de arquivos
- [x] Definir TypedDict EstadoTextToInsight
- [x] Implementar nós mockados
- [x] Implementar roteadores
- [x] Compilar grafo

### Fase 2: Integração Real (Próximo)
- [ ] Integrar com LLM real (OpenAI/Anthropic)
- [ ] Conectar banco de dados real
- [ ] Implementar sandbox com Docker
- [ ] Adicionar observabilidade

### Fase 3: Otimização
- [ ] Cache de schemas
- [ ] Paralelização de nós
- [ ] Métricas avançadas
- [ ] Retry policies

### Fase 4: Produção
- [ ] Deploy em produção
- [ ] Monitoramento 24/7
- [ ] Feedback loop com usuários

---

## Troubleshooting

### Erro: `ModuleNotFoundError: No module named 'langgraph'`

```bash
pip install langgraph langchain langchain-core
```

### Erro: `ImportError: cannot import name 'StateGraph'`

Verificar versão do langgraph:

```bash
pip install --upgrade langgraph
```

### Grafo não processa corretamente

1. Verificar estado inicial
2. Adicionar print debugging em nós
3. Validar roteadores com print
4. Revisar lógica em estado

### Performance baixa

- Cachear schemas
- Limitar tentativas
- Usar async onde possível
- Paralelizar nós independentes

---

## Recursos Úteis

- **LangGraph Docs**: https://langchain-ai.github.io/langgraph/
- **LangChain Docs**: https://docs.langchain.com
- **TypedDict**: https://docs.python.org/3/library/typing.html#typing.TypedDict
- **SST Examples**: https://github.com/langchain-ai/langgraph/tree/main/examples

---

## Contato e Suporte

Para dúvidas ou sugestões:
- Abrir issue no GitHub
- Discussões na comunidade
- Email: seu.email@exemplo.com

---

**Happy Coding! 🚀**
