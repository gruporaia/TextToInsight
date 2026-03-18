#!/usr/bin/env python3
"""
Script principal para demonstração do grafo Text-to-Insight.

Este script exemplifica como usar o grafo compilado para processar
uma pergunta do usuário através do fluxo de agentes.
"""

import sys
from src.graph import grafo_text_to_insight


def executar_consulta(pergunta: str) -> dict:
    """
    Executa uma consulta através do grafo Text-to-Insight.
    
    Args:
        pergunta (str): Pergunta ou solicitação do usuário.
    
    Returns:
        dict: Estado final após execução completa do grafo.
    """
    
    # Estado inicial
    estado_inicial = {
        "pergunta_usuario": pergunta,
        "contexto_schema": "",
        "codigo_gerado": "",
        "saida_terminal": "",
        "feedback_critico": "",
        "status": "iniciado",
        "tentativas_loop": 0,
    }
    
    print("=" * 70)
    print("🚀 INICIANDO TEXT-TO-INSIGHT")
    print("=" * 70)
    print(f"\n📝 Pergunta do usuário:\n{pergunta}\n")
    print("=" * 70)
    print("Processando grafo...\n")
    
    # Invocar o grafo
    resultado_final = grafo_text_to_insight.invoke(estado_inicial)
    
    return resultado_final


def exibir_resultado(resultado: dict) -> None:
    """
    Exibe o resultado final da execução de forma formatada.
    
    Args:
        resultado (dict): Estado final do grafo.
    """
    
    print("\n" + "=" * 70)
    print("✅ EXECUÇÃO CONCLUÍDA")
    print("=" * 70)
    
    print(f"\n📊 Status Final: {resultado.get('status', 'desconhecido').upper()}")
    print(f"🔄 Total de Tentativas: {resultado.get('tentativas_loop', 0)}")
    
    print("\n" + "-" * 70)
    print("📋 CÓDIGO GERADO:")
    print("-" * 70)
    codigo = resultado.get("codigo_gerado", "").strip()
    if codigo:
        # Exibir apenas as primeiras linhas do código
        linhas = codigo.split("\n")[:20]
        print("\n".join(linhas))
        if len(codigo.split("\n")) > 20:
            print(f"\n... ({len(codigo.split('\n')) - 20} linhas omitidas)")
    else:
        print("[Nenhum código gerado]")
    
    print("\n" + "-" * 70)
    print("🖥️  SAÍDA DA EXECUÇÃO:")
    print("-" * 70)
    saida = resultado.get("saida_terminal", "").strip()
    if saida:
        print(saida)
    else:
        print("[Nenhuma saída]")
    
    print("\n" + "-" * 70)
    print("💭 FEEDBACK DO CRÍTICO:")
    print("-" * 70)
    feedback = resultado.get("feedback_critico", "").strip()
    if feedback:
        # Exibir apenas as primeiras linhas do feedback
        linhas = feedback.split("\n")[:15]
        print("\n".join(linhas))
        if len(feedback.split("\n")) > 15:
            print(f"\n... ({len(feedback.split('\n')) - 15} linhas omitidas)")
    else:
        print("[Nenhum feedback]")
    
    print("\n" + "-" * 70)
    print("📌 CONTEXTO DO SCHEMA:")
    print("-" * 70)
    schema = resultado.get("contexto_schema", "").strip()
    if schema:
        # Exibir apenas as primeiras linhas do schema
        linhas = schema.split("\n")[:10]
        print("\n".join(linhas))
        if len(schema.split("\n")) > 10:
            print(f"\n... ({len(schema.split('\n')) - 10} linhas omitidas)")
    else:
        print("[Nenhum schema]")
    
    print("\n" + "=" * 70 + "\n")


def main():
    """Função principal do script de demonstração."""
    
    # Exemplo de pergunta
    perguntas_exemplo = [
        "Qual é o total de vendas por produto no último mês?",
        "Liste os 10 clientes com maior volume de compras",
        "Quantas vendas foram realizadas por dia da semana?",
    ]
    
    if len(sys.argv) > 1:
        # Usar pergunta do argumento de linha de comando
        pergunta = " ".join(sys.argv[1:])
    else:
        # Usar primeira pergunta de exemplo
        pergunta = perguntas_exemplo[0]
        print(f"💡 Nenhuma pergunta fornecida. Usando exemplo:")
        print(f"   Dica: python main.py '<sua_pergunta>'\n")
    
    # Executar consulta
    resultado = executar_consulta(pergunta)
    
    # Exibir resultado
    exibir_resultado(resultado)


if __name__ == "__main__":
    main()
