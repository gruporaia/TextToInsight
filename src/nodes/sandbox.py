"""
Nó Sandbox do grafo de agentes Text-to-Insight.

O nó sandbox é responsável por:
- Executar código de forma segura e isolada
- Capturar saída e erros
- Registrar status de execução
- Simular um ambiente de execução controlado
"""

import random
from ..state import EstadoTextToInsight


def nos_nodo_sandbox(estado: EstadoTextToInsight) -> dict:
    """
    Nó Sandbox: Executa código de forma segura.
    
    Simula a execução de código Python em um ambiente isolado:
    - Captura stdout/stderr
    - Trata exceções
    - Registra status (sucesso ou erro)
    - Simula aleatoriedade para fins de teste
    
    Args:
        estado (EstadoTextToInsight): Estado atual do grafo.
    
    Returns:
        dict: Dicionário com atualizações do estado.
              - saida_terminal: String com saída simulada da execução
              - status: 'codigo_ok' ou 'erro_codigo'
    """
    
    codigo = estado.get("codigo_gerado", "")
    tentativas = estado.get("tentativas_loop", 0)
    
    print(f"[SANDBOX] Executando código (tentativa {tentativas})...")
    
    # Simular execução com chance variável de erro baseada no número de tentativas
    # Cada tentativa tem melhor chance de sucesso (progressivamente)
    taxa_sucesso = 0.5 + (tentativas * 0.25)  # 50%, 75%, 100%
    execucao_sucesso = random.random() < taxa_sucesso
    
    if execucao_sucesso:
        print("[SANDBOX] Código executado com sucesso!")
        saida_simulada = """
        Resultado da execução:
        - Total de registros: 42
        - Status: OK
        - Tempo de execução: 0.234s
        - Memória utilizada: 45.3 MB
        """
        status_sandbox = "codigo_ok"
    else:
        print(f"[SANDBOX] Erro durante execução (tentativa {tentativas})!")
        saida_simulada = """
        Erro:
        NameError: name 'conexao' is not defined
        at line 23 in executar_consulta()
        """
        status_sandbox = "erro_codigo"
    
    return {
        "saida_terminal": saida_simulada,
        "status": status_sandbox,
    }
