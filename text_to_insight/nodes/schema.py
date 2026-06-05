"""
Nó Schema (Esquema) do grafo de agentes Text-to-Insight.

O nó de schema é responsável por:
- Recuperar metadados e contexto do banco de dados
- Fornecer informações estruturais (tabelas, colunas, tipos)
- Enriquecer o estado com informações necessárias para a geração de código

Suporta dois modos de introspecção:
1. Schema Crawler CLI (multi-dialeto: SQLite, DuckDB, PostgreSQL...)
   Ativado quando SCHEMACRAWLER_BIN está configurado no estado.
2. PRAGMA SQLite nativo (fallback garantido para SQLite)
"""
from pathlib import Path
import re
import sqlite3
import subprocess

from ..state import EstadoTextToInsight


# ---------------------------------------------------------------------------
# Dialetos suportados pelo Schema Crawler
# Cada entrada define como montar os args de conexão para o dialeto.
# ---------------------------------------------------------------------------
_SC_DIALETOS = {
    "sqlite": {
        "args": lambda db, _cfg: [
            "--server=sqlite",
            f"--database={db}",
            "--user=",
            "--password=",
        ]
    },
    "duckdb": {
        "args": lambda db, _cfg: [
            f"--url=jdbc:duckdb:{db}",
            "--user=",
            "--password=",
        ]
    },
    "postgresql": {
        "args": lambda _db, cfg: [
            "--server=postgresql",
            f"--host={cfg['host']}",
            f"--port={cfg.get('port', 5432)}",
            f"--database={cfg['database']}",
            f"--user={cfg['user']}",
            f"--password={cfg['password']}",
        ]
    },
}

_EXTENSAO_PARA_DIALETO = {
    ".db":      "sqlite",
    ".sqlite":  "sqlite",
    ".sqlite3": "sqlite",
    ".duckdb":  "duckdb",
}

# apenas dialetos de sqlite e duckdb são detectados por extensão. Para PostgreSQL e outros bancos remotos, é necessário configurar o dialeto explicitamente no estado (ex: "postgresql") e fornecer as credenciais em db_config.


def _detectar_dialeto(db_path: str) -> str:
    """Detecta dialeto pelo sufixo do arquivo. Fallback: sqlite."""
    ext = Path(db_path).suffix.lower()
    return _EXTENSAO_PARA_DIALETO.get(ext, "sqlite")


# ---------------------------------------------------------------------------
# Parser do output TEXTO do Schema Crawler
# Converte para o formato canônico do pipeline:
#
#   Tabela: nome
#   - coluna: TIPO (PK, NOT NULL)
#   - coluna2: TIPO
#     Foreign keys:
#     - col_origem -> tabela_ref.col_destino [cardinalidade]
#     Indexes:
#     - nome_idx [unique]: col1, col2
# ---------------------------------------------------------------------------

