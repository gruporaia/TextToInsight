#!/usr/bin/env python3
"""CLI do pacote Text-to-Insight."""

from __future__ import annotations

import argparse
import os
from typing import Any

from dotenv import load_dotenv

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from text_to_insight.InsightEngine import InsightEngine
from text_to_insight.runtime import exibir_resultado_console

load_dotenv()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Executa o pipeline Text-to-Insight para responder perguntas sobre o banco SQLite."
    )
    parser.add_argument(
        "--hitl",
        choices=["on", "off"],
        default="on",
        help="Ativa/desativa o modo Human-in-the-Loop. Padrão: on.",
    )
    parser.add_argument(
        "--enrich-rag",
        choices=["on", "off"],
        default="off",
        help="Ativa/desativa o enriquecimento RAG. Padrão: off.",
    )
    parser.add_argument(
        "--infer-fks",
        choices=["on", "off"],
        default="off",
        help="Ativa/desativa a inferência virtual de chaves estrangeiras. Padrão: off.",
    )
    parser.add_argument(
        "--use-schemacrawler",
        choices=["on", "off"],
        default="on",
        help="Ativa/desativa o uso do SchemaCrawler. Se off, usa PRAGMA SQLite. Padrão: on.",
    )
    parser.add_argument(
        "--thread-id",
        default="sessao_usuario_1",
        help="Identificador da thread para execução e retomada.",
    )
    parser.add_argument(
        "--db-path",
        default="data/olist_relational.db",
        help="Caminho para o banco SQLite.",
    )
    parser.add_argument(
        "--db-dialeto",
        choices=["sqlite", "postgresql", "mysql"],
        default="",
        help=(
            "Dialeto do banco: sqlite, postgresql ou mysql. Se omitido, e detectado "
            "automaticamente pela extensao de --db-path (.duckdb, .sqlite, etc), "
            "com sqlite como fallback."
        ),
    )
    parser.add_argument(
        "--db-host",
        default=None,
        help="Host do banco (obrigatório se --db-dialeto != sqlite).",
    )
    parser.add_argument(
        "--db-port",
        type=int,
        default=None,
        help="Porta do banco (padrão: 5432 para postgresql, 3306 para mysql).",
    )
    parser.add_argument(
        "--db-name",
        default=None,
        help="Nome do banco de dados remoto.",
    )
    parser.add_argument(
        "--db-user",
        default=None,
        help="Usuário do banco remoto.",
    )
    parser.add_argument(
        "--db-password",
        default=os.getenv("DB_PASSWORD"),
        help=(
            "Senha do banco remoto. EVITE usar esta flag diretamente: qualquer "
            "argumento de linha de comando fica visivel no historico do shell e "
            "na lista de processos do sistema. Prefira definir a variavel de "
            "ambiente DB_PASSWORD (ex: no .env)."
        ),
    )
    parser.add_argument(
        "--db-url",
        default=os.getenv("DATABASE_URL"),
        help=(
            "URL unica de conexao (ex: postgresql://usuario:senha@host:5432/banco). "
            "Se informado, o dialeto e detectado automaticamente pelo SQLAlchemy e as "
            "demais flags --db-dialeto/--db-host/--db-port/--db-name/--db-user/--db-password "
            "sao ignoradas. Tambem pode vir da variavel de ambiente DATABASE_URL."
        ),
    )
    parser.add_argument(
        "--model",
        default="gpt-5-mini",
        help="Modelo LLM a utilizar (ex: gemini-2.5-flash, gpt-5-nano).",
    )
    parser.add_argument(
        "--api-key-env",
        default="OPENAI_API_KEY",
        help="Nome da variável de ambiente com a chave de API.",
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
        "pergunta",
        nargs="*",
        help="Pergunta em linguagem natural. Se omitida, usa uma pergunta padrão.",
    )
    return parser.parse_args(argv)


def _coletar_resposta_humana(pergunta_agente: str) -> str:
    print(f"\n[HITL]: {pergunta_agente}")
    return input("[RESPOSTA USUARIO]: ")


def main(argv: list[str] | None = None) -> dict[str, Any]:
    args = _parse_args(argv)

    if args.pergunta:
        pergunta = " ".join(args.pergunta)
    else:
        pergunta = "Quantos pedidos existem no banco?"
        print(f"Nenhuma pergunta fornecida. Usando exemplo: '{pergunta}'\n")

    hitl_ativado = args.hitl == "on"
    cot_ativado = args.cot == "on"
    data_exploration_ativado = args.data_exploration == "on"
    exploration_selector_mode = args.exploration_selector
    print(f"[CONFIG] HITL: {'ATIVADO' if hitl_ativado else 'DESATIVADO'}")
    enrich_rag_ativado = args.enrich_rag == "on"
    inferir_fks_ativado = args.infer_fks == "on"
    use_schemacrawler_ativado = args.use_schemacrawler == "on"

    api_key = os.getenv(args.api_key_env)
    if not api_key:
        raise RuntimeError(
            f"Variável de ambiente '{args.api_key_env}' não encontrada. "
            "Configure a chave da API antes de executar."
        )
    db_config = None
    if args.db_dialeto in ("postgresql", "mysql"):
        db_config = {
            "host": args.db_host,
            "port": args.db_port,
            "database": args.db_name,
            "user": args.db_user,
            "password": args.db_password,
        }

    # Se --db-url foi informado, o CLI constrói e é dono da Engine SQLAlchemy
    # (inclusive do dispose() no final) e a repassa pronta para a InsightEngine,
    # em vez de deixar a credencial/URL crua viajar dentro do estado do grafo.
    db_engine = None
    if args.db_url:
        from sqlalchemy import create_engine

        from text_to_insight.nodes.code_agent.code_sql import _normalizar_db_url

        db_engine = create_engine(_normalizar_db_url(args.db_url))

    engine = InsightEngine(
        api_key=api_key,
        model=args.model,
        db_path=args.db_path,
        db_dialeto=args.db_dialeto,
        db_config=db_config,
        db_engine=db_engine,
        hitl=hitl_ativado,
        enrich_rag=enrich_rag_ativado,
        inferir_fks_virtuais=inferir_fks_ativado,
        usar_schemacrawler=use_schemacrawler_ativado,
        show_output=False,
        use_cot=cot_ativado,
        use_data_exploration=data_exploration_ativado,
        use_exploration_selector=exploration_selector_mode,
    )

    # show_output=False para evitar prints duplicados no console, já que exibir_resultado_console é chamado manualmente.

    try:
        callback = _coletar_resposta_humana if hitl_ativado else None
        resultado = engine.run(thread_id=args.thread_id, query=pergunta, on_human_prompt=callback)

        # Fallback (plano B) para clientes que prefiram retomar manualmente sem callback.
        while resultado.get("status") == "AWAITING_USER":
            resposta = _coletar_resposta_humana(resultado.get("message", "Pode confirmar o prosseguimento?"))
            resultado = engine.resume(
                thread_id=args.thread_id,
                user_response=resposta,
                on_human_prompt=callback,
            )

        exibir_resultado_console(resultado)
        return resultado
    finally:
        # Quem criou a engine (o CLI, neste caso) é quem a descarta.
        if db_engine is not None:
            db_engine.dispose()


if __name__ == "__main__":
    main()
