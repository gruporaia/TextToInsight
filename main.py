#!/usr/bin/env python3
"""
Script principal para demonstração do grafo Text-to-Insight.
"""

import csv
from datetime import datetime
import time
import sys
import os
from dotenv import load_dotenv
from langgraph.graph import StateGraph
from src.graph import Graph


def salvar_metricas_csv(resultado: dict, latencia: float, arquivo_csv="data/metricas_execucao.csv"):
    """Salva as métricas de uma execução em um arquivo CSV."""
    arquivo_existe = os.path.isfile(arquivo_csv)
    
    # Colunas 
    dados = {
        "data_hora": datetime.now(),
        "pergunta": resultado.get("pergunta_usuario", ""),
        "status_final": resultado.get("status", ""),
        "tentativas": resultado.get("tentativas_loop", 0),
        "tokens_input": resultado.get("tokens_input", 0),
        "tokens_output": resultado.get("tokens_output", 0),
        "tokens_total": resultado.get("tokens_total", 0),
        "latencia_segundos": round(latencia,2),
        "erro": resultado.get("erro_execucao", "")
    }

    # Garante que o diretório existe
    os.makedirs(os.path.dirname(arquivo_csv), exist_ok=True)

    with open(arquivo_csv, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=dados.keys())
        
        if not arquivo_existe:
            writer.writeheader() # Escreve o cabeçalho na primeira vez
            
        writer.writerow(dados)
    print(f"[MÉTRICAS] Salvas com sucesso em {arquivo_csv}")

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

    # Intervalo de cálculo da latência da consulta no grafo. Optei por considerar somente
    # o tempo em que o grafo de fato está rodando, então não incluo o tempo que leva as linhas
    # anteriores na main ou antes desse trecho.
    lat_inicio = time.perf_counter()

    resultado_final = grafo.invoke(estado_inicial)

    lat_fim = time.perf_counter()
    latencia_consulta = lat_fim - lat_inicio

    salvar_metricas_csv(resultado_final, latencia_consulta)

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