def _parsear_texto_schemacrawler(sc_text: str) -> str:
    """
    Faz parse do output --output-format=text do Schema Crawler e
    converte para o formato canônico que o enrich_schema.py e o
    SchemaGraphRAG esperam.

    Extrai, para cada tabela:
    - Colunas com tipo
    - Quais colunas são PK (via seção 'Primary Key')
    - Foreign keys com direção e cardinalidade
    - Índices com tipo (unique / regular)

    Args:
        sc_text: stdout do comando schemacrawler.sh --output-format=text

    Returns:
        str: Schema no formato canônico do pipeline
    """
    linhas = sc_text.splitlines()
    partes = ["=== SCHEMA (SCHEMA CRAWLER) ===", ""]

    # --- Estruturas de estado do parser ---
    tabela_atual: str | None = None
    colunas: list[tuple[str, str]] = []   # [(nome, tipo), ...]
    pks: set[str] = set()
    fks_recebidas: list[str] = []         # linhas já formatadas
    fks_emitidas: list[str] = []
    indexes: list[str] = []

    # Seção corrente dentro de uma tabela
    secao: str | None = None  # "colunas" | "pk" | "fk" | "idx" | None

    def _flush_tabela():
        """Serializa a tabela acumulada no formato canônico."""
        if tabela_atual is None:
            return

        partes.append(f"Tabela: {tabela_atual}")

        for nome, tipo in colunas:
            flags = []
            if nome in pks:
                flags.append("PK")
            sufixo = f" ({', '.join(flags)})" if flags else ""
            partes.append(f"- {nome}: {tipo}{sufixo}")

        # FKs emitidas (esta tabela -> outra)
        todas_fks = fks_emitidas + fks_recebidas
        if todas_fks:
            partes.append("  Foreign keys:")
            for fk in todas_fks:
                partes.append(f"  - {fk}")

        if indexes:
            partes.append("  Indexes:")
            for idx in indexes:
                partes.append(f"  - {idx}")

        partes.append("")

    # Regex para detectar início de tabela:
    # "nome_tabela                    [table]"
    re_tabela = re.compile(r"^(\w+)\s+\[table\]\s*$")

    # Regex para linha de coluna dentro da seção de colunas:
    # "  nome_coluna         TIPO    "  (2+ espaços de indentação)
    re_coluna = re.compile(r"^\s{2}(\w+)\s+(\w+)\s*$")

    # Regex para PK dentro da seção Primary Key:
    # "  nome_coluna    "
    re_pk = re.compile(r"^\s{2}(\w+)\s*$")

    # Regex para FK emitida: "  col (0..many)--> outra_tabela.outra_col"
    re_fk_emitida = re.compile(
        r"^\s{2}(\w+)\s+\((\S+)\)-->\s+(\w+)\.(\w+)\s*$"
    )
    # Regex para FK recebida: "  col <--(0..many) outra_tabela.outra_col"
    re_fk_recebida = re.compile(
        r"^\s{2}(\w+)\s+<--\((\S+)\)\s+(\w+)\.(\w+)\s*$"
    )

    # Regex para linha de índice: "nome_idx           [unique index]" ou "[index]"
    re_idx_nome = re.compile(r"^(\S+)\s+\[(unique index|index)\]\s*$")
    # Colunas do índice: "  col1    unknown"
    re_idx_col = re.compile(r"^\s{2}(\w+)\s+\S+\s*$")

    idx_nome_atual: str | None = None
    idx_tipo_atual: str | None = None
    idx_colunas: list[str] = []

    def _flush_index():
        nonlocal idx_nome_atual, idx_tipo_atual, idx_colunas
        if idx_nome_atual and idx_colunas:
            tipo_label = "unique" if "unique" in (idx_tipo_atual or "") else "index"
            indexes.append(f"{idx_nome_atual} [{tipo_label}]: {', '.join(idx_colunas)}")
        idx_nome_atual = None
        idx_tipo_atual = None
        idx_colunas = []

    for linha in linhas:
        linha_stripped = linha.strip()

        # --- Separador de seções (linha de ===== ou -----) ---
        if re.match(r"^[=\-]{10,}$", linha_stripped):
            continue

        # --- Cabeçalhos de seção dentro de uma tabela ---
        if linha_stripped == "Primary Key":
            secao = "pk"
            continue
        if linha_stripped == "Foreign Keys":
            secao = "fk"
            continue
        if linha_stripped == "Indexes":
            _flush_index()
            secao = "idx"
            continue

        # --- Início de nova tabela ---
        m_tabela = re_tabela.match(linha_stripped)
        if m_tabela:
            # Salva tabela anterior
            _flush_index()
            _flush_tabela()
            # Reinicia estado
            tabela_atual = m_tabela.group(1)
            colunas = []
            pks = set()
            fks_recebidas = []
            fks_emitidas = []
            indexes = []
            idx_nome_atual = None
            idx_colunas = []
            secao = "colunas"
            continue

        # --- Linhas vazias ou de metadados globais (antes de Tables) ---
        if tabela_atual is None:
            continue

        # --- Parser por seção ---

        if secao == "colunas":
            m = re_coluna.match(linha)
            if m:
                colunas.append((m.group(1), m.group(2)))

        elif secao == "pk":
            m = re_pk.match(linha)
            if m:
                pks.add(m.group(1))

        elif secao == "fk":
            m = re_fk_emitida.match(linha)
            if m:
                col, card, tab_ref, col_ref = m.groups()
                fks_emitidas.append(f"{col} -> {tab_ref}.{col_ref} ({card})")
                continue
            m = re_fk_recebida.match(linha)
            if m:
                col, card, tab_ref, col_ref = m.groups()
                fks_recebidas.append(f"{col} <- {tab_ref}.{col_ref} ({card})")

        elif secao == "idx":
            # Nova entrada de índice
            m = re_idx_nome.match(linha_stripped)
            if m:
                _flush_index()
                idx_nome_atual = m.group(1)
                idx_tipo_atual = m.group(2)
                idx_colunas = []
                continue
            # Coluna do índice atual
            if idx_nome_atual:
                m = re_idx_col.match(linha)
                if m:
                    idx_colunas.append(m.group(1))

    # Flush da última tabela
    _flush_index()
    _flush_tabela()

    return "\n".join(partes)


