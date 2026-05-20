"""
Nó Schema (Esquema) do grafo de agentes Text-to-Insight.

O nó de schema é responsável por:
- Recuperar metadados e contexto do banco de dados
- Fornecer informações estruturais (tabelas, colunas, tipos)
- Enriquecer o estado com informações necessárias para a geração de código
"""
from pathlib import Path
import sqlite3

from ..state import EstadoTextToInsight


def _formatar_schema_sqlite(conn: sqlite3.Connection) -> str:
    """
    Constrói uma representação textual do schema de um banco SQLite.

    O formato retornado inclui:
    - nome de cada tabela de usuário (ignora tabelas internas sqlite_*),
    - colunas com tipo e principais constraints,
    - relações de chave estrangeira (quando existirem).

    Args:
        conn: Conexão SQLite já aberta

    Returns:
        str: Texto formatado para ser usado como contexto de schema no agente
    """
    # Usa cursor único para consultas de introspecção.
    cursor = conn.cursor()

    # Lista somente tabelas de usuário; tabelas internas do SQLite são ignoradas.
    cursor.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    )
    tabelas = [row[0] for row in cursor.fetchall()]

    # Cabeçalho do schema textual que será passado adiante no estado.
    partes = ["=== SCHEMA SQLITE (INTROSPECCAO REAL) ===", ""]

    if not tabelas:
        partes.append("Nenhuma tabela encontrada no banco.")
        return "\n".join(partes)

    for tabela in tabelas:
        partes.append(f"Tabela: {tabela}")

        tabela_segura = tabela.replace("'", "''")  # Escapa aspas simples para segurança

        # PRAGMA table_info retorna metadados de colunas da tabela.
        cursor.execute(f"PRAGMA table_info('{tabela}')")
        colunas = cursor.fetchall()
        if colunas:
            for col in colunas:
                # cid, name, type, notnull, dflt_value, pk
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

        # PRAGMA foreign_key_list retorna os relacionamentos da tabela.
        cursor.execute(f"PRAGMA foreign_key_list('{tabela}')")
        fks = cursor.fetchall()
        if fks:
            partes.append("  Foreign keys:")
            for fk in fks:
                # id, seq, table, from, to, on_update, on_delete, match
                _, _, tabela_ref, col_origem, col_destino, on_upd, on_del, _ = fk
                partes.append(
                    f"  - {col_origem} -> {tabela_ref}.{col_destino} "
                    f"(on_update={on_upd}, on_delete={on_del})"
                )

        partes.append("")

    return "\n".join(partes)

def nos_nodo_esquema(estado: EstadoTextToInsight) -> dict:
    """
    Nó Schema: Busca contexto e metadados do banco de dados.
    
    Executa uma consulta ao banco de dados para obter:
    - Estrutura das tabelas
    - Colunas e tipos de dados
    - Relacionamentos
    - Informações de índices
    
    Args:
        estado (EstadoTextToInsight): Estado atual do grafo.
    
    Returns:
        dict: Dicionário com atualizações do estado.
              - contexto_schema: String com metadados simulados
              - status: 'schema_obtido'
    """
    


    # Simular busca de schema do banco de dados
    print("[SCHEMA] Iniciando introspecção do SQLite....")
    
    db_path = estado.get("db_path", "").strip()
    if not db_path:
        msg = "db_path não informado no estado."
        print(f"[SCHEMA] Erro: {msg}")
        return {
            "contexto_schema": "",
            "erro_execucao": msg,
            "status": "exec_erro",
        }

    caminho_db = Path(db_path)
    if not caminho_db.exists():
        msg = f"Arquivo de banco não encontrado: {db_path}"
        print(f"[SCHEMA] Erro: {msg}")
        return {
            "contexto_schema": "",
            "erro_execucao": msg,
            "status": "exec_erro",
        }

    cache_path = caminho_db.with_name(f"{caminho_db.stem}_enriched_schema.txt")
    if cache_path.exists():
        print(f"[SCHEMA] Schema enriquecido em cache encontrado para {caminho_db.stem}.")
        with open(cache_path, "r", encoding="utf-8") as f:
            contexto_cache = f.read()
            
        return {
            "contexto_schema": contexto_cache,
            "erro_execucao": "",
            "status": "schema_obtido",
            "tem_descricao": True  #Já tem descrição enriquecida, então pode pular o enrich
        }

    try:
        # Modo somente leitura para maior segurança
        conn = sqlite3.connect(f"file:{caminho_db}?mode=ro", uri=True)
        try:
            contexto = _formatar_schema_sqlite(conn) # formatação do schema passando a conexão aberta
        finally:
            conn.close()
        # cache_path = caminho_db.with_name(f"{caminho_db.stem}_full_schema.txt")
        # with open(cache_path, "w", encoding="utf-8") as f:
        #     f.write(contexto)
        print("[SCHEMA] Contexto obtido com sucesso.")
        return {
            "contexto_schema": contexto,
            "erro_execucao": "",
            "status": "schema_obtido",
            "tem_descricao": False #SQLite não vai ter descrição nunca, então sempre vai cair no enrich
        }
    except Exception as e:
        msg = f"Falha ao ler schema SQLite: {e}"
        print(f"[SCHEMA] Erro: {msg}")
        return {
            "contexto_schema": "",
            "erro_execucao": msg,
            "status": "exec_erro",
        }