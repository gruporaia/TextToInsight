"""
Nó Agente de Código do grafo de agentes Text-to-Insight.

O agente de código é responsável por:
- Processar a pergunta do usuário
- Usar o contexto do schema
- Gerar código Python funcional
"""

from ..state import EstadoTextToInsight


def nos_nodo_agente_codigo(estado: EstadoTextToInsight) -> dict:
    """
    Nó Agente de Código: Escreve código Python baseado no plano do planejador.
    
    Utiliza:
    - A pergunta do usuário
    - O contexto do schema
    - A história de tentativas anteriores
    
    Para gerar código Python que:
    - Consulte o banco de dados conforme necessário
    - Processe os dados
    - Retorne uma resposta à pergunta
    
    Args:
        estado (EstadoTextToInsight): Estado atual do grafo.
    
    Returns:
        dict: Dicionário com atualizações do estado.
              - codigo_gerado: String com código Python gerado
              - status: 'codigo_gerado'
    """
    
    pergunta = estado.get("pergunta_usuario", "")
    contexto = estado.get("contexto_schema", "")
    tentativas = estado.get("tentativas_loop", 0)
    
    print(f"[AGENTE_CODIGO] Gerando código (tentativa {tentativas + 1})...")
    print(f"[AGENTE_CODIGO] Pergunta: {pergunta[:50]}...")
    
    # Código simulado em Python
    codigo_simulado = f'''
import sqlite3

def executar_consulta():
    """
    Executa a consulta para responder à pergunta do usuário.
    
    Pergunta original: {pergunta}
    
    Contexto utilizado:
    - Schema de banco de dados
    - Relacionamentos entre tabelas
    """
    
    # Simular conexão ao banco
    # conn = sqlite3.connect("banco_dados.db")
    # cursor = conn.cursor()
    
    # Simular execução de query
    # cursor.execute("""
    #     SELECT * FROM USUARIOS
    #     WHERE data_criacao > '2024-01-01'
    # """)
    
    # Simular resultado
    resultado = {{
        "total_registros": 42,
        "processado": True,
        "timestamp": "2026-03-18T10:30:00Z"
    }}
    
    return resultado

if __name__ == "__main__":
    resultado = executar_consulta()
    print(f"Resultado: {{resultado}}")
'''
    
    print("[AGENTE_CODIGO] Código gerado com sucesso!")
    
    return {
        "codigo_gerado": codigo_simulado,
        "status": "codigo_gerado",
        "tentativas_loop": tentativas + 1,
    }