# ---------------------------------------------------------------------------
# Chamada ao Schema Crawler CLI
# ---------------------------------------------------------------------------

def _rodar_schemacrawler(
    db_path: str,
    dialeto: str,
    sc_bin: str,
    cfg: dict,
) -> str:
    """
    Executa o Schema Crawler via subprocess e retorna o texto do schema já
    convertido para o formato canônico do pipeline.

    Args:
        db_path: Caminho do banco de dados
        dialeto:  'sqlite' | 'duckdb' | 'postgresql'
        sc_bin:   Caminho para schemacrawler.sh
        cfg:      Configuração de conexão para bancos remotos

    Returns:
        str: Schema no formato canônico
    """
    dialeto_config = _SC_DIALETOS.get(dialeto)
    if not dialeto_config:
        raise ValueError(f"Dialeto não suportado pelo Schema Crawler: {dialeto}")

    args_dialeto = dialeto_config["args"](db_path, cfg)
    cmd = [
        sc_bin,
        *args_dialeto,
        "--info-level=detailed",
        "--command=schema",
        "--output-format=text",
        "--no-info",
        "-schemacrawler.format.hide_weakassociations=false", # mostra as FKs mesmo que não estejam mapeadas como FK no banco 
        "-schemacrawler.format.hide_weakassociation_names=false", # mostra o nome das FKs mesmo que sejam weak 
    ]

    print(f"[SCHEMA] Executando Schema Crawler ({dialeto})...")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=90,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Schema Crawler falhou (código {result.returncode}):\n"
            f"{result.stderr[:800]}"
        )

    return _parsear_texto_schemacrawler(result.stdout)


# ---------------------------------------------------------------------------
# Introspecção SQLite nativa (fallback)
# ---------------------------------------------------------------------------

