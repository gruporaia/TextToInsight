"""
Nó Schema (Esquema) do grafo de agentes Text-to-Insight.

O nó de schema é responsável por:
- Recuperar metadados do banco (tabelas, colunas, tipos, PKs e FKs) pela
  Engine SQLAlchemy recebida do chamador;
- Opcionalmente inferir FKs virtuais quando o banco não as declara;
- Reaproveitar o schema enriquecido pelo LLM (enrich_rag) que estiver no
  cache em disco (ver `text_to_insight/cache.py`).

A biblioteca nunca abre conexões por conta própria: o banco é sempre a
Engine que o chamador passou para a InsightEngine.
"""
from pathlib import Path
from typing import Any
import re

from ..cache import ler_schema_enriquecido
from ..state import EstadoTextToInsight


def _formatar_schema_sqlalchemy(engine) -> str:
    from sqlalchemy import inspect

    insp = inspect(engine)
    dialeto = engine.dialect.name
    tabelas = insp.get_table_names()

    partes = [f"=== SCHEMA {dialeto.upper()} (SQLALCHEMY) ===", ""]

    if not tabelas:
        partes.append("Nenhuma tabela encontrada no banco.")
        return "\n".join(partes)

    for tabela in sorted(tabelas):
        partes.append(f"Tabela: {tabela}")

        pk_cols = set(insp.get_pk_constraint(tabela).get("constrained_columns") or [])
        colunas = insp.get_columns(tabela)

        if colunas:
            for col in colunas:
                flags = []
                if col["name"] in pk_cols:
                    flags.append("PK")
                if not col.get("nullable", True):
                    flags.append("NOT NULL")
                if col.get("default") is not None:
                    flags.append(f"DEFAULT={col['default']}")
                sufixo = f" ({', '.join(flags)})" if flags else ""
                partes.append(f"- {col['name']}: {col['type']}{sufixo}")
        else:
            partes.append("- [sem colunas detectadas]")

        fks = insp.get_foreign_keys(tabela)
        if fks:
            partes.append("  Foreign keys:")
            for fk in fks:
                cols_origem = fk.get("constrained_columns") or []
                tabela_ref = fk.get("referred_table")
                cols_destino = fk.get("referred_columns") or []
                for col_origem, col_destino in zip(cols_origem, cols_destino):
                    partes.append(f"  - {col_origem} -> {tabela_ref}.{col_destino}")

        partes.append("")

    return "\n".join(partes)


