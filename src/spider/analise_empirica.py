"""
Módulo de Análise Empírica para avaliação Spider / Spider 2.0 Lite.

Contém funções de pós-processamento para gerar:
1. Distribuição de tentativas e ablação do Crítico
2. Matriz de confusão do Crítico
3. Taxonomia de erros SQL
4. Tabela de métricas operacionais
5. Estatísticas do Agente de Visualização
"""

import csv
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .metrics import normalize_sql


# ---------------------------------------------------------------------------
# 1. Distribuição de tentativas e ablação do Crítico
# ---------------------------------------------------------------------------

def calcular_distribuicao_tentativas(all_rows: list[dict]) -> dict[str, Any]:
    """
    Para cada pergunta, identifica em qual tentativa o Crítico aprovou.
    Retorna tabela de frequência e dados para ablação.
    """
    freq = {"1a_tentativa": 0, "2a_tentativa": 0, "3a_tentativa": 0, "falha": 0}

    for r in all_rows:
        tent = r.get("tentativa_numero", 1)
        veredito = r.get("veredito_critico", "")
        try:
            tent = int(tent)
        except (ValueError, TypeError):
            tent = 1

        if veredito == "aprovado":
            if tent == 1:
                freq["1a_tentativa"] += 1
            elif tent == 2:
                freq["2a_tentativa"] += 1
            elif tent >= 3:
                freq["3a_tentativa"] += 1
            else:
                freq["falha"] += 1
        else:
            freq["falha"] += 1

    return freq


def calcular_ablacao_critico(all_rows: list[dict]) -> dict[str, float]:
    """
    Calcula exact match COM e SEM o mecanismo de autocorreção (Crítico).

    - COM Crítico: exact match final (como já calculado).
    - SEM Crítico: exact match considerando APENAS o resultado da 1ª tentativa
      (campo `resultado_exato_match_1a_tentativa`).
    """
    total = len(all_rows) if all_rows else 1

    em_com_critico = sum(
        1 for r in all_rows if r.get("resultado_exato_match") is True
    )
    em_sem_critico = sum(
        1 for r in all_rows if r.get("resultado_exato_match_1a_tentativa") is True
    )

    f1_com_critico = sum(
        float(r.get("resultado_f1", 0.0) or 0.0) for r in all_rows
    )
    f1_sem_critico = sum(
        float(r.get("resultado_f1_1a_tentativa", 0.0) or 0.0) for r in all_rows
    )

    return {
        "exact_match_com_critico": em_com_critico / total,
        "exact_match_sem_critico": em_sem_critico / total,
        "f1_com_critico": f1_com_critico / total,
        "f1_sem_critico": f1_sem_critico / total,
        "total_perguntas": total,
        "acertos_com_critico": em_com_critico,
        "acertos_sem_critico": em_sem_critico,
    }


def gerar_grafico_ablacao(ablacao: dict, output_path: str, dataset_label: str = "Spider") -> str:
    """Gera gráfico de barras comparando exact match com e sem Crítico."""
    labels = ["Com Crítico\n(autocorreção)", "Sem Crítico\n(1ª tentativa)"]
    valores = [
        ablacao["exact_match_com_critico"] * 100,
        ablacao["exact_match_sem_critico"] * 100,
    ]
    cores = ["#2ecc71", "#e74c3c"]

    fig, ax = plt.subplots(figsize=(7, 5))
    bars = ax.bar(labels, valores, color=cores, width=0.5, edgecolor="white", linewidth=1.5)

    for bar, val in zip(bars, valores):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                f"{val:.1f}%", ha="center", va="bottom", fontweight="bold", fontsize=13)

    ax.set_ylabel("Exact Match (%)", fontsize=12)
    ax.set_title(f"Ablação do Crítico — {dataset_label}", fontsize=14, fontweight="bold")
    ax.set_ylim(0, max(valores) * 1.25 if max(valores) > 0 else 100)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    return output_path