def _formatar_schema_sqlite(conn: sqlite3.Connection) -> str:
    """
    Constrói representação textual do schema via PRAGMA SQLite.
    Formato compatível com enrich_schema.py e SchemaGraphRAG.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    )
    tabelas = [row[0] for row in cursor.fetchall()]

    partes = ["=== SCHEMA SQLITE (INTROSPECCAO REAL) ===", ""]

    if not tabelas:
        partes.append("Nenhuma tabela encontrada no banco.")
        return "\n".join(partes)

    for tabela in tabelas:
        partes.append(f"Tabela: {tabela}")
        tabela_segura = tabela.replace("'", "''")

        cursor.execute(f"PRAGMA table_info('{tabela_segura}')")
        colunas = cursor.fetchall()
        if colunas:
            for col in colunas:
                _, nome, tipo, notnull, default, pk = col
                flags = []
                if pk:
                    flags.append("PK")
                if notnull:
                    flags.append("NOT NULL")
                if default is not None:
                    flags.append(f"DEFAULT={default}")
                sufixo = f" ({', '.join(flags)})" if flags else ""
                partes.append(f"- {nome}: {tipo}{sufixo}")
        else:
            partes.append("- [sem colunas detectadas]")

        cursor.execute(f"PRAGMA foreign_key_list('{tabela_segura}')")
        fks = cursor.fetchall()
        if fks:
            partes.append("  Foreign keys:")
            for fk in fks:
                _, _, tabela_ref, col_origem, col_destino, on_upd, on_del, _ = fk
                partes.append(
                    f"  - {col_origem} -> {tabela_ref}.{col_destino} "
                    f"(on_update={on_upd}, on_delete={on_del})"
                )

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

def nos_nodo_esquema(estado: EstadoTextToInsight) -> dict:
    """
    Nó Schema: busca metadados do banco de dados e popula contexto_schema.

    Fluxo:
    1. Valida db_path.
    2. Verifica cache enriquecido, se existir, retorna direto (pula enrich).
    3. Tenta Schema Crawler se schemacrawler_bin estiver configurado.
    4. Fallback para PRAGMA SQLite nativo (apenas SQLite).

    O formato de saída é sempre compatível com enrich_schema.py e
    SchemaGraphRAG, independentemente da fonte de introspecção.

    Campos lidos do estado:
        db_path (str):           Caminho do banco de dados (obrigatório)
        schemacrawler_bin (str): Path para schemacrawler.sh (opcional)
        db_config (dict):        host/port/user/password para bancos remotos

    Campos escritos no estado:
        contexto_schema (str)
        erro_execucao (str)
        status (str)
        tem_descricao (bool)
    """
    db_path = estado.get("db_path", "").strip()
    sc_bin  = estado.get("schemacrawler_bin", "").strip()
    db_cfg  = estado.get("db_config", {})
    usar_schemacrawler = estado.get("usar_schemacrawler", True)
    inferir_fks_virtuais = estado.get("inferir_fks_virtuais", False)

    # --- Validação ---
    if not db_path:
        msg = "db_path não informado no estado."
        print(f"[SCHEMA] Erro: {msg}")
        return {"contexto_schema": "", "erro_execucao": msg, "status": "exec_erro"}

    caminho_db = Path(db_path)
    dialeto = _detectar_dialeto(db_path)

    # Para bancos locais, valida existência do arquivo
    if dialeto in ("sqlite", "duckdb") and not caminho_db.exists():
        msg = f"Arquivo de banco não encontrado: {db_path}"
        print(f"[SCHEMA] Erro: {msg}")
        return {"contexto_schema": "", "erro_execucao": msg, "status": "exec_erro"}

    # --- Cache enriquecido ---
    # Nome inclui dialeto para evitar colisão entre bancos diferentes
    cache_path = caminho_db.with_name(
        f"{caminho_db.stem}_{dialeto}_enriched_schema.txt"
    )
    # Compatibilidade com cache gerado antes da separação por dialeto
    cache_legado = caminho_db.with_name(f"{caminho_db.stem}_enriched_schema.txt")

    for cache in (cache_path, cache_legado):
        if cache.exists():
            print(f"[SCHEMA] Cache enriquecido encontrado: {cache.name}")
            return {
                "contexto_schema": cache.read_text(encoding="utf-8"),
                "erro_execucao": "",
                "status": "schema_obtido",
                "tem_descricao": True,
            }

    # --- Introspecção ---
    contexto: str

    sc_disponivel = bool(sc_bin) and Path(sc_bin).exists()

    if usar_schemacrawler and sc_disponivel:
        try:
            contexto = _rodar_schemacrawler(db_path, dialeto, sc_bin, db_cfg)
            print("[SCHEMA] Schema Crawler: introspecção concluída.")
        except Exception as e:
            print(f"[SCHEMA] Schema Crawler falhou ({e}). Tentando fallback...")
            if dialeto == "sqlite":
                conn = sqlite3.connect(f"file:{caminho_db}?mode=ro", uri=True)
                with conn:
                    contexto = _formatar_schema_sqlite(conn)
                print("[SCHEMA] Fallback PRAGMA SQLite aplicado.")
            else:
                msg = f"Schema Crawler falhou para dialeto '{dialeto}' e não há fallback: {e}"
                print(f"[SCHEMA] Erro: {msg}")
                return {"contexto_schema": "", "erro_execucao": msg, "status": "exec_erro"}
    else:
        if dialeto != "sqlite":
            msg = (
                f"Dialeto '{dialeto}' requer Schema Crawler, mas schemacrawler_bin "
                f"não está configurado (ou usar_schemacrawler está desativado)."
            )
            print(f"[SCHEMA] Erro: {msg}")
            return {"contexto_schema": "", "erro_execucao": msg, "status": "exec_erro"}

        if not usar_schemacrawler:
            print("[SCHEMA] usar_schemacrawler desativado — usando PRAGMA SQLite.")
        else:
            print("[SCHEMA] schemacrawler_bin não configurado — usando PRAGMA SQLite.")
        conn = sqlite3.connect(f"file:{caminho_db}?mode=ro", uri=True)
        with conn:
            contexto = _formatar_schema_sqlite(conn)

    # --- Heurística de FKs virtuais ---
    if inferir_fks_virtuais:
        print("[SCHEMA] Inferindo FKs virtuais.")
        contexto = _inferir_fks_virtuais(contexto)

    return {
        "contexto_schema": contexto,
        "erro_execucao": "",
        "status": "schema_obtido",
        "tem_descricao": False,
    }