# ---------------------------------------------------------------------------
# Heurística para analisar a estrutura do schema e injegar relações implícitas
# ---------------------------------------------------------------------------
def _inferir_fks_virtuais(schema_canonico: str) -> str:                               
    """                                                                               
    Varre o schema canônico gerado e infere FKs virtuais se não houver FKs mapeadas.  
    Busca colunas com padrão '<tabela>_id' ou correspondência direta de ID,
    incluindo tabelas com prefixos (ex: olist_orders para order_id).
    Também detecta colunas com nomes idênticos entre tabelas (ex: product_category_name).
    """                                                                               
    linhas = schema_canonico.splitlines()                                             
    tabelas_colunas = {}  # {tabela: [colunas]}                                       
    tabela_atual = None                                                               
                                                                                        
    # 1. Mapeia tabelas e suas colunas                                                
    for linha in linhas:                                                              
        if linha.startswith("Tabela: "):                                              
            tabela_atual = linha.split("Tabela: ")[1].strip()                         
            tabelas_colunas[tabela_atual] = []                                        
        elif linha.startswith("- ") and tabela_atual:                                 
            col_nome = linha.split("- ")[1].split(":")[0].strip()                     
            tabelas_colunas[tabela_atual].append(col_nome)                            
    
    # 2. Pré-computa índice reverso: base_name → lista de tabelas que contêm esse base
    #    Ex: "order" → ["olist_orders", "orders"], "product" → ["olist_products", "products"]
    nomes_tabelas = list(tabelas_colunas.keys())
    nomes_lower = {t: t.lower() for t in nomes_tabelas}
                                                                                        
    novas_linhas = []                                                                 
    tabela_atual = None                                                               
    fks_existentes = set()                                                            
                                                                                        
    # 3. Varre o schema e injeta as FKs virtuais onde faltarem                        
    for linha in linhas:                                                              
        if linha.startswith("Tabela: "):                                              
            tabela_atual = linha.split("Tabela: ")[1].strip()                         
            fks_existentes.clear()                                                    
            novas_linhas.append(linha)                                                
            continue                                                                  
                                                                                        
        if tabela_atual and " -> " in linha:                                          
            # Registra FKs que já existem fisicamente                                 
            fks_existentes.add(linha.strip())                                         
                                                                                        
        novas_linhas.append(linha)                                                    
                                                                                        
        # Se terminou de listar a tabela (próxima linha em branco ou fim)             
        # E não há FKs físicas ou queremos complementar:                              
        if tabela_atual and (linha == "" or linha == linhas[-1]):                     
            fks_injetar = []                                                          
            colunas_da_tabela = tabelas_colunas.get(tabela_atual, [])                 
                                                                                        
            for col in colunas_da_tabela:                                             
                # --- Estratégia 1: colunas terminadas em _id, _code, _no ---
                match = re.match(r"^(\w+?)(?:_id|_code|_no)$", col, re.IGNORECASE)                   
                if match:                                                             
                    base_name = match.group(1).lower()                                
                    
                    # Gera candidatas: match exato, plurais, e variantes com prefixos
                    candidatas_exatas = [
                        base_name,
                        f"{base_name}s",
                        f"{base_name}es",
                        f"{base_name}_data",
                        f"{base_name}s_data",
                    ]
                    
                    tabela_destino = None
                    
                    # Tenta match exato primeiro
                    for cand in candidatas_exatas:
                        if cand in tabelas_colunas and cand != tabela_atual:
                            tabela_destino = cand
                            break
                    
                    # Se não achou, tenta match parcial (tabelas com prefixo)
                    # Ex: base_name="order" → acha "olist_orders" (termina com "orders" ou "order")
                    if not tabela_destino:
                        sufixos_busca = [base_name, f"{base_name}s", f"{base_name}es"]
                        for t in nomes_tabelas:
                            if t == tabela_atual:
                                continue
                            t_low = nomes_lower[t]
                            for sufixo in sufixos_busca:
                                # Tabela termina com o sufixo após um separador (_) ou é o nome completo
                                if t_low.endswith(f"_{sufixo}") or t_low == sufixo:
                                    tabela_destino = t
                                    break
                            if tabela_destino:
                                break
                    
                    if tabela_destino:
                        colunas_destino = tabelas_colunas[tabela_destino]
                        coluna_chave = None
                        
                        # Tenta descobrir qual coluna liga no destino
                        if col in colunas_destino:
                            coluna_chave = col
                        elif "id" in colunas_destino:
                            coluna_chave = "id"
                            
                        if coluna_chave:
                            fk_str = f"  - {col} -> {tabela_destino}.{coluna_chave} (virtual)"     
                            if fk_str not in fks_existentes:                              
                                fks_injetar.append(fk_str)
                    continue
                
                # --- Estratégia 2: colunas com nome idêntico em outra tabela ---
                # Ex: product_category_name aparece em olist_products E em product_category_name_translation
                # Ignora colunas genéricas demais (id, name, type, status, etc.)
                colunas_genericas = {"id", "name", "type", "status", "date", "value", "description", "code"}
                if col.lower() in colunas_genericas:
                    continue
                    
                for outra_tabela, outra_colunas in tabelas_colunas.items():
                    if outra_tabela == tabela_atual:
                        continue
                    if col in outra_colunas:
                        # Verifica se a coluna faz parte do nome da outra tabela
                        # (forte sinal de FK, ex: product_category_name → product_category_name_translation)
                        col_base = col.lower().replace("_", "")
                        outra_base = outra_tabela.lower().replace("_", "")
                        if col_base in outra_base or outra_base.startswith(col_base[:8]):
                            fk_str = f"  - {col} -> {outra_tabela}.{col} (virtual)"
                            if fk_str not in fks_existentes:
                                fks_injetar.append(fk_str)
                                break
                                                                                        
            if fks_injetar:
                # Remove a última linha em branco temporariamente para injetar as FKs 
                if novas_linhas and novas_linhas[-1] == "":
                    novas_linhas.pop()
                if not any("Foreign keys:" in l for l in novas_linhas[-20:]): # Verifica se o cabeçalho existe
                    novas_linhas.append("  Foreign keys:")
                novas_linhas.extend(fks_injetar)
                novas_linhas.append("")

    return "\n".join(novas_linhas)

# ---------------------------------------------------------------------------
# Nó do grafo
# ---------------------------------------------------------------------------

def nos_nodo_esquema(
    estado: EstadoTextToInsight,
    engine: Any,
    enrich_rag: bool = False,
    cache_dir: str | Path | None = None,
) -> dict:
    """
    Nó Schema: lê os metadados do banco pela Engine SQLAlchemy injetada e
    popula `contexto_schema`.

    O dialeto vem de `engine.dialect.name`. Com `enrich_rag` ligado, se este
    schema já foi enriquecido antes, o texto enriquecido do cache é usado e
    `tem_descricao` volta True, o que faz o grafo pular o enriquecimento.
    Com `enrich_rag` desligado o cache não é lido, para que uma execução sem
    enriquecimento nunca receba descrições geradas por uma execução anterior.

    Campos lidos do estado:
        inferir_fks_virtuais (bool)

    Campos escritos no estado:
        contexto_schema (str)
        erro_execucao (str)
        status (str)
        tem_descricao (bool)
    """
    try:
        contexto = _formatar_schema_sqlalchemy(engine)
    except Exception as e:
        msg = f"Falha ao ler schema via engine: {e}"
        print(f"[SCHEMA] Erro: {msg}")
        return {"contexto_schema": "", "erro_execucao": msg, "status": "exec_erro"}

    if estado.get("inferir_fks_virtuais", False):
        print("[SCHEMA] Inferindo FKs virtuais.")
        contexto = _inferir_fks_virtuais(contexto)

    enriquecido = ler_schema_enriquecido(cache_dir, contexto) if enrich_rag else None
    if enriquecido is not None:
        print("[SCHEMA] Schema enriquecido encontrado no cache.")
        return {
            "contexto_schema": enriquecido,
            "erro_execucao": "",
            "status": "schema_obtido",
            "tem_descricao": True,
        }

    return {
        "contexto_schema": contexto,
        "erro_execucao": "",
        "status": "schema_obtido",
        "tem_descricao": False,
    }
