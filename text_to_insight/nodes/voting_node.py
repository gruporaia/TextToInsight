"""
Nó de Votação por Maioria - Arquitetura ReFoRCE.

Responsabilidade: agregar os 5 candidatos SQL gerados em paralelo,
aplicar votação por maioria baseada em assinatura de resultados,
e decidir se há consenso ou se é necessária exploração.

Lógica:
1. Filtrar apenas candidatos com valido=True
2. Agrupar por assinatura_resultado
3. Se max(grupo) >= 3 (>50% de 5): consenso encontrado → aprovar
4. Else: status ambiguo → exploração ou fallback
"""

from collections import Counter
from typing import Any
from ..state import EstadoTextToInsight, EstadoCandidato


def nos_nodo_votacao(estado: EstadoTextToInsight) -> EstadoTextToInsight:
    """
    Nó de votação: avalia consensus entre os candidatos paralelos.
    
    Args:
        estado: Estado do grafo contendo lista de candidatos
    
    Returns:
        Estado atualizado com:
        - status_consenso: "consenso_encontrado", "ambiguo", ou "nao_votado"
        - sql_vencedora: SQL aprovada (se consenso)
        - status: "aprovado" (consenso) ou "ambiguo_precisa_exploracao"
        - motivo_ambiguidade: detalhes se divergência persistir
    """
    
    # Inicializar campos se não existirem
    if "candidatos" not in estado or not estado["candidatos"]:
        estado["status_consenso"] = "nao_votado"
        estado["motivo_ambiguidade"] = "Nenhum candidato gerado (erro no Fan-out)"
        return estado
    
    candidatos: list[EstadoCandidato] = estado["candidatos"]
    print(f"\n📊 VOTAÇÃO: Analisando {len(candidatos)} candidatos...")
    
    # --- PASSO 1: Filtrar candidatos válidos ---
    candidatos_validos = [c for c in candidatos if c.get("valido", False)]
    print(f"   ✓ {len(candidatos_validos)}/{len(candidatos)} candidatos válidos (executados com sucesso)")
    
    if not candidatos_validos:
        print("   ❌ Nenhum candidato válido → marcando como ambiguo (exploração necessária)")
        estado["status_consenso"] = "ambiguo"
        estado["status"] = "ambiguo_precisa_exploracao"
        estado["rodadas_exploracao"] = estado.get("rodadas_exploracao", 0) + 1
        estado["motivo_ambiguidade"] = "Todos os 5 candidatos falharam na execução"
        return estado
    
    # --- PASSO 2: Agrupar por assinatura ---
    assinatura_counts: dict[str, list[EstadoCandidato]] = {}
    for candidato in candidatos_validos:
        assinatura = candidato.get("assinatura_resultado", "UNKNOWN")
        if assinatura not in assinatura_counts:
            assinatura_counts[assinatura] = []
        assinatura_counts[assinatura].append(candidato)
    
    print(f"   ✓ Agrupados em {len(assinatura_counts)} assinatura(s) única(s)")
    for i, (assinatura, grupo) in enumerate(assinatura_counts.items(), 1):
        print(f"      Assinatura {i}: {len(grupo)} candidato(s) | Hash: {assinatura[:20]}...")
    
    # --- PASSO 3: Verificar consenso (>50% = >= 3 de 5) ---
    # Para 5 candidatos: >= 3 = consenso
    max_grupo_count = max(len(grupo) for grupo in assinatura_counts.values())
    limiar_consenso = 3  # >50% de 5 candidatos
    
    print(f"   📈 Grupo maior: {max_grupo_count} candidato(s) | Limiar: {limiar_consenso}")
    
    if max_grupo_count >= limiar_consenso:
        # ✅ CONSENSO ENCONTRADO
        print(f"   ✅ CONSENSO ENCONTRADO: {max_grupo_count} candidatos convergem!")
        
        # Encontrar o grupo vencedor e selecionar um candidato
        grupo_vencedor = max(
            assinatura_counts.values(),
            key=len
        )
        candidato_vencedor = grupo_vencedor[0]
        sql_vencedora = candidato_vencedor.get("sql", "")
        
        estado["status_consenso"] = "consenso_encontrado"
        estado["status"] = "aprovado"
        estado["sql_vencedora"] = sql_vencedora
        estado["sql_gerada"] = sql_vencedora  # Atualizar SQL global
        estado["feedback_critico"] = (
            f"✅ CONSENSO: {max_grupo_count} de {len(candidatos_validos)} "
            f"candidatos convergem para a mesma solução (SQL aprovada por maioria)"
        )
        
        # Copiar resultado do candidato vencedor para estado global
        if "resultado_execucao" in candidato_vencedor:
            resultado = candidato_vencedor["resultado_execucao"]
            estado["linhas_resultado_completo"] = resultado.get("linhas", [])
            estado["total_linhas_resultado"] = resultado.get("total_linhas", 0)
            if "preview" in resultado:
                estado["linhas_resultado_preview"] = resultado["preview"]
        
        print(f"   → SQL vencedora salva: {sql_vencedora[:60]}...")
        return estado
    
    else:
        # ❌ SEM CONSENSO → Ambiguidade
        print(f"   ❌ SEM CONSENSO: máximo {max_grupo_count} de {len(candidatos_validos)} (precisa >= 3)")
        
        # Contar rodadas de exploração
        rodadas = estado.get("rodadas_exploracao", 0)
        max_rodadas = 2
        
        if rodadas >= max_rodadas:
            # Marcar como definitivamente ambíguo
            print(f"   ⚠️ Limite de exploração atingido ({rodadas}/{max_rodadas})")
            estado["status_consenso"] = "ambiguo"
            estado["status"] = "ambiguo"  # Irá para resposta final (fallback)
            estado["motivo_ambiguidade"] = (
                f"Divergência persistente após {rodadas} rodadas de exploração. "
                f"Assinaturas encontradas: {len(assinatura_counts)}. "
                f"Distribuição: {dict((k, len(v)) for k, v in list(assinatura_counts.items())[:3])}"
            )
            print(f"   → Marcado como AMBIGUO PERMANENTE")
        else:
            # Enviar para exploração
            print(f"   → Acionando exploração (rodada {rodadas + 1}/{max_rodadas})")
            estado["status_consenso"] = "ambiguo"
            estado["status"] = "ambiguo_precisa_exploracao"
            estado["rodadas_exploracao"] = rodadas + 1
            
            # Construir motivo da ambiguidade
            lista_assinaturas = "\n".join(
                f"  - Assinatura {i}: {len(grupo)} candidato(s)"
                for i, (_, grupo) in enumerate(assinatura_counts.items(), 1)
            )
            estado["motivo_ambiguidade"] = (
                f"Divergência entre candidatos (nenhum atingiu >50%):\n{lista_assinaturas}\n"
                f"Rodada de exploração {rodadas + 1}/{max_rodadas}"
            )
        
        return estado


def calcular_assinatura_resultado(resultado: list[dict[str, Any]]) -> str:
    """
    Calcula uma assinatura (hash) para um conjunto de resultados.
    
    Usado para comparar se dois candidatos SQL produziram exatamente
    o mesmo resultado (mesmas linhas em mesma ordem).
    
    Args:
        resultado: Lista de dicts com as linhas do resultado
    
    Returns:
        String hash para comparação
    """
    import hashlib
    import json
    
    if not resultado:
        return "EMPTY"
    
    try:
        # Serializar mantendo ordem das linhas
        json_str = json.dumps(resultado, sort_keys=True, default=str)
        hash_obj = hashlib.md5(json_str.encode())
        return hash_obj.hexdigest()
    except Exception as e:
        print(f"⚠️  Erro ao calcular assinatura: {e}")
        return f"ERROR_{str(e)[:20]}"
