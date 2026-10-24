"""
Chamada ao Jev (TypeSafe System One), usada pelo verificador.

O Jev responde perguntas fechadas sobre um estado (texto) com probabilidades. Quem chama
decide o que fazer quando não há chave ou a chamada falha: o verificador segue só com as regras.
A chave vem de TYPESAFE_API_KEY e só vai no cabeçalho; nunca é impressa nem guardada no estado.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

URL_JEV = "https://api.typesafe.ai/v1/systemone"
MODELO_JEV = "jev-1.13.0"
TIMEOUT_SEGUNDOS = 8.0


def chave_jev() -> str:
    return os.getenv("TYPESAFE_API_KEY", "").strip()


def perguntar(state: dict[str, Any], perguntas: dict[str, dict], chave: str, timeout: float = TIMEOUT_SEGUNDOS) -> dict:
    """Manda o estado e as perguntas ao Jev e devolve o dicionário `answers`."""
    corpo = {"model": MODELO_JEV, "state": state, "questions": perguntas}
    resp = httpx.post(URL_JEV, json=corpo, headers={"Authorization": f"Bearer {chave}"}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["answers"]


def escolha(instrucoes: str, opcoes: dict[str, str]) -> dict:
    return {"type": "choice", "instructions": instrucoes, "criteria": dict(opcoes)}


def media_das_duas_ordens(respostas: dict, nome: str, opcoes: list[str]) -> dict[str, float]:
    """O Jev tende a preferir a primeira opção; a escolha vai nas duas ordens e usamos a média."""
    a = respostas[nome]["probabilities"]
    b = respostas[nome + "_inv"]["probabilities"]
    probs = {o: (float(a.get(o, 0.0)) + float(b.get(o, 0.0))) / 2 for o in opcoes}
    total = sum(probs.values()) or 1.0
    return {o: p / total for o, p in probs.items()}


def _identificador(nome: str) -> str:
    return nome if nome.replace("_", "").isalnum() else f'"{nome}"'


def schema_compacto(contexto_schema: str) -> str:
    """Schema do nó de schema em formato curto: uma linha "tabela: col1, col2" por tabela e as chaves estrangeiras."""
    tabelas: list[tuple[str, list[str]]] = []
    fks: list[str] = []
    for linha in contexto_schema.splitlines():
        if linha.startswith("Tabela: "):
            tabelas.append((linha[len("Tabela: "):].strip(), []))
        elif not tabelas:
            continue
        elif linha.startswith("  - ") and "->" in linha:
            origem, destino = (p.strip() for p in linha[4:].split("->", 1))
            fks.append(f"{_identificador(tabelas[-1][0])}.{_identificador(origem)} -> {destino.split(' (')[0].strip()}")
        elif linha.startswith("- ") and ":" in linha:
            tabelas[-1][1].append(_identificador(linha[2:].rsplit(":", 1)[0].strip()))
    texto = "\n".join(f"{_identificador(t)}: {', '.join(cols)}" for t, cols in tabelas)
    return texto + ("\nForeign keys:\n" + "\n".join(fks) if fks else "")


def separar_dica(pergunta: str) -> tuple[str, str]:
    """No BIRD a dica vem colada na pergunta ("...\\nHint: ..."); o Jev recebe as duas separadas."""
    for marcador in ("\nHint:", "\nhint:"):
        if marcador in pergunta:
            texto, dica = pergunta.split(marcador, 1)
            return texto.strip(), dica.strip()
    return pergunta.strip(), ""
