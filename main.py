#!/usr/bin/env python3
"""
Script principal para demonstração do grafo Text-to-Insight.
"""

import sys
import os
from dotenv import load_dotenv
from langgraph.graph import StateGraph
from src.graph import Graph

load_dotenv()

def executar_consulta(grafo: StateGraph, pergunta: str) -> dict:
    """Executa uma consulta através do grafo Text-to-Insight."""
    estado_inicial = {
        "pergunta_usuario": pergunta,
        "contexto_schema": "",
        "sql_gerada": "",
        "saida_terminal": "",
        "feedback_critico": "",
        "erro_execucao": "",
        "status": "iniciado",
        "tentativas_loop": 0,
        "db_path": "data/olist_relational.db",
    }

    print("=" * 70)
    print("INICIANDO TEXT-TO-INSIGHT")
    print("=" * 70)
    print(f"\nPergunta: {pergunta}\n")
    print("=" * 70)

    resultado_final = grafo.invoke(estado_inicial)
    return resultado_final


def exibir_resultado(resultado: dict) -> None:
    """Exibe o resultado final da execução de forma formatada."""
    print("\n" + "=" * 70)
    print("EXECUCAO CONCLUIDA")
    print("=" * 70)

    print(f"\nStatus Final: {resultado.get('status', 'desconhecido').upper()}")
    print(f"Total de Tentativas: {resultado.get('tentativas_loop', 0)}")

    print("\n" + "-" * 70)
    print("SQL GERADA:")
    print("-" * 70)
    sql = resultado.get("sql_gerada", "").strip()
    print(sql if sql else "[Nenhuma SQL gerada]")

    print("\n" + "-" * 70)
    print("SAIDA DA EXECUCAO:")
    print("-" * 70)
    saida = resultado.get("saida_terminal", "").strip()
    print(saida if saida else "[Nenhuma saida]")

    print("\n" + "-" * 70)
    print("RESULTADO (preview):")
    print("-" * 70)
    preview = resultado.get("linhas_resultado_preview", [])
    total = resultado.get("total_linhas_resultado", 0)
    if preview:
        for row in preview[:10]:
            print(row)
        if total > 10:
            print(f"... ({total - 10} linhas omitidas)")
    else:
        print("[Nenhum resultado]")

    print("\n" + "-" * 70)
    print("FEEDBACK DO CRITICO:")
    print("-" * 70)
    feedback = resultado.get("feedback_critico", "").strip()
    print(feedback if feedback else "[Nenhum feedback]")

    # Se o nó de resposta final gerou uma resposta em linguagem natural, exibi-la
    resposta_natural = resultado.get("resposta_natural", "").strip()
    print("\n" + "-" * 70)
    print("RESPOSTA NATURAL AO USUARIO:")
    print("-" * 70)
    print(resposta_natural)

    print("\n" + "=" * 70 + "\n")


def main():
    if len(sys.argv) > 1:
        pergunta = " ".join(sys.argv[1:])
    else:
        pergunta = "Quantos pedidos existem no banco?"
        print(f"Nenhuma pergunta fornecida. Usando exemplo: '{pergunta}'\n")

    api_key = os.getenv("GOOGLE_API_KEY")

    grafo = Graph(api_key)

    resultado = executar_consulta(grafo, pergunta)
    exibir_resultado(resultado)


if __name__ == "__main__":
    main()
