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


def _float_nao_negativo(valor: str) -> float:
    numero = float(valor)
    if numero < 0:
        raise argparse.ArgumentTypeError(f"deve ser >= 0, recebido {valor}")
    return numero


def _int_positivo(valor: str) -> int:
    numero = int(valor)
    if numero <= 0:
        raise argparse.ArgumentTypeError(f"deve ser > 0, recebido {valor}")
    return numero


def _montar_model_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """Monta os parâmetros do LLM, omitindo os não informados (usa o padrão do provedor)."""
    candidatos = {"temperature": args.temperature, "max_tokens": args.max_tokens}
    return {chave: valor for chave, valor in candidatos.items() if valor is not None}


def _normalizar_db_url(db_url: str) -> str:
    """
    "postgresql://" e "mysql://" sem driver explícito usam os drivers dos
    extras do pacote (psycopg2 e pymysql). Sem isso, o SQLAlchemy tenta o
    driver padrão dele (ex: psycopg v3), que pode não estar instalado.
    """
    if db_url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + db_url[len("postgresql://"):]
    if db_url.startswith("mysql://"):
        return "mysql+pymysql://" + db_url[len("mysql://"):]
    return db_url


def _criar_engine(db_path: str | None, db_url: str | None, timeout_segundos: int = 15):
    """
    Cria a Engine SQLAlchemy que o CLI passa para a InsightEngine.

    A biblioteca não abre conexões; quem abre é o chamador, e aqui o
    chamador é o CLI. `--db-url` tem prioridade sobre `--db-path`. Um
    arquivo SQLite é aberto em modo somente leitura.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    if db_url:
        dialeto = make_url(db_url).get_backend_name()
        connect_args: dict[str, Any] = {}
        if dialeto == "postgresql":
            connect_args = {"connect_timeout": timeout_segundos}
        elif dialeto == "mysql":
            connect_args = {"connect_timeout": timeout_segundos, "read_timeout": timeout_segundos}
        return create_engine(_normalizar_db_url(db_url), connect_args=connect_args)

    if not db_path:
        raise SystemExit("Informe o banco com --db-path (arquivo SQLite) ou --db-url / DATABASE_URL.")
    caminho = Path(db_path).expanduser().resolve()
    if not caminho.exists():
        raise SystemExit(f"Arquivo de banco não encontrado: {caminho}")
    return create_engine(f"sqlite:///file:{caminho}?mode=ro&uri=true")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Executa o pipeline Text-to-Insight para responder perguntas sobre um banco de dados."
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
        "--thread-id",
        default="sessao_usuario_1",
        help="Identificador da thread para execução e retomada.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Caminho para um arquivo SQLite (aberto somente leitura).",
    )
    parser.add_argument(
        "--db-url",
        default=os.getenv("DATABASE_URL"),
        help=(
            "URL unica de conexao (ex: postgresql://usuario:senha@host:5432/banco). "
            "Tem prioridade sobre --db-path. Tambem pode vir da variavel de "
            "ambiente DATABASE_URL. PostgreSQL e MySQL exigem os extras "
            "text-to-insight[postgres] / text-to-insight[mysql]."
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
        "--temperature",
        type=_float_nao_negativo,
        default=None,
        help="Temperatura do LLM (ex: 0 para respostas determinísticas). Padrão: padrão do provedor.",
    )
    parser.add_argument(
        "--max-tokens",
        type=_int_positivo,
        default=None,
        help="Limite de tokens gerados por resposta do LLM. Padrão: padrão do provedor.",
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

    api_key = os.getenv(args.api_key_env)
    if not api_key:
        raise RuntimeError(
            f"Variável de ambiente '{args.api_key_env}' não encontrada. "
            "Configure a chave da API antes de executar."
        )

    # O CLI constrói e é dono da Engine SQLAlchemy (inclusive do dispose() no
    # final) e a repassa pronta para a InsightEngine.
    db_engine = _criar_engine(args.db_path, args.db_url)
    engine = InsightEngine(
        api_key=api_key,
        model=args.model,
        db_engine=db_engine,
        hitl=hitl_ativado,
        enrich_rag=enrich_rag_ativado,
        inferir_fks_virtuais=inferir_fks_ativado,
        show_output=False,
        use_cot=cot_ativado,
        use_data_exploration=data_exploration_ativado,
        use_exploration_selector=exploration_selector_mode,
        model_kwargs=_montar_model_kwargs(args),
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
        db_engine.dispose()


if __name__ == "__main__":
    main()
