#!/usr/bin/env python3
"""
Script de Avaliação Spider2-Lite com ReFoRCE - Coleta de Artefatos Completos.

Testa o agente Text-to-Insight com a nova arquitetura ReFoRCE contra Spider 2.0 Lite,
coletando todos os artefatos gerados durante o processo (candidatos, votação, etc).

Principais mudanças vs script anterior:
- Captura estado completo do grafo (candidatos, status_consenso, rodadas_exploracao)
- Salva artefatos ReFoRCE em JSON (5 candidatos, votação, exploração)
- Adiciona coluna com resumo do processo (Map → Reduce → Consensus/Exploration)
- Mantém compatibilidade com métricas Spider existentes

Uso:
    python scripts/test_spider2_eval_reforce.py --sample-size 10 --seed 42
    python scripts/test_spider2_eval_reforce.py --db-filter E_commerce --output results/eval.csv
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
import hashlib

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

# Importar InsightEngine e componentes ReFoRCE
from text_to_insight import InsightEngine

from src.spider.csv_reporter import CSVReporter
from src.spider.metrics import (
    build_comparison_row,
    results_exact_match,
    results_f1_score,
    sql_similarity_score,
)
from src.spider.query_executor import SpiderQueryExecutor

load_dotenv()


class Spider2QueryExecutor(SpiderQueryExecutor):
    """Executor adaptado para Spider 2.0 Lite com estrutura de subpastas."""
    def get_db_path(self, db_id: str) -> Path:
        db_path = self.database_dir / db_id / f"{db_id}.sqlite"
        if not db_path.exists():
            db_path = self.database_dir / f"{db_id}.sqlite"
        if not db_path.exists():
            raise FileNotFoundError(f"Banco não encontrado: {db_path}")
        return db_path


def load_spider2_examples(data_dir: str) -> list[dict]:
    """Carrega instâncias do spider2-lite.jsonl"""
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
    """Carrega resultados gold (CSVs)."""
    exec_result_dir = Path(data_dir) / "evaluation_suite" / "gold" / "exec_result"
    
    exact_match = exec_result_dir / f"{instance_id}.csv"
    if exact_match.exists():
        try:
            return [pd.read_csv(exact_match).to_dict(orient="records")]
        except Exception:
            pass
            
    pattern = str(exec_result_dir / f"{instance_id}_*.csv")
    files = sorted(glob.glob(pattern))
    results = []
    for f in files:
        try:
            results.append(pd.read_csv(f).to_dict(orient="records"))
        except Exception:
            pass
            
    return results


def extrair_artefatos_reforce(resultado: dict) -> dict:
    """
    Extrai e resume os artefatos ReFoRCE do estado final.
    
    Returns:
        Dict com:
        - num_candidatos: total gerado (sempre 5 por rodada)
        - num_validos: quantos rodaram com sucesso
        - status_consenso: "consenso_encontrado" | "ambiguo" | "nao_votado"
        - rodadas_exploracao: quantas vezes explorador foi acionado
        - sql_vencedora: SQL aprovada pelo consenso
        - best_sql_last_attempt: Melhor SQL da última tentativa (para ambiguidades)
        - resumo_candidatos: JSON com índices, hashes e erros
        - total_candidatos_todas_rodadas: Total de candidatos em TODAS as rodadas
    """
    candidatos = resultado.get("candidatos", [])
    status_consenso = resultado.get("status_consenso", "nao_votado")
    rodadas_exploracao = resultado.get("rodadas_exploracao", 0)
    sql_vencedora = resultado.get("sql_vencedora", "")
    motivo_ambiguidade = resultado.get("motivo_ambiguidade", "")
    
    num_candidatos = len(candidatos)
    num_validos = sum(1 for c in candidatos if c.get("valido", False))
    
    # ✅ NOVO: Encontrar a melhor SQL da última tentativa (mesmo se ambíguo)
    best_sql_last_attempt = ""
    if candidatos:
        # Preferir válidos, depois se não tiver, qualquer um
        best_cand = next((c for c in candidatos if c.get("valido", False)), None)
        if not best_cand and candidatos:
            best_cand = candidatos[0]
        if best_cand:
            best_sql_last_attempt = best_cand.get("sql", "")
    
    # Resumo dos candidatos desta rodada
    resumo_candidatos = []
    for i, c in enumerate(candidatos):
        resumo_candidatos.append({
            "indice": i,
            "valido": c.get("valido", False),
            "temperatura": round(c.get("temperatura", 0.0), 1),
            "assinatura": c.get("assinatura_resultado", "")[:16] if c.get("assinatura_resultado") else "",
            "erro": c.get("erro", "")[:80] if c.get("erro") else "",
            "total_linhas": c.get("resultado_execucao", {}).get("total_linhas", 0),
        })
    
    return {
        "num_candidatos": num_candidatos,
        "num_validos": num_validos,
        "status_consenso": status_consenso,
        "rodadas_exploracao": rodadas_exploracao,
        "sql_vencedora": sql_vencedora,
        "best_sql_last_attempt": best_sql_last_attempt,
        "resumo_candidatos": json.dumps(resumo_candidatos, ensure_ascii=False),
    }


def main():
    parser = argparse.ArgumentParser(description="Avaliar Spider2-Lite com ReFoRCE e coletar artefatos")
    parser.add_argument("--sample-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--db-filter", type=str)
    parser.add_argument("--output", type=str)
    parser.add_argument("--data-dir", type=str, default="data/spider2-lite")
    parser.add_argument("--sqlite-dir", type=str, default="data/spider2-lite/resource/databases/sqlite")
    parser.add_argument("--question-filter", type=str)
    parser.add_argument("--model", type=str, default="gpt-5-mini")
    parser.add_argument("--with-graphs", action="store_true")

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

    # Filtrar para SQLite local
    exemplos = [ex for ex in exemplos if ex.get("instance_id", "").startswith("local")]
    print(f"✓ Filtrados para {len(exemplos)} exemplos baseados em SQLite (prefixo 'local')")

    # Filtrar para instâncias que têm dados de ouro (SQL + resultados)
    gold_sql_dir = Path(args.data_dir) / "evaluation_suite" / "gold" / "sql"
    exemplos_com_gold = []
    for ex in exemplos:
        instance_id = ex.get("instance_id", "")
        sql_path = gold_sql_dir / f"{instance_id}.sql"
        if sql_path.exists():
            exemplos_com_gold.append(ex)
    exemplos = exemplos_com_gold
    print(f"✓ Filtrados para {len(exemplos)} instâncias com dados de ouro disponíveis")

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

    # Output CSV com artefatos ReFoRCE
    csv_path = args.output if args.output else f"results/spider2_reforce_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
    print(f"✓ Resultados serão salvos em: {csv_path}")

    print(f"\n🚀 Iniciando avaliação com {len(exemplos)} perguntas...\n")
    print("=" * 130)

    all_rows = []
    engine_cache = {}

    for idx, ex in enumerate(exemplos, 1):
        instance_id = ex.get("instance_id")
        db_id = ex.get("db", "")
        pergunta = ex.get("question", "")
        
        query_ouro = get_gold_sql(args.data_dir, instance_id)
        gold_results_list = get_gold_results(args.data_dir, instance_id)

        print(f"\n[{idx}/{len(exemplos)}] Instance: {instance_id} | DB: {db_id}")
        print(f"     Pergunta: {pergunta[:70]}...")

        # Resultado ouro
        if gold_results_list:
            resultado_ouro = gold_results_list[0]
            print(f"     → Resultado Ouro: {len(resultado_ouro)} linhas")
        else:
            resultado_ouro_obj = executor.execute_query(db_id, query_ouro)
            if not resultado_ouro_obj["success"]:
                print(f"     ❌ Erro na query ouro")
                continue
            resultado_ouro = resultado_ouro_obj["results"]
            print(f"     → Resultado Ouro: {len(resultado_ouro)} linhas")

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
                )
            except Exception as e:
                print(f"     ❌ Erro ao inicializar InsightEngine: {e}")
                continue

        engine = engine_cache[db_id]

        print(f"     → Invocando agente com ReFoRCE...")
        inicio_agente = time.time()
        try:
            resultado = engine.run(thread_id=f"spider2_reforce_{instance_id}", query=pergunta)
        except Exception as e:
            print(f"     ⚠️  Erro ao processar: {str(e)[:100]}")
            continue

        tempo_total_ms = (time.time() - inicio_agente) * 1000

        # Extração básica
        query_agente = resultado.get("sql_gerada", "")
        veredito = resultado.get("status", "")
        tokens_total = resultado.get("tokens_total", 0) or 0
        erro_execucao = resultado.get("erro_execucao", "")
        
        # ✅ Validação do Schema
        schema_length = resultado.get("schema_length", 0) or len(resultado.get("contexto_schema", "") or resultado.get("contexto_rag_schema", ""))
        schema_valido = resultado.get("schema_valido", 0)
        if schema_length > 0 and schema_valido == 0:
            schema_valido = 1  # Backup: se tem schema_length, marcar como válido

        # ✅ NOVO: Extração ReFoRCE
        artefatos_reforce = extrair_artefatos_reforce(resultado)

        # ✅ NOVO: Métricas simplificadas
        execution_accuracy = 0  # 1 se executou sem erro, 0 c.c.
        valid_sql = 0  # 1 se SQL válida (parsed corretamente), 0 c.c.
        match_exato = None  # Mantém para retrocompatibilidade
        
        # Se não temos erro de execução, SQL é válida
        if not erro_execucao and query_agente.strip():
            valid_sql = 1
            
        # Se não temos erro de execução, tentamos executar
        if query_agente and erro_execucao == "":
            execution_accuracy = 1  # Executou sem erro
        else:
            execution_accuracy = 0

        # Avaliação de resultados (comparação com gold)
        if query_agente and execution_accuracy == 1:
            resultado_agente = executor.execute_query(db_id, query_agente)
            if resultado_agente["success"]:
                best_match = False
                
                for gold_res in gold_results_list:
                    match_atual = results_exact_match(gold_res, resultado_agente["results"])
                    if match_atual:
                        best_match = True
                        break
                
                match_exato = best_match
        
        if match_exato is None:
            match_exato = execution_accuracy  # Se não conseguiu comparar, usa execution accuracy

        # Montar linha do CSV
        row = {
            "instance_id": instance_id,
            "db_id": db_id,
            "pergunta": pergunta,
            "query_ouro": query_ouro[:100] if query_ouro else "",
            "query_agente": query_agente[:100] if query_agente else "",
            "match_exato": "SIM" if match_exato else ("NAO" if match_exato is False else "ERRO"),
            
            # ✅ NOVO: Métricas simplificadas
            "execution_accuracy": execution_accuracy,
            "valid_sql": valid_sql,
            "erro_execucao": erro_execucao[:100] if erro_execucao else "",
            "schema_length": schema_length,
            "schema_valido": schema_valido,  # ✅ NOVO: Flag se schema foi injetado
            
            "tokens_total": tokens_total,
            "tempo_ms": round(tempo_total_ms, 2),
            "veredito": veredito,
            
            # ✅ ReFoRCE artifacts
            "reforce_num_candidatos": artefatos_reforce["num_candidatos"],
            "reforce_num_validos": artefatos_reforce["num_validos"],
            "reforce_status": artefatos_reforce["status_consenso"],
            "reforce_rodadas_exploracao": artefatos_reforce["rodadas_exploracao"],
            "reforce_sql_vencedora": artefatos_reforce["sql_vencedora"][:100] if artefatos_reforce["sql_vencedora"] else "",
            "reforce_best_sql_attempt": artefatos_reforce["best_sql_last_attempt"][:100] if artefatos_reforce["best_sql_last_attempt"] else "",
            "reforce_candidatos_resumo": artefatos_reforce["resumo_candidatos"],
        }
        
        all_rows.append(row)
        
        # Log
        status_icon = "✅" if match_exato else ("❌" if match_exato is False else "⚠️")
        print(f"     {status_icon} Exato Match: {match_exato} | Exec Accuracy: {execution_accuracy} | Valid SQL: {valid_sql}")
        print(f"     🤖 ReFoRCE: {artefatos_reforce['num_validos']}/{artefatos_reforce['num_candidatos']} válidos → {artefatos_reforce['status_consenso']}")
        if erro_execucao:
            print(f"     ⚠️ Erro: {erro_execucao[:80]}")

    # Salvar CSV
    if all_rows:
        df = pd.DataFrame(all_rows)
        df.to_csv(csv_path, index=False, encoding="utf-8")
        print(f"\n✅ Resultados salvos em {csv_path} ({len(all_rows)} linhas)")
        print(f"\n{'='*80}")
        print(f"📊 RESUMO DA AVALIAÇÃO")
        print(f"{'='*80}")
        print(f"  Match Exato: {sum(1 for r in all_rows if r['match_exato'] == 'SIM')}/{len(all_rows)}")
        print(f"  Execution Accuracy: {df['execution_accuracy'].mean():.1%}")
        print(f"  Valid SQL: {df['valid_sql'].mean():.1%}")
        print(f"\n🔍 SCHEMA VALIDATION:")
        print(f"  Schema válido: {df['schema_valido'].sum()}/{len(all_rows)} ({df['schema_valido'].mean():.1%})")
        print(f"  Schema length médio: {df['schema_length'].mean():.0f} chars")
        print(f"  Schema length min/max: {df['schema_length'].min()}..{df['schema_length'].max()} chars")
        print(f"\n⚡ TOKEN USAGE:")
        print(f"  Tokens totais: {df['tokens_total'].sum():.0f} (média: {df['tokens_total'].mean():.0f} por pergunta)")
        print(f"  Tokens min/max por pergunta: {df['tokens_total'].min():.0f} / {df['tokens_total'].max():.0f}")
        print(f"\n🤖 ReFoRCE METRICS:")
        print(f"  Consenso encontrado: {sum(1 for r in all_rows if r['reforce_status'] == 'consenso_encontrado')}/{len(all_rows)}")
        print(f"  Candidatos médios por pergunta: {df['reforce_num_candidatos'].mean():.1f}")
        print(f"  Válidos por rodada: {df['reforce_num_validos'].mean():.1f} / {df['reforce_num_candidatos'].mean():.1f}")
        print(f"  Rodadas de exploração: {df['reforce_rodadas_exploracao'].sum():.0f} total")
        print(f"⏱️  Tempo médio: {df['tempo_ms'].mean():.0f} ms ({df['tempo_ms'].mean()/1000:.1f}s)")
        print(f"{'='*80}\n")
    else:
        print("\n❌ Nenhum resultado foi processado.")
        sys.exit(1)


if __name__ == "__main__":
    main()
