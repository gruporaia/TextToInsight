#!/usr/bin/env python3
"""
Script de Avaliação do Agente contra Spider Dataset.

Testa o agente Text-to-Insight contra perguntas reais do Spider dataset,
rastreando cada tentativa (quando o crítico reprova e volta ao planejador).

Uso:
    python scripts/test_spider_eval.py --sample-size 10 --seed 42
    python scripts/test_spider_eval.py --db-filter concert_singer --output reports/eval.csv
"""

import argparse
import os
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

# Importar módulos do projeto
from src.graph import Graph
from src.spider.csv_reporter import CSVReporter
from src.spider.data_loader import (
    filter_by_db_id,
    get_unique_db_ids,
    load_spider_dev_examples,
    sample_examples,
)
from src.spider.metrics import (
    build_comparison_row,
    results_exact_match,
    sql_similarity_score,
)
from src.spider.query_executor import SpiderQueryExecutor

load_dotenv()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Avaliar agente Text-to-Insight contra Spider dataset"
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=10,
        help="Quantas perguntas testar (default: 10)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Seed para reproducibilidade (default: 42)",
    )
    parser.add_argument(
        "--db-filter",
        type=str,
        help="Filtrar por banco específico (ex: concert_singer)",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Caminho para salvar CSV (default: reports/spider_eval_TIMESTAMP.csv)",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Máximo de tentativas por pergunta (default: 3)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data/spider_data/spider_data",
        help="Diretório com dados do Spider",
    )

    args = parser.parse_args()

    # Validar API key
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("❌ Erro: GOOGLE_API_KEY não encontrada em .env")
        sys.exit(1)

    # 1. Carregar dados
    print(f"\n📂 Carregando exemplos do Spider de {args.data_dir}...")
    try:
        ejemplos = load_spider_dev_examples(args.data_dir)
        print(f"✓ Carregados {len(ejemplos)} exemplos")
    except FileNotFoundError as e:
        print(f"❌ {e}")
        sys.exit(1)

    # 2. Aplicar filtros
    if args.db_filter:
        ejemplos = filter_by_db_id(ejemplos, args.db_filter)
        print(f"✓ Filtrados por db_id={args.db_filter}: {len(ejemplos)} exemplos")

    # 3. Fazer sampling
    ejemplos = sample_examples(ejemplos, sample_size=args.sample_size, seed=args.seed)
    print(
        f"✓ Selecionados {len(ejemplos)} exemplos (seed={args.seed}, "
        f"bancos únicos: {len(get_unique_db_ids(ejemplos))})"
    )

    # 4. Inicializar componentes
    print("\n🔧 Inicializando componentes...")
    try:
        grafo = Graph(api_key)
        print("✓ Grafo LangGraph inicializado")
    except Exception as e:
        print(f"❌ Erro ao inicializar grafo: {e}")
        sys.exit(1)

    executor = SpiderQueryExecutor()
    print("✓ Query executor inicializado")

    # 5. Preparar CSV
    if args.output:
        csv_path = args.output
    else:
        csv_path = f"reports/{CSVReporter.generate_timestamped_filename('spider_eval')}"

    reporter = CSVReporter(csv_path)
    print(f"✓ CSV reporter inicializado: {csv_path}")

    # 6. Loop de testes
    print(f"\n🚀 Iniciando avaliação com {len(ejemplos)} perguntas...\n")
    print("=" * 100)

    all_rows = []
    ex_id = 1

    for idx, ex in enumerate(ejemplos, 1):
        pergunta = ex.get("question", "")
        query_ouro = ex.get("query", "")
        db_id = ex.get("db_id", "")

        print(f"\n[{idx}/{len(ejemplos)}] Pergunta: {pergunta[:60]}...")
        print(f"     DB: {db_id} | Query Ouro: {query_ouro[:50]}...")

        # Executar query ouro para obter resultado esperado
        print(f"     → Executando query ouro...")
        resultado_ouro = executor.execute_query(db_id, query_ouro)

        if not resultado_ouro["success"]:
            print(f"     ⚠️  Erro na query ouro: {resultado_ouro['error']}")
            continue  # Pular este exemplo

        print(f"     ✓ Query ouro retornou {resultado_ouro['row_count']} linhas")

        # Invocar grafo com stream() para rastrear tentativas
        tentativa_numero = 1
        estado_inicial = {
            "pergunta_usuario": pergunta,
            "db_path": str(executor.get_db_path(db_id)),
            "contexto_schema": "",
            "sql_gerada": "",
            "linhas_resultado_preview": [],
            "total_linhas_resultado": 0,
            "erro_execucao": "",
            "saida_terminal": "",
            "feedback_critico": "",
            "status": "iniciado",
            "tentativas_loop": 0,
        }

        print(f"     → Invocando agente (max tentativas: {args.max_attempts})...")
        inicio_agente = time.time()
        full_estado = estado_inicial.copy()  # Manter estado acumulado

        try:
            for output in grafo.stream(
                estado_inicial, config={"recursion_limit": 30}
            ):
                # stream() retorna dict: {'nó_name': {mudanças_do_nó}}
                # Acumular mudanças no estado completo
                for node_name, mudancas in output.items():
                    full_estado.update(mudancas)
                    
                    # Após crítico retornar, coletar métricas e salvar
                    if "critic" in node_name.lower():
                        tempo_tentativa = (time.time() - inicio_agente) * 1000

                        query_agente = full_estado.get("sql_gerada", "")
                        veredito = full_estado.get("status", "")
                        feedback_estado = full_estado.get("feedback_critico", "")
                        erro_exec = full_estado.get("erro_execucao", "")
                        tentativas = full_estado.get("tentativas_loop", 1)

                        # Mapear status para veredito e definir feedback
                        if veredito == "aprovado":
                            veredito_critico = "aprovado"
                            # Se aprovado, feedback é confirmação
                            feedback_critico = feedback_estado if feedback_estado else "Aprovado"
                        elif veredito == "reprovado":
                            veredito_critico = "reprovado"
                            # Se reprovado, usar feedback do crítico
                            feedback_critico = feedback_estado if feedback_estado else "Reprovado pelo crítico"
                        else:
                            veredito_critico = "erro"
                            feedback_critico = feedback_estado if feedback_estado else "Erro na avaliação"

                        # Comparar resultados se query agente foi gerada
                        resultado_exato_match = None
                        similarity_score = 0.0

                        if query_agente and not erro_exec:
                            resultado_agente = executor.execute_query(db_id, query_agente)
                            if resultado_agente["success"]:
                                resultado_exato_match = results_exact_match(
                                    resultado_ouro["results"],
                                    resultado_agente["results"],
                                )
                                similarity_score = sql_similarity_score(query_ouro, query_agente)
                                print(
                                    f"       Tentativa {tentativas}: "
                                    f"similarity={similarity_score:.2f}, "
                                    f"match={resultado_exato_match}, "
                                    f"veredito={veredito_critico}"
                                )
                            else:
                                erro_exec = resultado_agente["error"]
                        else:
                            print(
                                f"       Tentativa {tentativas}: "
                                f"sem query gerada ou com erro de execução"
                            )

                        # Construir linha para CSV
                        row = build_comparison_row(
                            id_exemplo=ex_id,
                            tentativa_numero=tentativas,
                            db_id=db_id,
                            pergunta=pergunta,
                            query_ouro=query_ouro,
                            query_agente=query_agente,
                            tempo_agente_ms=tempo_tentativa,
                            veredito_critico=veredito_critico,
                            feedback_critico=feedback_critico,
                            erro_execucao=erro_exec,
                            resultado_exato_match=resultado_exato_match,
                            similarity_score=similarity_score,
                        )

                        reporter.append_row(row)
                        all_rows.append(row)

                        # Se aprovado, terminar loop
                        if veredito_critico == "aprovado":
                            print(f"     ✅ APROVADO na tentativa {tentativas}")
                            break
                        elif tentativas >= args.max_attempts:
                            print(f"     ❌ MÁXIMO DE TENTATIVAS ({args.max_attempts}) ATINGIDO")
                            break

        except Exception as e:
            print(f"     ⚠️  Erro ao processar pergunta: {str(e)}")
            continue

        ex_id += 1
        time.sleep(1)  # Delay entre perguntas

    # 7. Gerar resumo
    print("\n" + "=" * 100)
    print("📊 RESUMO FINAL")
    print("=" * 100)

    if all_rows:
        summary = reporter.generate_summary(all_rows)
        print(f"Total de perguntas: {summary['total_perguntas']}")
        print(f"Total de tentativas: {summary['total_tentativas']}")
        print(f"Perguntas aprovadas: {summary['perguntas_aprovadas']}")
        print(f"Taxa de aprovação: {summary['taxa_aprovacao']:.1%}")
        print(f"Taxa de sucesso na 1ª tentativa: {summary['taxa_1a_tentativa']:.1%}")
        print(f"Tentativas médias por pergunta: {summary['tentativas_media']:.2f}")
        print(f"Similarity score médio: {summary['similarity_media']:.4f}")
        print(f"Tempo médio por tentativa: {summary['tempo_medio_ms']:.2f} ms")
        print(f"\n✅ CSV salvo em: {csv_path}")
    else:
        print("❌ Nenhum resultado para salvar")


if __name__ == "__main__":
    main()
