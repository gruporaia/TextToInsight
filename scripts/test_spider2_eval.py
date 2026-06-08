#!/usr/bin/env python3
"""
Script de Avaliação do Agente contra Spider 2.0 Lite Dataset.

Testa o agente Text-to-Insight contra perguntas reais do Spider 2.0 Lite dataset,
usando a classe InsightEngine do pacote text_to_insight. Foca especificamente
nas instâncias "local" que correspondem a bancos SQLite.

Uso:
    python scripts/test_spider2_eval.py --sample-size 10 --seed 42
    python scripts/test_spider2_eval.py --db-filter E_commerce --output reports/eval_spider2.csv
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
import random
import glob
import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

# Importar InsightEngine do pacote text_to_insight
from text_to_insight import InsightEngine

from src.spider.csv_reporter import CSVReporter
from src.spider.metrics import (
    build_comparison_row,
    results_exact_match,
    results_f1_score,
    sql_similarity_score,
)
from src.spider.query_executor import SpiderQueryExecutor
from src.spider.analise_empirica import gerar_relatorio_empirico_completo

load_dotenv()


class Spider2QueryExecutor(SpiderQueryExecutor):
    """
    Executor adaptado para o Spider 2.0 Lite, 
    onde os bancos locais geralmente estão na raiz da pasta.
    """
    def get_db_path(self, db_id: str) -> Path:
        # Tenta na raiz
        db_path = self.database_dir / f"{db_id}.sqlite"
        if not db_path.exists():
            # Tenta na subpasta como no Spider 1.0
            db_path = self.database_dir / db_id / f"{db_id}.sqlite"
        if not db_path.exists():
            raise FileNotFoundError(f"Banco não encontrado: {db_path} (nem na subpasta)")
        return db_path


def load_spider2_examples(data_dir: str) -> list[dict]:
    """Carrega as instâncias do spider2-lite.jsonl"""
    jsonl_path = Path(data_dir) / "spider2-lite.jsonl"
    if not jsonl_path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {jsonl_path}")
    
    examples = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                examples.append(json.loads(line))
    return examples


def get_gold_sql(data_dir: str, instance_id: str) -> str:
    """Lê a query gold da pasta evaluation_suite/gold/sql/"""
    sql_path = Path(data_dir) / "evaluation_suite" / "gold" / "sql" / f"{instance_id}.sql"
    if not sql_path.exists():
        return ""
    with open(sql_path, "r", encoding="utf-8") as f:
        return f.read().strip()


def get_gold_results(data_dir: str, instance_id: str) -> list[list[dict]]:
    """Carrega os resultados gold (CSVs) para um dado instance_id.
    Pode haver múltiplos CSVs (e.g. local040_a.csv, local040_b.csv).
    """
    exec_result_dir = Path(data_dir) / "evaluation_suite" / "gold" / "exec_result"
    
    # Try exact match first
    exact_match = exec_result_dir / f"{instance_id}.csv"
    if exact_match.exists():
        try:
            return [pd.read_csv(exact_match).to_dict(orient="records")]
        except Exception:
            pass
            
    # Try multiple (e.g. _a, _b)
    pattern = str(exec_result_dir / f"{instance_id}_*.csv")
    files = sorted(glob.glob(pattern))
    results = []
    for f in files:
        try:
            results.append(pd.read_csv(f).to_dict(orient="records"))
        except Exception:
            pass
            
    return results


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
    lines.append("# Spider 2.0 Lite Evaluation Report")
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
    lines.append("| Instance ID | DB | Pergunta | Match | F1 | Similarity |")
    lines.append("|-------------|----|----------|-------|----|------------|")
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
            lines.append(f"### Falha {i} — Instância `{f['id']}` (`{f['db_id']}`)")
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
    parser = argparse.ArgumentParser(description="Avaliar agente Text-to-Insight contra Spider 2.0 Lite")
    parser.add_argument("--sample-size", type=int, default=10, help="Quantas perguntas testar")
    parser.add_argument("--seed", type=int, default=42, help="Seed para reproducibilidade")
    parser.add_argument("--db-filter", type=str, help="Filtrar por banco específico (ex: E_commerce)")
    parser.add_argument("--output", type=str, help="Caminho para salvar CSV")
    parser.add_argument("--max-attempts", type=int, default=3, help="Máximo de tentativas por pergunta")
    parser.add_argument("--data-dir", type=str, default="spider2-lite", help="Diretório base do Spider 2 Lite")
    parser.add_argument("--sqlite-dir", type=str, default="spider2-lite/resource/databases/spider2-localdb", help="Diretório contendo os bancos sqlite do Spider 2")
    parser.add_argument("--question-filter", type=str, help="Filtrar por um trecho da pergunta")
    parser.add_argument("--model", type=str, default="gpt-5-mini", help="Modelo LLM a utilizar")
    parser.add_argument("--with-graphs", action="store_true", help="Ativar a geração de gráficos e salvamento de CSV")
    parser.add_argument("--report-dir", type=str, default="", help="Pasta dentro de 'reports' para salvar os relatórios .md")
    parser.add_argument("--infer-fks", action="store_true", help="Ativar inferência de FKs virtuais (Spider 2 Lite local)")
    parser.add_argument("--no-schemacrawler", action="store_true", help="Desativar o uso do SchemaCrawler")
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

    print(f"\n📂 Carregando exemplos do Spider 2 Lite de {args.data_dir}...")
    try:
        exemplos = load_spider2_examples(args.data_dir)
        print(f"✓ Carregados {len(exemplos)} exemplos totais")
    except FileNotFoundError as e:
        print(f"❌ {e}")
        sys.exit(1)

    # Filtrar apenas os locais (SQLite) já que o framework suporta SQLite nativamente
    exemplos = [ex for ex in exemplos if ex.get("instance_id", "").startswith("local")]
    print(f"✓ Filtrados para {len(exemplos)} exemplos baseados em SQLite (prefixo 'local')")

    if args.db_filter:
        exemplos = [ex for ex in exemplos if ex.get("db") == args.db_filter]
        print(f"✓ Filtrados por db_id={args.db_filter}: {len(exemplos)} exemplos")

    if args.question_filter:
        exemplos = [ex for ex in exemplos if args.question_filter.lower() in ex.get("question", "").lower()]
        print(f"✓ Filtrados pela pergunta '{args.question_filter}': {len(exemplos)} exemplos")
    
    if args.seed is not None:
        random.seed(args.seed)
    if args.sample_size is not None and args.sample_size < len(exemplos):
        exemplos = random.sample(exemplos, k=args.sample_size)
        
    print(f"✓ Selecionados {len(exemplos)} exemplos para teste.")

    print("\n🔧 Inicializando componentes...")
    executor = Spider2QueryExecutor(database_dir=args.sqlite_dir)
    print("✓ Query executor inicializado")

    csv_path = args.output if args.output else f"reports/{CSVReporter.generate_timestamped_filename('spider2_eval')}"
    reporter = CSVReporter(csv_path)
    print(f"✓ CSV reporter inicializado: {csv_path}")

    print(f"\n🚀 Iniciando avaliação com {len(exemplos)} perguntas...\n")
    print("=" * 100)

    all_rows = []
    failures = []
    engine_cache = {}

    for idx, ex in enumerate(exemplos, 1):
        instance_id = ex.get("instance_id")
        db_id = ex.get("db", "")
        pergunta = ex.get("question", "")
        

        # Carregar contexto externo (external_knowledge) se disponível
        external_knowledge_file = ex.get("external_knowledge")
        if external_knowledge_file:
            ek_path = Path(args.data_dir) / "resource" / "documents" / external_knowledge_file
            if ek_path.exists():
                ek_content = ek_path.read_text(encoding="utf-8").strip()
                pergunta = f"{pergunta}\n\n<additional_context>\n{ek_content}\n</additional_context>"
                print(f"     📎 Contexto externo carregado: {external_knowledge_file} ({len(ek_content)} chars)")
            else:
                print(f"     ⚠️  Arquivo de external_knowledge não encontrado: {ek_path}")

        # Recuperar query ouro e/ou csvs ouro
        query_ouro = get_gold_sql(args.data_dir, instance_id)
        gold_results_list = get_gold_results(args.data_dir, instance_id)

        if not query_ouro and not gold_results_list:
            print(f"\n[{idx}/{len(exemplos)}] ⚠️ Nenhuma query ouro nem resultado CSV encontrados para {instance_id}. Pulando.")
            continue

        print(f"\n[{idx}/{len(exemplos)}] Instance: {instance_id} | DB: {db_id} | Pergunta: {pergunta[:60]}...")
        if query_ouro:
            print(f"     → Query Ouro: {query_ouro[:50]}...")
        else:
            print(f"     → Query Ouro não fornecida (avaliando via CSVs oficiais).")

        # Configurar resultado ouro (para relatórios e fallback)
        if gold_results_list:
            resultado_ouro_primeiro = {"success": True, "results": gold_results_list[0], "row_count": len(gold_results_list[0])}
            print(f"     ✓ CSV Ouro carregado ({len(gold_results_list)} variantes, usando a primeira para display com {len(gold_results_list[0])} linhas)")
        else:
            resultado_ouro_primeiro = executor.execute_query(db_id, query_ouro)
            if not resultado_ouro_primeiro["success"]:
                print(f"     ⚠️  Erro na query ouro ou db ausente: {resultado_ouro_primeiro['error']}")
                print("     (Aviso: Certifique-se de baixar e extrair os bancos locais em spider2-localdb)")
                continue
            gold_results_list = [resultado_ouro_primeiro["results"]]
            print(f"     ✓ Query ouro retornou {resultado_ouro_primeiro['row_count']} linhas")

        # Inicializar engine
        try:
            db_path = str(executor.get_db_path(db_id))
        except FileNotFoundError as e:
            print(f"     ❌ {e}")
            continue
            
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
                    inferir_fks_virtuais=args.infer_fks,
                    usar_schemacrawler=not args.no_schemacrawler,
                    enrich_rag=(args.enrich_rag == "on"),
                )
                print(f"     ✓ InsightEngine inicializado para db={db_id} (CoT={args.cot}, DataExploration={args.data_exploration}, ExplorationSelector={args.exploration_selector}, RAG={args.rag}, EnrichRAG={args.enrich_rag})")
            except Exception as e:
                print(f"     ❌ Erro ao inicializar InsightEngine: {e}")
                continue

        engine = engine_cache[db_id]

        print(f"     → Invocando agente...")
        inicio_agente = time.time()
        try:
            resultado = engine.run(thread_id=f"spider2_test_{instance_id}", query=pergunta)
        except Exception as e:
            print(f"     ⚠️  Erro ao processar pergunta: {str(e)}")
            continue

        tempo_total = (time.time() - inicio_agente) * 1000

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

        if veredito == "aprovado":
            veredito_critico = "aprovado"
            feedback_critico = feedback_estado if feedback_estado else "Aprovado"
        elif veredito == "reprovado":
            veredito_critico = "reprovado"
            feedback_critico = feedback_estado if feedback_estado else "Reprovado pelo crítico"
        else:
            veredito_critico = "erro"
            feedback_critico = feedback_estado if feedback_estado else "Erro na avaliação"

        resultado_exato_match = None
        resultado_exato_match_1a = None
        resultado_f1_1a = 0.0
        similarity_score = 0.0
        f1_scores = {"f1": 0.0, "precision": 0.0, "recall": 0.0}

        if query_agente and not erro_exec:
            resultado_agente = executor.execute_query(db_id, query_agente)
            if resultado_agente["success"]:
                if query_ouro:
                    similarity_score = sql_similarity_score(query_ouro, query_agente)
                
                # Testar contra todas as variantes de ouro e pegar a melhor pontuação
                best_match = False
                best_f1 = {"f1": 0.0, "precision": 0.0, "recall": 0.0}
                
                for gold_res in gold_results_list:
                    match_atual = results_exact_match(gold_res, resultado_agente["results"])
                    f1_atual = results_f1_score(gold_res, resultado_agente["results"])
                    
                    if match_atual:
                        best_match = True
                    
                    if f1_atual["f1"] > best_f1["f1"]:
                        best_f1 = f1_atual
                
                resultado_exato_match = best_match
                f1_scores = best_f1

                print(
                    f"       Resultado final ({tentativas} tentativa(s)): "
                    f"similarity={similarity_score:.2f}, "
                    f"match={resultado_exato_match}, "
                    f"F1={f1_scores['f1']:.2f}"
                )
            else:
                erro_exec = resultado_agente["error"]
        else:
            print(f"       Resultado final ({tentativas} tentativa(s)): sem query gerada ou com erro")

        # Coletar detalhes se não foi exact match
        if resultado_exato_match is not True:
            failures.append({
                "id": instance_id,
                "db_id": db_id,
                "pergunta": pergunta,
                "query_ouro": query_ouro,
                "query_agente": query_agente,
                "resultado_ouro": gold_results_list[0] if gold_results_list else [],
                "resultado_agente": resultado_agente["results"] if (query_agente and not erro_exec and resultado_agente.get("success")) else [],
                "f1": f1_scores["f1"] if 'f1_scores' in locals() and f1_scores else 0.0,
                "precision": f1_scores["precision"] if 'f1_scores' in locals() and f1_scores else 0.0,
                "recall": f1_scores["recall"] if 'f1_scores' in locals() and f1_scores else 0.0,
                "erro_execucao": erro_exec,
                "historico_tentativas": historico_tent,
            })

        row = build_comparison_row(
            id_exemplo=instance_id,
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

    print("\n" + "=" * 100)
    print("📊 RESUMO FINAL SPIDER 2 LITE")
    print("=" * 100)

    if all_rows:
        summary = reporter.generate_summary(all_rows)
        f1_medio = sum(float(r.get("resultado_f1", 0.0) or 0.0) for r in all_rows) / len(all_rows) if all_rows else 0.0
        match_values = [r.get("resultado_exato_match") for r in all_rows]
        exact_matches = sum(1 for v in match_values if v is True)
        exact_match_rate = exact_matches / len(all_rows) if all_rows else 0.0

        print(f"Total de perguntas processadas: {summary['total_perguntas']}")
        print(f"Tentativas médias por pergunta: {summary['tentativas_media']:.2f}")
        print(f"Similarity score médio: {summary['similarity_media']:.4f}")
        print(f"F1 score médio (resultados): {f1_medio:.4f}")
        print(f"Exact match rate: {exact_match_rate:.1%}")
        print(f"Falhas: {len(failures)}/{len(all_rows)}")
        print(f"Tempo médio por tentativa: {summary['tempo_medio_ms']:.2f} ms")
        print(f"\n✅ CSV salvo em: {csv_path}")

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
            dataset_label="Spider 2.0 Lite",
            all_rows=all_rows,
            output_dir=empirico_dir,
        )
        print(f"✅ Relatório empírico salvo em: {empirico_path}")
        print(f"   Gráficos e CSVs auxiliares em: {Path(empirico_dir).relative_to(Path.cwd())}/")
    else:
        print("❌ Nenhum resultado para salvar. (Verificou os bancos na pasta spider2-localdb?)")


if __name__ == "__main__":
    main()
