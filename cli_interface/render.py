"""Apresentacao (rich) do dict normalizado que vem do io_adapter.

So desenha a partir do contrato publico do adapter
(`status, answer, data, chart, csv, message`). Nao reusa a apresentacao da CLI
antiga (`exibir_resultado_console`), que e baseada em `tabulate`/`print`.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Quantas linhas da tabela mostrar no terminal (o conjunto completo vai no CSV).
MAX_LINHAS_TABELA = 20


def result(console: Console, saida: dict[str, Any]) -> None:
    status = saida.get("status")

    if status == "error":
        console.print(
            Panel(saida.get("message") or "Erro durante a execucao.", title="erro", border_style="red")
        )
        return
    if status == "blocked":
        console.print(
            Panel(saida.get("message") or "Fluxo bloqueado.", title="bloqueado", border_style="yellow")
        )
        return
    if status == "awaiting":
        # No fluxo sincrono o HITL resolve dentro do ask(); isto e so um fallback.
        console.print(
            Panel(saida.get("message") or "Aguardando input.", title="aguardando", border_style="cyan")
        )
        return

    # status == "success"
    answer = saida.get("answer")
    if answer:
        console.print(Panel(Text(str(answer)), title="t2i", border_style="green"))

    data = saida.get("data") or {}
    rows = data.get("rows") or []
    total = int(data.get("total") or 0)
    if rows:
        _print_table(console, rows, total)

    rodape: list[str] = []
    csv_path = (saida.get("csv") or {}).get("path")
    if csv_path:
        rodape.append(f"csv: {csv_path}")
    chart = saida.get("chart") or {}
    if chart.get("generated") and chart.get("path"):
        rodape.append(f"grafico: {chart['path']}")
    if rodape:
        console.print(Text("   ".join(rodape), style="dim"))


def _print_table(console: Console, rows: list[Any], total: int) -> None:
    primeira = rows[0]
    colunas = list(primeira.keys()) if isinstance(primeira, dict) else None

    table = Table(show_header=bool(colunas), header_style="bold cyan")
    visiveis = rows[:MAX_LINHAS_TABELA]

    if colunas:
        for c in colunas:
            table.add_column(str(c))
        for r in visiveis:
            table.add_row(*[_fmt(r.get(c)) for c in colunas])
    else:
        n = max((len(r) for r in visiveis if isinstance(r, (list, tuple))), default=1)
        for _ in range(n):
            table.add_column()
        for r in visiveis:
            if isinstance(r, (list, tuple)):
                table.add_row(*[_fmt(x) for x in r])
            else:
                table.add_row(_fmt(r))

    console.print(table)
    if total > len(visiveis):
        console.print(
            Text(
                f"... mostrando {len(visiveis)} de {total} linhas (conjunto completo no CSV).",
                style="dim",
            )
        )


def _fmt(v: Any) -> str:
    return "" if v is None else str(v)