def gerar_grafico_distribuicao_tentativas(freq: dict, output_path: str, dataset_label: str = "Spider") -> str:
    """Gera gráfico de barras com a distribuição de tentativas."""
    labels = ["1ª tentativa", "2ª tentativa", "3ª tentativa", "Falha (3 tent.)"]
    valores = [freq["1a_tentativa"], freq["2a_tentativa"], freq["3a_tentativa"], freq["falha"]]
    cores = ["#27ae60", "#f39c12", "#e67e22", "#e74c3c"]

    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(labels, valores, color=cores, width=0.55, edgecolor="white", linewidth=1.5)

    for bar, val in zip(bars, valores):
        if val > 0:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3,
                    str(val), ha="center", va="bottom", fontweight="bold", fontsize=12)

    ax.set_ylabel("Número de Perguntas", fontsize=12)
    ax.set_title(f"Distribuição de Tentativas até Aprovação — {dataset_label}", fontsize=13, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    return output_path


# ---------------------------------------------------------------------------
# 1.5 Transições de Autocorreção
# ---------------------------------------------------------------------------

def calcular_transicoes_autocorrecao(all_rows: list[dict]) -> dict[str, int]:
    """
    Analisa as transições de Exact Match da 1ª tentativa para a final,
    apenas para perguntas que tiveram > 1 tentativa.
    """
    transicoes = {
        "ajudou": 0,    # False -> True
        "manteve_certo": 0, # True -> True
        "manteve_errado": 0, # False -> False
        "atrapalhou": 0 # True -> False
    }

    for r in all_rows:
        tent = r.get("tentativa_numero", 1)
        try:
            tent = int(tent)
        except (ValueError, TypeError):
            tent = 1
            
        if tent > 1:
            em_1a = r.get("resultado_exato_match_1a_tentativa") is True
            em_final = r.get("resultado_exato_match") is True
            
            if not em_1a and em_final:
                transicoes["ajudou"] += 1
            elif em_1a and em_final:
                transicoes["manteve_certo"] += 1
            elif not em_1a and not em_final:
                transicoes["manteve_errado"] += 1
            elif em_1a and not em_final:
                transicoes["atrapalhou"] += 1

    return transicoes


def exportar_detalhes_transicoes(all_rows: list[dict], output_csv: str) -> int:
    """
    Exporta os dados de TODAS as queries que passaram por autocorreção (>1 tentativa),
    classificando o tipo de transição.
    """
    detalhes = []
    for r in all_rows:
        tent = r.get("tentativa_numero", 1)
        try:
            tent = int(tent)
        except (ValueError, TypeError):
            tent = 1
            
        if tent > 1:
            em_1a = r.get("resultado_exato_match_1a_tentativa") is True
            em_final = r.get("resultado_exato_match") is True
            
            tipo_transicao = ""
            if not em_1a and em_final:
                tipo_transicao = "EM=False -> EM=True (Ajudou)"
            elif em_1a and em_final:
                tipo_transicao = "EM=True -> EM=True (Manteve Certo)"
            elif not em_1a and not em_final:
                tipo_transicao = "EM=False -> EM=False (Manteve Errado)"
            elif em_1a and not em_final:
                tipo_transicao = "EM=True -> EM=False (Atrapalhou)"
                
            detalhes.append({
                "id_exemplo": r.get("id_exemplo", ""),
                "db_id": r.get("db_id", ""),
                "tipo_transicao": tipo_transicao,
                "pergunta": r.get("pergunta_usuario", ""),
                "query_ouro": r.get("query_ouro_spider", ""),
                "query_1a_tentativa": r.get("query_1a_tentativa", ""),
                "query_final": r.get("query_agente_tentativa", ""),
                "feedback_critico": r.get("feedback_critico_recebido", ""),
                "f1_1a_tentativa": r.get("resultado_f1_1a_tentativa", 0),
                "f1_final": r.get("resultado_f1", 0),
                "tentativas": tent
            })

    if detalhes:
        Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
        with open(output_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=detalhes[0].keys())
            writer.writeheader()
            writer.writerows(detalhes)

    return len(detalhes)


# ---------------------------------------------------------------------------
# 2. Matriz de confusão do Crítico
# ---------------------------------------------------------------------------

def calcular_matriz_confusao(all_rows: list[dict]) -> dict[str, int]:
    """
    Cruza veredito do Crítico com exact match real.
    Retorna TP, FP, FN, TN.
    """
    tp = fp = fn = tn = 0
    for r in all_rows:
        aprovado = r.get("veredito_critico") == "aprovado"
        match = r.get("resultado_exato_match") is True

        if aprovado and match:
            tp += 1
        elif aprovado and not match:
            fp += 1
        elif not aprovado and match:
            fn += 1
        else:
            tn += 1

    return {"TP": tp, "FP": fp, "FN": fn, "TN": tn}


def exportar_falsos_positivos(all_rows: list[dict], output_csv: str) -> int:
    """
    Exporta os casos de falso positivo (Crítico aprovou, mas exact match incorreto).
    Retorna a quantidade de falsos positivos.
    """
    fps = []
    for r in all_rows:
        aprovado = r.get("veredito_critico") == "aprovado"
        match = r.get("resultado_exato_match")
        if aprovado and match is not True:
            fps.append({
                "id_exemplo": r.get("id_exemplo", ""),
                "db_id": r.get("db_id", ""),
                "pergunta": r.get("pergunta_usuario", ""),
                "query_ouro": r.get("query_ouro_spider", ""),
                "query_agente": r.get("query_agente_tentativa", ""),
                "veredito_critico": r.get("veredito_critico", ""),
                "feedback_critico": r.get("feedback_critico_recebido", ""),
                "resultado_exato_match": match,
                "f1": r.get("resultado_f1", 0),
            })

    if fps:
        Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
        with open(output_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fps[0].keys())
            writer.writeheader()
            writer.writerows(fps)

    return len(fps)


# ---------------------------------------------------------------------------
# 3. Taxonomia de erros
# ---------------------------------------------------------------------------

_CATEGORIAS_ERRO = [
    ("DISTINCT", r"\bDISTINCT\b"),
    ("GROUP BY", r"\bGROUP\s+BY\b"),
    ("ORDER BY", r"\bORDER\s+BY\b"),
    ("LIMIT", r"\bLIMIT\b"),
    ("HAVING", r"\bHAVING\b"),
    ("Subconsulta", r"\(\s*SELECT\b"),
    ("JOIN", r"\bJOIN\b"),
    ("WHERE", r"\bWHERE\b"),
    ("Agregação (SUM/AVG/COUNT/MIN/MAX)", r"\b(SUM|AVG|COUNT|MIN|MAX)\s*\("),
    ("UNION", r"\bUNION\b"),
]


def _detectar_divergencias(sql_ouro: str, sql_agente: str) -> list[str]:
    """Detecta categorias de divergência entre SQL ouro e SQL agente."""
    ouro_norm = normalize_sql(sql_ouro)
    agente_norm = normalize_sql(sql_agente)

    divergencias = []
    for nome, padrao in _CATEGORIAS_ERRO:
        ouro_tem = bool(re.search(padrao, ouro_norm, re.IGNORECASE))
        agente_tem = bool(re.search(padrao, agente_norm, re.IGNORECASE))
        if ouro_tem != agente_tem:
            divergencias.append(nome)

    # Checar diferença nas colunas do SELECT
    def _extrair_colunas_select(sql_norm: str) -> set[str]:
        m = re.match(r"SELECT\s+(.*?)\s+FROM\b", sql_norm, re.IGNORECASE | re.DOTALL)
        if m:
            cols = m.group(1).split(",")
            return {c.strip() for c in cols}
        return set()

    cols_ouro = _extrair_colunas_select(ouro_norm)
    cols_agente = _extrair_colunas_select(agente_norm)
    if cols_ouro and cols_agente and cols_ouro != cols_agente:
        divergencias.append("Colunas SELECT diferentes")

    if not divergencias:
        divergencias.append("Outro (valores/lógica)")

    return divergencias


def classificar_erros(all_rows: list[dict]) -> tuple[list[dict], Counter]:
    """
    Para as perguntas sem exact match, classifica o tipo de erro.
    Retorna lista de registros detalhados e Counter por categoria.
    """
    erros_detalhados = []
    contagem = Counter()

    for r in all_rows:
        if r.get("resultado_exato_match") is True:
            continue
        sql_ouro = r.get("query_ouro_spider", "")
        sql_agente = r.get("query_agente_tentativa", "")

        if not sql_ouro or not sql_agente:
            categorias = ["Sem SQL (ouro ou agente)"]
        else:
            categorias = _detectar_divergencias(sql_ouro, sql_agente)

        for cat in categorias:
            contagem[cat] += 1

        erros_detalhados.append({
            "id_exemplo": r.get("id_exemplo", ""),
            "db_id": r.get("db_id", ""),
            "pergunta": r.get("pergunta_usuario", ""),
            "query_ouro": sql_ouro,
            "query_agente": sql_agente,
            "categorias_erro": "; ".join(categorias),
        })

    return erros_detalhados, contagem


def exportar_taxonomia_erros_csv(erros_detalhados: list[dict], output_csv: str) -> None:
    """Exporta erros detalhados em CSV."""
    if not erros_detalhados:
        return
    Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=erros_detalhados[0].keys())
        writer.writeheader()
        writer.writerows(erros_detalhados)


