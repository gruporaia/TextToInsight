"""
Nó Gerador de Gráficos do grafo de agentes Text-to-Insight.

Responsabilidade: usar o LLM para gerar código Python (matplotlib) que
visualize os dados do CSV de resultado da query, executar o código gerado,
e armazenar o caminho da imagem resultante no estado.
"""

import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from langchain_google_genai import ChatGoogleGenerativeAI

from ..state import EstadoTextToInsight
from ..utils import extrair_tokens

# Diretório padrão para salvar os gráficos gerados
GRAPHS_DIR = Path(__file__).parent.parent.parent / "graphs"

PROMPT_GRAPH_GENERATOR = """Você é um especialista em visualização de dados com Python e matplotlib.

Sua tarefa: gerar APENAS o bloco de código Python que cria um gráfico matplotlib
para visualizar os dados descritos abaixo.

=== PERGUNTA DO USUÁRIO ===
{pergunta}

=== SQL EXECUTADA ===
{sql}

=== COLUNAS DO RESULTADO ===
{colunas}

=== AMOSTRA DOS DADOS (primeiras linhas do CSV) ===
{amostra}

=== TOTAL DE LINHAS ===
{total_linhas}

Regras:
- O código será inserido dentro de um script que já importou pandas, matplotlib e já
  carregou o DataFrame com `df = pd.read_csv(...)`.
- Você NÃO deve importar nada nem carregar dados. Apenas use a variável `df`.
- Use `plt` (já importado como `import matplotlib.pyplot as plt`).
- Escolha o tipo de gráfico mais adequado à pergunta e aos dados (barras, linhas, pizza, dispersão, etc.).
- Adicione título, labels nos eixos, e legenda quando relevante.
- Use cores visualmente agradáveis.
- Se necessário, rotacione labels do eixo X para legibilidade.
- O gráfico será salvo automaticamente, você NÃO deve chamar plt.savefig() nem plt.show().
- Responda APENAS com o código Python puro, sem markdown, sem explicações.
- Se os dados tiverem muitas categorias (>15), mostre apenas o top 10-15 mais relevantes.

Não faça um gráfico basico visualmente, faça ele bonito, use cores agradaveis e que ajude o usuario a entender os dados. Tenta fazer algo com cara profissional! Feito por um analista apresentando para um grande cliente que julga o livro pela capa.
"""


def _extrair_codigo_python(texto: str) -> str:
    """Extrai código Python puro da resposta do LLM, removendo markdown."""
    match = re.search(r"```(?:python)?\s*\n?(.*?)```", texto, re.DOTALL)
    if match:
        return match.group(1).strip()
    return texto.strip()


def _construir_script(csv_path: str, output_path: str, codigo_visualizacao: str) -> str:
    """Monta o script Python completo que será executado."""
    return f'''import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

df = pd.read_csv("{csv_path}")

{codigo_visualizacao}

plt.tight_layout()
plt.savefig("{output_path}", dpi=150, bbox_inches='tight')
plt.close()
print("GRAPH_OK")
'''


def _executar_script(script: str) -> tuple[bool, str]:
    """Executa o script Python em um subprocesso e retorna (sucesso, saída/erro)."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(script)
        tmp_path = tmp.name

    try:
        resultado = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        stdout = resultado.stdout.strip()
        stderr = resultado.stderr.strip()

        if resultado.returncode == 0 and "GRAPH_OK" in stdout:
            return True, stdout
        else:
            erro = stderr if stderr else stdout
            return False, erro
    except subprocess.TimeoutExpired:
        return False, "Timeout: o script de geração do gráfico excedeu 30 segundos."
    except Exception as e:
        return False, f"Erro ao executar script: {e}"
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def nos_nodo_gerador_grafico(estado: EstadoTextToInsight, llm: ChatGoogleGenerativeAI) -> dict:
    """
    Nó Gerador de Gráficos: usa o LLM para gerar código matplotlib e o executa.
    """
    csv_path = estado.get("caminho_csv_resultado", "")
    pergunta = estado.get("pergunta_usuario", "")
    sql = estado.get("sql_gerada", "")
    preview = estado.get("linhas_resultado_preview", [])
    total = estado.get("total_linhas_resultado", 0)

    print("[GERADOR_GRAFICO] Iniciando geração de gráfico...")

    if not csv_path or not Path(csv_path).exists():
        print("[GERADOR_GRAFICO] CSV não encontrado — pulando geração.")
        return {"grafico_gerado": False, "caminho_grafico": ""}

    # Extrair colunas e amostra para o prompt
    colunas = list(preview[0].keys()) if preview and isinstance(preview[0], dict) else []
    amostra_str = str(preview[:5]) if preview else "(vazio)"

    prompt = PROMPT_GRAPH_GENERATOR.format(
        pergunta=pergunta,
        sql=sql,
        colunas=", ".join(colunas) if colunas else "(desconhecidas)",
        amostra=amostra_str,
        total_linhas=total,
    )

    # Chamada ao LLM para gerar o código de visualização
    resposta = llm.invoke(prompt)
    codigo_bruto = resposta.content.strip()
    codigo_visualizacao = _extrair_codigo_python(codigo_bruto)

    print(f"[GERADOR_GRAFICO] Código gerado ({len(codigo_visualizacao)} bytes)")

    # Montar caminho de saída do gráfico
    GRAPHS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_path = str((GRAPHS_DIR / f"grafico_{timestamp}.png").resolve())

    # Montar e executar o script completo
    script = _construir_script(csv_path, output_path, codigo_visualizacao)
    print(script)
    print(f"[GERADOR_GRAFICO] Executando script...")

    sucesso, saida = _executar_script(script)

    in_tokens, out_tokens, total_tokens = extrair_tokens(resposta)

    if sucesso and Path(output_path).exists():
        print(f"[GERADOR_GRAFICO] ✓ Gráfico salvo em: {output_path}")
        return {
            "grafico_gerado": True,
            "caminho_grafico": output_path,
            "tokens_input": in_tokens,
            "tokens_output": out_tokens,
            "tokens_total": total_tokens,
        }
    else:
        print(f"[GERADOR_GRAFICO] ✗ Falha na geração do gráfico: {saida[:200]}")
        return {
            "grafico_gerado": False,
            "caminho_grafico": "",
            "tokens_input": in_tokens,
            "tokens_output": out_tokens,
            "tokens_total": total_tokens,
        }
