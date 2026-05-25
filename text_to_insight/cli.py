#!/usr/bin/env python3
"""CLI do pacote Text-to-Insight."""

from __future__ import annotations

import argparse
import os
from typing import Any

from dotenv import load_dotenv

from .InsightEngine import InsightEngine
from .runtime import exibir_resultado_console

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
        "--model",
        default="gemini-2.5-flash",
        help="Modelo LLM a utilizar (ex: gemini-2.5-flash, gpt-5-nano).",
    )
    parser.add_argument(
        "--api-key-env",
        default="GOOGLE_API_KEY",
        help="Nome da variável de ambiente com a chave de API.",
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
    enrich_rag_ativado = args.enrich_rag == "on"

    api_key = os.getenv(args.api_key_env)
    if not api_key:
        raise RuntimeError(
            f"Variável de ambiente '{args.api_key_env}' não encontrada. "
            "Configure a chave da API antes de executar."
        )

    engine = InsightEngine(
        api_key=api_key,
        model=args.model,
        db_path=args.db_path,
        hitl=hitl_ativado,
        enrich_rag=enrich_rag_ativado,
        show_output=False,
    )

    # show_output=False para evitar prints duplicados no console, já que exibir_resultado_console é chamado manualmente.

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


if __name__ == "__main__":
    main()
