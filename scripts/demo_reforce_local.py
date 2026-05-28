#!/usr/bin/env python3
"""
Demo Simplificado: Testar ReFoRCE com Dados Locais

Em vez de depender do Spider2-Lite (que requer conversão JSON→SQLite),
este script testa a arquitetura ReFoRCE usando o banco olist_relational.db
que já está disponível localmente.

Objetivo: Validar que o grafo ReFoRCE funciona end-to-end com:
- 5 candidatos gerados em paralelo
- Votação por consenso
- Exploração de divergências
- Coleta de artefatos

Uso:
    python scripts/demo_reforce_local.py
    python scripts/demo_reforce_local.py --queries 3
"""

import sys
import os
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from text_to_insight import InsightEngine

load_dotenv()


# Queries de teste para demonstrar ReFoRCE
TEST_QUERIES = [
    "Quais são os 5 principais produtos por número de vendas?",
    "Qual é o valor total de vendas por categoria de produto?",
    "Quantos clientes fizeram apenas uma compra?",
    "Qual é o ticket médio de vendas por mês?",
    "Quais são os estados com maior número de pedidos?",
]


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Demo ReFoRCE com dados locais")
    parser.add_argument("--queries", type=int, default=3, help="Número de queries a testar")
    parser.add_argument("--model", type=str, default="gpt-4o-mini", help="Modelo LLM")
    args = parser.parse_args()

    # Validar API key
    model = args.model
    api_key = os.getenv("OPENAI_API_KEY") if "gpt" in model.lower() else os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("❌ Erro: Chave API não encontrada em .env")
        sys.exit(1)

    # Banco de dados local
    db_path = "data/olist_relational.db"
    if not Path(db_path).exists():
        print(f"❌ Banco não encontrado: {db_path}")
        sys.exit(1)

    print("\n" + "=" * 80)
    print("🚀 DEMO: ReFoRCE com Dados Locais")
    print("=" * 80)
    print(f"\n📊 Banco de dados: {db_path}")
    print(f"🤖 Modelo: {model}")
    print(f"📝 Queries a testar: {args.queries}")

    # Inicializar engine
    print("\n🔧 Inicializando InsightEngine com ReFoRCE...")
    engine = InsightEngine(
        api_key=api_key,
        model=model,
        db_path=db_path,
        hitl=False,
        show_output=False,
        enable_graphs=False,  # Desabilitar geração de gráficos para simplificar
    )
    print("✓ Engine inicializado")

    # Executar queries de teste
    results = []
    queries = TEST_QUERIES[:args.queries]

    for idx, query in enumerate(queries, 1):
        print(f"\n[{idx}/{len(queries)}] Executando query: {query[:60]}...")
        print("-" * 80)

        try:
            resultado = engine.run(thread_id=f"demo_reforce_{idx}", query=query)

            # Extrair dados essenciais
            sql_gerada = resultado.get("sql_gerada", "")
            status = resultado.get("status", "")
            veredito = resultado.get("status", "")
            
            # ✅ ReFoRCE artifacts
            candidatos = resultado.get("candidatos", [])
            status_consenso = resultado.get("status_consenso", "nao_votado")
            rodadas_exploracao = resultado.get("rodadas_exploracao", 0)
            sql_vencedora = resultado.get("sql_vencedora", "")
            
            num_candidatos = len(candidatos)
            num_validos = sum(1 for c in candidatos if c.get("valido", False))
            
            # Log detalhado
            print(f"\n✅ Status Final: {veredito}")
            print(f"📊 SQL Gerada: {sql_gerada[:80]}...")
            print(f"\n🤖 ReFoRCE Results:")
            print(f"   • Candidatos gerados: {num_candidatos}")
            print(f"   • Candidatos válidos: {num_validos}")
            print(f"   • Status consenso: {status_consenso}")
            print(f"   • Rodadas exploração: {rodadas_exploracao}")
            print(f"   • SQL vencedora: {sql_vencedora[:60]}..." if sql_vencedora else "   • SQL vencedora: (nenhuma)")
            
            # Resumo de cada candidato
            print(f"\n   Detalhes dos candidatos:")
            for i, c in enumerate(candidatos):
                valido_icon = "✅" if c.get("valido") else "❌"
                erro = c.get("erro", "")[:40] if c.get("erro") else ""
                print(f"     [{i}] {valido_icon} | Assinatura: {c.get('assinatura_resultado', '')[:16]}... | {erro}")
            
            # Tokens
            tokens_total = resultado.get("tokens_total", 0) or 0
            print(f"\n💰 Tokens consumidos: {tokens_total}")
            
            results.append({
                "query": query,
                "sql": sql_gerada,
                "status": veredito,
                "reforce_candidatos": num_candidatos,
                "reforce_validos": num_validos,
                "reforce_consenso": status_consenso,
                "reforce_rodadas": rodadas_exploracao,
                "tokens": tokens_total,
            })

        except Exception as e:
            print(f"❌ Erro ao processar: {str(e)[:100]}")
            continue

    # Resumo final
    if results:
        print("\n" + "=" * 80)
        print("📈 RESUMO FINAL")
        print("=" * 80)
        print(f"\nQueries processadas: {len(results)}")
        
        consenso_count = sum(1 for r in results if r["reforce_consenso"] == "consenso_encontrado")
        print(f"Consenso encontrado: {consenso_count}/{len(results)}")
        
        avg_candidatos = sum(r["reforce_candidatos"] for r in results) / len(results)
        avg_validos = sum(r["reforce_validos"] for r in results) / len(results)
        print(f"Média de candidatos: {avg_candidatos:.1f}")
        print(f"Média de candidatos válidos: {avg_validos:.1f}")
        
        total_tokens = sum(r["tokens"] for r in results)
        print(f"Total de tokens: {total_tokens}")
        
        print("\n✅ Demo concluído com sucesso!")
    else:
        print("\n❌ Nenhuma query foi processada com sucesso.")
        sys.exit(1)


if __name__ == "__main__":
    main()
