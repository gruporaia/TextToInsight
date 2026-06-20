#!/usr/bin/env python3
"""
Script de Avaliação do Agente contra Spider Dataset.

Testa o agente Text-to-Insight contra perguntas reais do Spider dataset,
usando a classe InsightEngine do pacote text_to_insight.

Uso:
    python scripts/test_spider_eval.py --sample-size 10 --seed 42
    python scripts/test_spider_eval.py --db-filter concert_singer --output reports/eval.csv
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

# Importar InsightEngine do pacote text_to_insight
from text_to_insight import InsightEngine

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
    results_f1_score,
    sql_similarity_score,
)
from src.spider.query_executor import SpiderQueryExecutor
from src.spider.analise_empirica import gerar_relatorio_empirico_completo

load_dotenv()


def _calcular_estatisticas(valores: list[float]) -> dict[str, float]:
    if not valores:
        return {"mean": 0.0, "max": 0.0, "min": 0.0, "median": 0.0, "p75": 0.0, "p90": 0.0, "p95": 0.0}
    
    valores_ord = sorted(valores)
    n = len(valores_ord)
    
    def percentile(p: float) -> float:
        idx = (n - 1) * (p / 100.0)
        idx_floor = int(idx)
        idx_ceil = min(idx_floor + 1, n - 1)
        weight = idx - idx_floor
        return valores_ord[idx_floor] * (1.0 - weight) + valores_ord[idx_ceil] * weight

    return {
        "mean": sum(valores_ord) / n,
        "max": max(valores_ord),
        "min": min(valores_ord),
        "median": percentile(50.0),
        "p75": percentile(75.0),
        "p90": percentile(90.0),
        "p95": percentile(95.0),
    }


def _gerar_relatorio_md(
    report_path: str,
    summary: dict,
    f1_medio: float,
    exact_match_rate: float,
    all_rows: list[dict],
    failures: list[dict],
    model: str,
    sample_size: int,
    seed: int,
    data_dir: str,
) -> None:
    """Gera um relatório textual em Markdown com estatísticas e detalhes de falhas."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)

    lines = []
    lines.append("# Spider Evaluation Report")
    lines.append("")
    lines.append(f"**Gerado em:** {timestamp}")
    lines.append("")

    # --- Configuração ---
    lines.append("## Configuração")
    lines.append("")
    lines.append(f"| Parâmetro | Valor |")
    lines.append(f"|-----------|-------|")
    lines.append(f"| Modelo | `{model}` |")
    lines.append(f"| Sample size | {sample_size} |")
    lines.append(f"| Seed | {seed} |")
    lines.append(f"| Data dir | `{data_dir}` |")
    lines.append("")

    # --- Resumo ---
    lines.append("## Resumo")
    lines.append("")
    lines.append(f"| Métrica | Valor |")
    lines.append(f"|---------|-------|")
    lines.append(f"| Total de perguntas | {summary['total_perguntas']} |")
    lines.append(f"| Total de tentativas | {summary['total_tentativas']} |")
    lines.append(f"| Tentativas médias por pergunta | {summary['tentativas_media']:.2f} |")
    lines.append(f"| Similarity score médio (SQL) | {summary['similarity_media']:.4f} |")
    lines.append(f"| F1 score médio (resultados) | {f1_medio:.4f} |")
    lines.append(f"| Exact match rate | {exact_match_rate:.1%} |")
    lines.append(f"| Falhas (Failures/Mismatches) | {len(failures)}/{len(all_rows)} |")
    lines.append(f"| Tempo médio por tentativa | {summary['tempo_medio_ms']:.0f} ms |")
    lines.append("")

    # --- Métricas de Custo e Latência ---
    input_tokens = [float(r.get("tokens_input", 0) or 0) for r in all_rows]
    output_tokens = [float(r.get("tokens_output", 0) or 0) for r in all_rows]
    total_tokens = [float(r.get("tokens_total", 0) or 0) for r in all_rows]
    latencies = [float(r.get("tempo_agente_ms", 0) or 0) for r in all_rows]

    stats_input = _calcular_estatisticas(input_tokens)
    stats_output = _calcular_estatisticas(output_tokens)
    stats_total = _calcular_estatisticas(total_tokens)
    stats_latency = _calcular_estatisticas(latencies)

    lines.append("## Métricas de Custo e Latência")
    lines.append("")
    lines.append("| Métrica | Média (Mean) | Máx (Max) | Mín (Min/Mid) | Mediana (Median) | P75 | P90 | P95 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    
    def fmt_row(label, stats, is_latency=False):
        fmt = ".2f" if is_latency else ".1f"
        suffix = " ms" if is_latency else ""
        return (
            f"| {label} "
            f"| {stats['mean']:{fmt}}{suffix} "
            f"| {stats['max']:{fmt}}{suffix} "
            f"| {stats['min']:{fmt}}{suffix} "
            f"| {stats['median']:{fmt}}{suffix} "
            f"| {stats['p75']:{fmt}}{suffix} "
            f"| {stats['p90']:{fmt}}{suffix} "
            f"| {stats['p95']:{fmt}}{suffix} |"
        )

    lines.append(fmt_row("Input Tokens", stats_input))
    lines.append(fmt_row("Output Tokens", stats_output))
    lines.append(fmt_row("Total Tokens", stats_total))
    lines.append(fmt_row("Latency", stats_latency, is_latency=True))
    lines.append("")

    # --- Tabela por pergunta ---
    lines.append("## Resultados por Pergunta")
    lines.append("")
    lines.append("| # | DB | Pergunta | Match | F1 | Similarity |")
    lines.append("|---|-----|----------|-------|----|------------|")
    for r in all_rows:
        pergunta_curta = str(r['pergunta_usuario'])[:50]
        match_icon = "✅" if r['resultado_exato_match'] is True else ("❌" if r['resultado_exato_match'] is False else "⚠️")
        lines.append(
            f"| {r['id_exemplo']} "
            f"| {r['db_id']} "
            f"| {pergunta_curta}... "
            f"| {match_icon} "
            f"| {r.get('resultado_f1', 0):.2f} "
            f"| {r['similarity_score_sql']:.2f} |"
        )
    lines.append("")

    # --- Detalhes das falhas ---
    if failures:
        lines.append("## Detalhes das Falhas (Failures/Mismatches)")
        lines.append("")
        lines.append(f"Total: **{len(failures)}** perguntas não obtiveram exact match.")
        lines.append("")

        for i, f in enumerate(failures, 1):
            lines.append(f"### Falha {i} — Pergunta #{f['id']} (`{f['db_id']}`)")
            lines.append("")
            lines.append(f"**Pergunta:** {f['pergunta']}")
            lines.append("")
            lines.append(f"**F1:** {f['f1']:.4f} | **Precision:** {f['precision']:.4f} | **Recall:** {f['recall']:.4f}")
            lines.append("")
            if f.get("erro_execucao"):
                lines.append(f"❌ **Erro de execução final:** `{f['erro_execucao']}`")
                lines.append("")

            # Query Ouro
            lines.append("**Query Ouro (Spider):**")
            lines.append(f"```sql\n{f['query_ouro']}\n```")
            lines.append("")

            # Tentativas
            attempts = f.get("historico_tentativas", [])
            if not attempts:
                attempts = [{
                    "sql": f.get("query_agente", ""),
                    "erro": f.get("erro_execucao", ""),
                    "prompt": "Não disponível",
                    "contexto": "Não disponível",
                    "raciocinio": ""
                }]

            lines.append("#### Tentativas de Resolução (Attempts):")
            lines.append("")
            for idx_att, att in enumerate(attempts, 1):
                lines.append(f"##### Tentativa {idx_att}:")
                lines.append("")

                # Contexto
                contexto = att.get('contexto', '')
                if contexto:
                    lines.append("<details>")
                    lines.append(f"<summary><strong>Tabelas e Contexto do Schema</strong> (Data Exploration)</summary>")
                    lines.append("")
                    lines.append("```")
                    lines.append(contexto)
                    lines.append("```")
                    lines.append("")
                    lines.append("</details>")
                    lines.append("")

                # Raciocínio (CoT)
                raciocinio = att.get('raciocinio', '')
                if raciocinio:
                    lines.append("<details>")
                    lines.append(f"<summary><strong>Raciocínio (Chain of Thought)</strong></summary>")
                    lines.append("")
                    lines.append(raciocinio)
                    lines.append("")
                    lines.append("</details>")
                    lines.append("")

                # Prompt
                prompt = att.get('prompt', '')
                if prompt:
                    lines.append("<details>")
                    lines.append(f"<summary><strong>Prompt do Code Agent</strong></summary>")
                    lines.append("")
                    lines.append("```")
                    lines.append(prompt)
                    lines.append("```")
                    lines.append("")
                    lines.append("</details>")
                    lines.append("")

                # SQL Gerada
                lines.append("**SQL Gerada:**")
                lines.append(f"```sql\n{att.get('sql', '(vazia)')}\n```")
                lines.append("")

                # Erro
                erro = att.get('erro', '')
                if erro:
                    lines.append(f"❌ **Erro de execução:** `{erro}`")
                else:
                    lines.append(f"✅ **Executado com sucesso**")
                lines.append("")
                lines.append("---")
                lines.append("")

            # Result comparison (show up to 20 rows each)
            lines.append("**Resultado Ouro** (primeiras 20 linhas):")
            lines.append("")
            ouro_sample = f['resultado_ouro'][:20] if f.get('resultado_ouro') else []
            if ouro_sample:
                cols = list(ouro_sample[0].keys())
                lines.append("| " + " | ".join(cols) + " |")
                lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
                for row in ouro_sample:
                    vals = [str(row.get(c, "")) for c in cols]
                    lines.append("| " + " | ".join(vals) + " |")
                if len(f['resultado_ouro']) > 20:
                    lines.append(f"*... e mais {len(f['resultado_ouro']) - 20} linhas*")
            else:
                lines.append("*(vazio)*")
            lines.append("")

            lines.append("**Resultado Agente** (primeiras 20 linhas):")
            lines.append("")
            agent_sample = f['resultado_agente'][:20] if f.get('resultado_agente') else []
            if agent_sample:
                cols = list(agent_sample[0].keys())
                lines.append("| " + " | ".join(cols) + " |")
                lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
                for row in agent_sample:
                    vals = [str(row.get(c, "")) for c in cols]
                    lines.append("| " + " | ".join(vals) + " |")
                if len(f['resultado_agente']) > 20:
                    lines.append(f"*... e mais {len(f['resultado_agente']) - 20} linhas*")
            else:
                lines.append("*(vazio)*")
            lines.append("")
            lines.append("---")
            lines.append("")
    else:
        lines.append("## Detalhes das Falhas (Failures/Mismatches)")
        lines.append("")
        lines.append("🎉 **Nenhuma falha!** Todos os resultados foram exact match.")
        lines.append("")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


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
        default="spider_data",
        help="Diretório com dados do Spider",
    )

    # testar queries individualmente
    parser.add_argument(
        "--question-filter",
        type=str,
        help="Filtrar por um trecho específico da pergunta em inglês",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-4o-mini",
        help="Modelo LLM a utilizar (default: gpt-4o-mini)",
    )
    parser.add_argument(
        "--with-graphs",
        action="store_true",
        help="Ativar a geração de gráficos e salvamento de CSV",
    )
    parser.add_argument(
        "--report-dir",
        type=str,
        default="",
        help="Pasta dentro de 'reports' para salvar os relatórios .md (CSVs continuam fora)",
    )
    parser.add_argument(
        "--cot",
        choices=["on", "off"],
        default="on",
        help="Ativa/desativa o Chain of Thought (CoT). Padrão: on.",
    )
    parser.add_argument(
        "--data-exploration",
        choices=["on", "off"],
        default="on",
        help="Ativa/desativa a etapa de data exploration. Padrão: on.",
    )
    parser.add_argument(
        "--exploration-selector",
        choices=["off", "llm", "rag"],
        default="off",
        help="Modo de seleção de colunas para exploração. Padrão: off.",
    )
    parser.add_argument(
        "--rag",
        choices=["on", "off"],
        default="on",
        help="Ativa/desativa o RAG para recuperação do schema. Padrão: on.",
    )
    parser.add_argument(
        "--enrich-rag",
        choices=["on", "off"],
        default="off",
        help="Ativa/desativa o enriquecimento RAG. Padrão: off.",
    )

    args = parser.parse_args()

    # Validar API key
    model = args.model
    
    api_key = os.getenv("OPENAI_API_KEY") if "gpt" in model.lower() else os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("❌ Erro: Chave API não encontrada em .env")
        sys.exit(1)

    # 1. Carregar dados
    print(f"\n📂 Carregando exemplos do Spider de {args.data_dir}...")
    try:
        exemplos = load_spider_dev_examples(args.data_dir)
        print(f"✓ Carregados {len(exemplos)} exemplos")
    except FileNotFoundError as e:
        print(f"❌ {e}")
        sys.exit(1)

    # 2. Aplicar filtros
    if args.db_filter:
        exemplos = filter_by_db_id(exemplos, args.db_filter)
        print(f"✓ Filtrados por db_id={args.db_filter}: {len(exemplos)} exemplos")

    # --- NOVO TRECHO ADICIONADO ---
    if args.question_filter:
        exemplos = [
            ex for ex in exemplos 
            if args.question_filter.lower() in ex.get("question", "").lower()
        ]
        print(f"✓ Filtrados pela pergunta contendo '{args.question_filter}': {len(exemplos)} exemplos")
    
    # 3. Fazer sampling
    exemplos = sample_examples(exemplos, sample_size=args.sample_size, seed=args.seed)
    print(
        f"✓ Selecionados {len(exemplos)} exemplos (seed={args.seed}, "
        f"bancos únicos: {len(get_unique_db_ids(exemplos))})"
    )

    # 4. Inicializar componentes
    print("\n🔧 Inicializando componentes...")

    executor = SpiderQueryExecutor(database_dir=str(Path(args.data_dir) / "database"))
    print("✓ Query executor inicializado")

    # 5. Preparar CSV
    if args.output:
        csv_path = args.output
    else:
        csv_path = f"reports/{CSVReporter.generate_timestamped_filename('spider_eval')}"

    reporter = CSVReporter(csv_path)
    print(f"✓ CSV reporter inicializado: {csv_path}")

    # 6. Loop de testes
    print(f"\n🚀 Iniciando avaliação com {len(exemplos)} perguntas...\n")
    print("=" * 100)

    all_rows = []
    failures = []  # Coletar detalhes dos casos que falharam
    ex_id = 1

    # Cache de InsightEngine por db_id para evitar recompilação do grafo
    engine_cache: dict[str, InsightEngine] = {}

    for idx, ex in enumerate(exemplos, 1):
        pergunta = ex.get("question", "")
        query_ouro = ex.get("query", "")
        db_id = ex.get("db_id", "")

        print(f"\n[{idx}/{len(exemplos)}] Pergunta: {pergunta[:60]}...")
        print(f"     DB: {db_id} | Query Ouro: {query_ouro[:50]}...")

        # Executar query ouro para obter resultado esperado
        print(f"     → Executando query ouro...")
        resultado_ouro = executor.execute_query(db_id, query_ouro)

        if not resultado_ouro["success"]:
            print(f"     ⚠️  Erro na query ouro: {resultado_ouro['error']}")
            continue  # Pular este exemplo

        print(f"     ✓ Query ouro retornou {resultado_ouro['row_count']} linhas")

        # Obter ou criar InsightEngine para este db_id
        db_path = str(executor.get_db_path(db_id))
        if db_id not in engine_cache:
            try:
                engine_cache[db_id] = InsightEngine(
                    api_key=api_key,
                    model=model,
                    db_path=db_path,
                    hitl=False,
                    show_output=False,
                    enable_graphs=args.with_graphs,
                    use_cot=(args.cot == "on"),
                    use_data_exploration=(args.data_exploration == "on"),
                    use_exploration_selector=args.exploration_selector,
                    use_rag=(args.rag == "on"),
                    enrich_rag=(args.enrich_rag == "on"),
                )
                print(f"     ✓ InsightEngine inicializado para db={db_id} (CoT={args.cot}, DataExploration={args.data_exploration}, ExplorationSelector={args.exploration_selector}, RAG={args.rag}, EnrichRAG={args.enrich_rag})")
            except Exception as e:
                print(f"     ❌ Erro ao inicializar InsightEngine: {e}")
                continue

        engine = engine_cache[db_id]

        # Invocar agente via InsightEngine.run()
        print(f"     → Invocando agente via InsightEngine...")
        inicio_agente = time.time()

        try:
            resultado = engine.run(
                thread_id=f"spider_test_{ex_id}",
                query=pergunta,
            )
        except Exception as e:
            print(f"     ⚠️  Erro ao processar pergunta: {str(e)}")
            continue

        tempo_total = (time.time() - inicio_agente) * 1000

        # Extrair dados do resultado
        query_agente = resultado.get("sql_gerada", "")
        veredito = resultado.get("status", "")
        feedback_estado = resultado.get("feedback_critico", "")
        erro_exec = resultado.get("erro_execucao", "")
        tentativas = resultado.get("tentativas_loop", 1)
        historico_tent = resultado.get("historico_tentativas", [])
        raciocinio_agente = resultado.get("raciocinio_agente", "")
        contexto_prompt_agente = resultado.get("contexto_prompt_agente", "")

        # Extrair métricas de tokens acumuladas
        tokens_input = resultado.get("tokens_input", 0) or 0
        tokens_output = resultado.get("tokens_output", 0) or 0
        tokens_total = resultado.get("tokens_total", 0) or 0

        # Extrair dados do agente de visualização
        viz_acionado = "grafico_gerado" in resultado
        viz_sucesso = resultado.get("grafico_gerado", False)

        # Extrair SQL da 1ª tentativa para ablação do Crítico
        query_1a_tentativa = ""
        if historico_tent and isinstance(historico_tent, list) and len(historico_tent) > 0:
            query_1a_tentativa = historico_tent[0].get("sql", "")
        if not query_1a_tentativa:
            query_1a_tentativa = query_agente  # fallback: se só houve 1 tentativa

        # Mapear status para veredito e definir feedback
        if veredito == "aprovado":
            veredito_critico = "aprovado"
            feedback_critico = feedback_estado if feedback_estado else "Aprovado"
        elif veredito == "reprovado":
            veredito_critico = "reprovado"
            feedback_critico = feedback_estado if feedback_estado else "Reprovado pelo crítico"
        else:
            veredito_critico = "erro"
            feedback_critico = feedback_estado if feedback_estado else "Erro na avaliação"

        # Comparar resultados se query agente foi gerada
        resultado_exato_match = None
        resultado_exato_match_1a = None
        resultado_f1_1a = 0.0
        similarity_score = 0.0
        f1_scores = {"f1": 0.0, "precision": 0.0, "recall": 0.0}

        if query_agente and not erro_exec:
            resultado_agente = executor.execute_query(db_id, query_agente)
            if resultado_agente["success"]:
                # Imprimir os resultados das duas queries
                print(f"Resultado Ouro: {resultado_ouro['results'][:50]}")
                print(f"Resultado Text-to-Insight: {resultado_agente['results'][:50]}")

                resultado_exato_match = results_exact_match(
                    resultado_ouro["results"],
                    resultado_agente["results"],
                )
                similarity_score = sql_similarity_score(query_ouro, query_agente)
                f1_scores = results_f1_score(
                    resultado_ouro["results"],
                    resultado_agente["results"],
                )

                # Ablação: calcular exact match e F1 da 1ª tentativa
                if query_1a_tentativa and query_1a_tentativa != query_agente:
                    res_1a = executor.execute_query(db_id, query_1a_tentativa)
                    if res_1a["success"]:
                        resultado_exato_match_1a = results_exact_match(
                            resultado_ouro["results"], res_1a["results"]
                        )
                        f1_1a = results_f1_score(
                            resultado_ouro["results"], res_1a["results"]
                        )
                        resultado_f1_1a = f1_1a["f1"]
                    else:
                        resultado_exato_match_1a = False
                        resultado_f1_1a = 0.0
                else:
                    resultado_exato_match_1a = resultado_exato_match
                    resultado_f1_1a = f1_scores["f1"]

                print(
                    f"       Resultado final ({tentativas} tentativa(s)): "
                    f"similarity={similarity_score:.2f}, "
                    f"match={resultado_exato_match}, "
                    f"F1={f1_scores['f1']:.2f}"
                )
            else:
                erro_exec = resultado_agente["error"]
        else:
            print(
                f"       Resultado final ({tentativas} tentativa(s)): "
                f"sem query gerada ou com erro de execução"
            )

        # Coletar detalhes se não foi exact match
        if resultado_exato_match is not True:
            failures.append({
                "id": ex_id,
                "db_id": db_id,
                "pergunta": pergunta,
                "query_ouro": query_ouro,
                "query_agente": query_agente,
                "resultado_ouro": resultado_ouro["results"] if resultado_ouro.get("success") else [],
                "resultado_agente": resultado_agente["results"] if (query_agente and not erro_exec and resultado_agente.get("success")) else [],
                "f1": f1_scores["f1"] if 'f1_scores' in locals() and f1_scores else 0.0,
                "precision": f1_scores["precision"] if 'f1_scores' in locals() and f1_scores else 0.0,
                "recall": f1_scores["recall"] if 'f1_scores' in locals() and f1_scores else 0.0,
                "erro_execucao": erro_exec,
                "historico_tentativas": historico_tent,
            })

        # Construir linha para CSV
        row = build_comparison_row(
            id_exemplo=ex_id,
            tentativa_numero=tentativas,
            db_id=db_id,
            pergunta=pergunta,
            query_ouro=query_ouro,
            query_agente=query_agente,
            tempo_agente_ms=tempo_total,
            veredito_critico="",
            feedback_critico="",
            erro_execucao=erro_exec,
            resultado_exato_match=resultado_exato_match,
            similarity_score=similarity_score,
            resultado_f1=f1_scores["f1"] if 'f1_scores' in locals() and f1_scores else 0.0,
            resultado_precision=f1_scores["precision"] if 'f1_scores' in locals() and f1_scores else 0.0,
            resultado_recall=f1_scores["recall"] if 'f1_scores' in locals() and f1_scores else 0.0,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            tokens_total=tokens_total,
            viz_acionado=viz_acionado,
            viz_sucesso=viz_sucesso,
            resultado_exato_match_1a_tentativa=None,
            resultado_f1_1a_tentativa=0.0,
            query_1a_tentativa="",
        )

        reporter.append_row(row)
        all_rows.append(row)

        print(f"     ✓ Concluído após {tentativas} tentativa(s)")

        ex_id += 1
        time.sleep(1)  # Delay entre perguntas

    # 7. Gerar resumo
    print("\n" + "=" * 100)
    print("📊 RESUMO FINAL")
    print("=" * 100)

    if all_rows:
        summary = reporter.generate_summary(all_rows)
        # Calcular F1 médio
        f1_values = [float(r.get("resultado_f1", 0.0) or 0.0) for r in all_rows]
        f1_medio = sum(f1_values) / len(f1_values) if f1_values else 0.0
        # Calcular exact match rate
        match_values = [r.get("resultado_exato_match") for r in all_rows]
        exact_matches = sum(1 for v in match_values if v is True)
        exact_match_rate = exact_matches / len(all_rows) if all_rows else 0.0

        print(f"Total de perguntas: {summary['total_perguntas']}")
        print(f"Total de tentativas: {summary['total_tentativas']}")
        print(f"Tentativas médias por pergunta: {summary['tentativas_media']:.2f}")
        print(f"Similarity score médio: {summary['similarity_media']:.4f}")
        print(f"F1 score médio (resultados): {f1_medio:.4f}")
        print(f"Exact match rate: {exact_match_rate:.1%}")
        print(f"Falhas: {len(failures)}/{len(all_rows)}")
        print(f"Tempo médio por tentativa: {summary['tempo_medio_ms']:.2f} ms")
        print(f"\n✅ CSV salvo em: {csv_path}")

        # 8. Gerar relatório textual em Markdown
        if args.report_dir:
            md_dir = Path("reports") / args.report_dir
            md_dir.mkdir(parents=True, exist_ok=True)
            report_path = str(md_dir / f"{Path(csv_path).stem}_report.md")
            empirico_path = str(md_dir / f"{Path(csv_path).stem}_empirico.md")
        else:
            report_path = csv_path.replace(".csv", "_report.md")
            empirico_path = csv_path.replace(".csv", "_empirico.md")

        _gerar_relatorio_md(
            report_path=report_path,
            summary=summary,
            f1_medio=f1_medio,
            exact_match_rate=exact_match_rate,
            all_rows=all_rows,
            failures=failures,
            model=model,
            sample_size=args.sample_size,
            seed=args.seed,
            data_dir=args.data_dir,
        )
        print(f"✅ Relatório salvo em: {report_path}")

        # 9. Gerar relatório empírico completo (análises do orientador)
        empirico_dir = str((Path(empirico_path).parent / Path(csv_path).stem).absolute()) + "_empirico"
        gerar_relatorio_empirico_completo(
            report_path=empirico_path,
            dataset_label="Spider",
            all_rows=all_rows,
            output_dir=empirico_dir,
        )
        print(f"✅ Relatório empírico salvo em: {empirico_path}")
        print(f"   Gráficos e CSVs auxiliares em: {Path(empirico_dir).relative_to(Path.cwd())}/")
    else:
        print("❌ Nenhum resultado para salvar")


if __name__ == "__main__":
    main()