def gerar_grafico_taxonomia_erros(contagem: Counter, output_path: str, dataset_label: str = "Spider") -> str:
    """Gera gráfico de barras horizontais com contagem por categoria de erro."""
    if not contagem:
        return ""

    cats = contagem.most_common()
    labels = [c[0] for c in cats]
    valores = [c[1] for c in cats]

    fig, ax = plt.subplots(figsize=(9, max(4, len(labels) * 0.55)))
    cores = plt.cm.RdYlGn_r([i / max(len(labels), 1) for i in range(len(labels))])
    bars = ax.barh(labels[::-1], valores[::-1], color=cores[::-1], edgecolor="white", linewidth=1.2)

    for bar, val in zip(bars, valores[::-1]):
        ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                str(val), va="center", fontweight="bold", fontsize=11)

    ax.set_xlabel("Contagem", fontsize=12)
    ax.set_title(f"Taxonomia de Erros SQL — {dataset_label}", fontsize=13, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    return output_path


# ---------------------------------------------------------------------------
# 4. Tabela de métricas operacionais
# ---------------------------------------------------------------------------

def calcular_metricas_operacionais(all_rows: list[dict]) -> dict[str, Any]:
    """
    Calcula métricas operacionais por pergunta e segmenta por
    resolvidas na 1ª tentativa vs múltiplas tentativas.
    """
    grupo_1a = []  # resolvidas na 1ª tentativa
    grupo_multi = []  # 2+ tentativas

    for r in all_rows:
        tent = r.get("tentativa_numero", 1)
        try:
            tent = int(tent)
        except (ValueError, TypeError):
            tent = 1

        dados = {
            "tokens_input": int(r.get("tokens_input", 0) or 0),
            "tokens_output": int(r.get("tokens_output", 0) or 0),
            "tokens_total": int(r.get("tokens_total", 0) or 0),
            "tempo_ms": float(r.get("tempo_agente_ms", 0) or 0),
        }

        if tent <= 1:
            grupo_1a.append(dados)
        else:
            grupo_multi.append(dados)

    def _media(grupo: list[dict], chave: str) -> float:
        vals = [g[chave] for g in grupo]
        return sum(vals) / len(vals) if vals else 0.0

    def _soma(grupo: list[dict], chave: str) -> int:
        return sum(g[chave] for g in grupo)

    return {
        "geral": {
            "tokens_input_medio": _media(grupo_1a + grupo_multi, "tokens_input"),
            "tokens_output_medio": _media(grupo_1a + grupo_multi, "tokens_output"),
            "tokens_total_soma": _soma(grupo_1a + grupo_multi, "tokens_total"),
            "tempo_medio_ms": _media(grupo_1a + grupo_multi, "tempo_ms"),
            "n": len(grupo_1a + grupo_multi),
        },
        "1a_tentativa": {
            "tokens_input_medio": _media(grupo_1a, "tokens_input"),
            "tokens_output_medio": _media(grupo_1a, "tokens_output"),
            "tokens_total_soma": _soma(grupo_1a, "tokens_total"),
            "tempo_medio_ms": _media(grupo_1a, "tempo_ms"),
            "n": len(grupo_1a),
        },
        "multiplas_tentativas": {
            "tokens_input_medio": _media(grupo_multi, "tokens_input"),
            "tokens_output_medio": _media(grupo_multi, "tokens_output"),
            "tokens_total_soma": _soma(grupo_multi, "tokens_total"),
            "tempo_medio_ms": _media(grupo_multi, "tempo_ms"),
            "n": len(grupo_multi),
        },
    }


# ---------------------------------------------------------------------------
# 5. Estatísticas do Agente de Visualização
# ---------------------------------------------------------------------------

def calcular_estatisticas_visualizacao(all_rows: list[dict]) -> dict[str, int]:
    """Contabiliza quantas queries acionaram o agente de visualização."""
    total = len(all_rows)
    acionaram = sum(1 for r in all_rows if r.get("viz_acionado") is True)
    sucesso = sum(1 for r in all_rows if r.get("viz_sucesso") is True)
    falha = acionaram - sucesso

    return {
        "total_queries": total,
        "acionaram_agente": acionaram,
        "graficos_sucesso": sucesso,
        "graficos_falha": falha,
    }


# ---------------------------------------------------------------------------
# 6. Relatório consolidado em Markdown
# ---------------------------------------------------------------------------

def gerar_secao_distribuicao_tentativas(freq: dict, ablacao: dict, grafico_dist_path: str, grafico_abl_path: str) -> list[str]:
    """Gera seção do relatório com distribuição de tentativas e ablação."""
    lines = []
    lines.append("## 1. Distribuição de Tentativas e Ablação do Crítico")
    lines.append("")
    lines.append("### Tabela de Frequência")
    lines.append("")
    lines.append("| Tentativa | Quantidade |")
    lines.append("|-----------|-----------|")
    lines.append(f"| 1ª tentativa | {freq['1a_tentativa']} |")
    lines.append(f"| 2ª tentativa | {freq['2a_tentativa']} |")
    lines.append(f"| 3ª tentativa | {freq['3a_tentativa']} |")
    lines.append(f"| Falha (todas as 3) | {freq['falha']} |")
    lines.append("")
    lines.append("### Ablação do Crítico")
    lines.append("")
    lines.append("| Configuração | Exact Match | Taxa EM | F1 Médio |")
    lines.append("|-------------|-------------|---------|----------|")
    lines.append(f"| Com Crítico (autocorreção) | {ablacao['acertos_com_critico']}/{ablacao['total_perguntas']} | {ablacao['exact_match_com_critico']:.1%} | {ablacao['f1_com_critico']:.4f} |")
    lines.append(f"| Sem Crítico (1ª tentativa) | {ablacao['acertos_sem_critico']}/{ablacao['total_perguntas']} | {ablacao['exact_match_sem_critico']:.1%} | {ablacao['f1_sem_critico']:.4f} |")
    lines.append("")
    if grafico_dist_path:
        lines.append(f"![Distribuição de Tentativas]({grafico_dist_path})")
        lines.append("")
    if grafico_abl_path:
        lines.append(f"![Ablação do Crítico]({grafico_abl_path})")
        lines.append("")
    return lines


def gerar_secao_transicoes(transicoes: dict, n_total_transicoes: int, csv_path: str) -> list[str]:
    """Gera seção do relatório com a tabela de transições de autocorreção."""
    lines = []
    lines.append("## 1.5. Transições de Autocorreção (>1 tentativa)")
    lines.append("")
    total_transicoes = sum(transicoes.values())
    
    if total_transicoes == 0:
        lines.append("Nenhuma pergunta precisou de autocorreção (todas resolvidas na 1ª tentativa).")
        lines.append("")
        return lines

    lines.append("| Transição | Quantidade | Significado |")
    lines.append("|-----------|------------|-------------|")
    lines.append(f"| EM=False → EM=True | {transicoes['ajudou']} | Autocorreção ajudou |")
    lines.append(f"| EM=True → EM=True | {transicoes['manteve_certo']} | Já era certo, continuou certo |")
    lines.append(f"| EM=False → EM=False | {transicoes['manteve_errado']} | Já era errado, continuou errado |")
    lines.append(f"| EM=True → EM=False | {transicoes['atrapalhou']} | Autocorreção atrapalhou |")
    lines.append("")
    
    if n_total_transicoes > 0:
        lines.append(f"> Detalhes completos (Queries, Feedbacks e F1) de todas as **{n_total_transicoes} transições** exportados em: `{csv_path}`")
        lines.append("")
        
    return lines


def gerar_secao_matriz_confusao(mc: dict, n_fps: int, fps_csv: str) -> list[str]:
    """Gera seção do relatório com matriz de confusão."""
    lines = []
    lines.append("## 2. Matriz de Confusão do Crítico")
    lines.append("")
    lines.append("|  | Exact Match Correto | Exact Match Incorreto |")
    lines.append("|--|--------------------|-----------------------|")
    lines.append(f"| **Crítico Aprovou** | TP = {mc['TP']} | FP = {mc['FP']} |")
    lines.append(f"| **Crítico Reprovou** | FN = {mc['FN']} | TN = {mc['TN']} |")
    lines.append("")
    total = mc["TP"] + mc["FP"] + mc["FN"] + mc["TN"]
    if total > 0:
        acc = (mc["TP"] + mc["TN"]) / total
        lines.append(f"**Acurácia do Crítico:** {acc:.1%}")
        lines.append("")
    if n_fps > 0:
        lines.append(f"> **{n_fps} falso(s) positivo(s)** exportados para análise qualitativa: `{fps_csv}`")
        lines.append("")
    return lines


def gerar_secao_taxonomia_erros(contagem: Counter, n_erros: int, erros_csv: str, grafico_path: str) -> list[str]:
    """Gera seção do relatório com taxonomia de erros."""
    lines = []
    lines.append("## 3. Taxonomia de Erros SQL")
    lines.append("")
    lines.append(f"Total de perguntas sem exact match: **{n_erros}**")
    lines.append("")
    if contagem:
        lines.append("| Categoria de Divergência | Contagem |")
        lines.append("|--------------------------|----------|")
        for cat, cnt in contagem.most_common():
            lines.append(f"| {cat} | {cnt} |")
        lines.append("")
    if erros_csv:
        lines.append(f"> Detalhes exportados em: `{erros_csv}`")
        lines.append("")
    if grafico_path:
        lines.append(f"![Taxonomia de Erros]({grafico_path})")
        lines.append("")
    return lines


def gerar_secao_metricas_operacionais(metricas: dict) -> list[str]:
    """Gera seção do relatório com métricas operacionais."""
    lines = []
    lines.append("## 4. Métricas Operacionais")
    lines.append("")
    lines.append("| Métrica | Geral | 1ª Tentativa | 2+ Tentativas |")
    lines.append("|---------|-------|--------------|---------------|")

    g = metricas["geral"]
    t1 = metricas["1a_tentativa"]
    tm = metricas["multiplas_tentativas"]

    lines.append(f"| N (perguntas) | {g['n']} | {t1['n']} | {tm['n']} |")
    lines.append(f"| Tokens input médios | {g['tokens_input_medio']:.0f} | {t1['tokens_input_medio']:.0f} | {tm['tokens_input_medio']:.0f} |")
    lines.append(f"| Tokens output médios | {g['tokens_output_medio']:.0f} | {t1['tokens_output_medio']:.0f} | {tm['tokens_output_medio']:.0f} |")
    lines.append(f"| Tokens total (soma) | {g['tokens_total_soma']} | {t1['tokens_total_soma']} | {tm['tokens_total_soma']} |")
    lines.append(f"| Tempo médio (ms) | {g['tempo_medio_ms']:.0f} | {t1['tempo_medio_ms']:.0f} | {tm['tempo_medio_ms']:.0f} |")
    lines.append("")
    return lines


def gerar_secao_visualizacao(stats: dict) -> list[str]:
    """Gera seção do relatório com estatísticas de visualização."""
    lines = []
    lines.append("## 5. Estatísticas do Agente de Visualização")
    lines.append("")
    lines.append("| Métrica | Valor |")
    lines.append("|---------|-------|")
    lines.append(f"| Total de queries | {stats['total_queries']} |")
    lines.append(f"| Acionaram agente de gráfico | {stats['acionaram_agente']} |")
    lines.append(f"| Gráficos gerados com sucesso | {stats['graficos_sucesso']} |")
    lines.append(f"| Gráficos com falha | {stats['graficos_falha']} |")
    lines.append("")
    return lines


def gerar_relatorio_empirico_completo(
    report_path: str,
    dataset_label: str,
    all_rows: list[dict],
    output_dir: str,
) -> str:
    """
    Gera o relatório empírico completo em Markdown com todos os 5 módulos.
    
    Args:
        report_path: Caminho para salvar o relatório .md
        dataset_label: "Spider" ou "Spider 2.0 Lite"
        all_rows: Lista de dicts com resultados por pergunta
        output_dir: Diretório para salvar gráficos e CSVs auxiliares
    
    Returns:
        Caminho do relatório gerado.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = []
    lines.append(f"# Relatório Empírico — {dataset_label}")
    lines.append("")
    lines.append(f"**Gerado em:** {timestamp}")
    lines.append("")

    # 1. Distribuição de tentativas + ablação
    freq = calcular_distribuicao_tentativas(all_rows)
    ablacao = calcular_ablacao_critico(all_rows)

    grafico_dist = str(Path(output_dir) / "distribuicao_tentativas.png")
    gerar_grafico_distribuicao_tentativas(freq, grafico_dist, dataset_label)

    grafico_abl = str(Path(output_dir) / "ablacao_critico.png")
    gerar_grafico_ablacao(ablacao, grafico_abl, dataset_label)

    lines.extend(gerar_secao_distribuicao_tentativas(freq, ablacao, grafico_dist, grafico_abl))

    # 1.5 Transições de Autocorreção
    transicoes = calcular_transicoes_autocorrecao(all_rows)
    detalhes_csv = str(Path(output_dir) / "detalhes_transicoes.csv")
    n_detalhes = exportar_detalhes_transicoes(all_rows, detalhes_csv)
    lines.extend(gerar_secao_transicoes(transicoes, n_detalhes, detalhes_csv))

    # 2. Matriz de confusão
    mc = calcular_matriz_confusao(all_rows)
    fps_csv = str(Path(output_dir) / "falsos_positivos.csv")
    n_fps = exportar_falsos_positivos(all_rows, fps_csv)
    lines.extend(gerar_secao_matriz_confusao(mc, n_fps, fps_csv))

    # 3. Taxonomia de erros
    erros_detalhados, contagem_erros = classificar_erros(all_rows)
    erros_csv = str(Path(output_dir) / "taxonomia_erros.csv")
    exportar_taxonomia_erros_csv(erros_detalhados, erros_csv)

    grafico_erros = ""
    if contagem_erros:
        grafico_erros = str(Path(output_dir) / "taxonomia_erros.png")
        gerar_grafico_taxonomia_erros(contagem_erros, grafico_erros, dataset_label)

    lines.extend(gerar_secao_taxonomia_erros(contagem_erros, len(erros_detalhados), erros_csv, grafico_erros))

    # 4. Métricas operacionais
    metricas = calcular_metricas_operacionais(all_rows)
    lines.extend(gerar_secao_metricas_operacionais(metricas))

    # 5. Estatísticas de visualização
    stats_viz = calcular_estatisticas_visualizacao(all_rows)
    lines.extend(gerar_secao_visualizacao(stats_viz))

    # Salvar
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return report_path
