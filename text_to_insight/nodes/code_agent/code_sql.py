"""
Nó para execução de SQL em SQLite, PostgreSQL e MySQL.

Este módulo NÃO gera Python dinamicamente
Ele expõe funções utilitárias para validar e executar SQL em modo seguro

"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

# Expressão regular para detectar comandos SQL potencialmente perigosos.
#
# Além dos comandos de escrita/DDL óbvios, bloqueia também construções que
# COMEÇAM com SELECT/WITH (logo passariam pela checagem de "apenas leitura")
# mas na prática escrevem ou leem arquivos no servidor de banco:
#   - MySQL: "SELECT ... INTO OUTFILE/DUMPFILE" escreve um arquivo;
#            LOAD_FILE() lê um arquivo arbitrário do disco.
#            As palavras OUTFILE e DUMPFILE são bloqueadas SOZINHAS (e não a
#            frase "INTO OUTFILE"): o MySQL aceita comentários entre as duas
#            palavras (INTO/**/OUTFILE, INTO /*!50000 OUTFILE*/, ou um
#            comentário de linha seguido de quebra de linha), então casar a
#            frase com \s+ deixava essas variantes passarem. Como OUTFILE e
#            DUMPFILE só existem nessa construção, bloquear a palavra isolada
#            não depende de qual separador foi usado. (Custo: uma coluna
#            chamada "outfile" também seria bloqueada — aceitável.)
#   - PostgreSQL: pg_read_file/pg_read_binary_file/pg_ls_dir leem arquivos e
#            diretórios do servidor; lo_import/lo_export leem/escrevem via
#            large objects — todas exigem privilégio elevado, mas são
#            bloqueadas aqui como defesa em profundidade.
#   - SQLite: load_extension() carrega uma biblioteca nativa arbitrária.
BLOQUEIOS_REGEX = re.compile(
    r"\b("
    r"insert|update|delete|drop|alter|truncate|create|attach|detach|pragma|"
    r"vacuum|reindex|replace|"
    r"outfile|dumpfile|load_file|load_extension|"
    r"pg_read_file|pg_read_binary_file|pg_ls_dir|lo_import|lo_export"
    r")\b",
    re.IGNORECASE,
)

def validar_sql_segura(sql: str) -> tuple[bool, str]:
    """
    Valida SQL para uso seguro no executor.

    Regras:
    - permite apenas consultas iniciadas por SELECT ou WITH
    - bloqueia multiplas statements (mais de um ';')
    - bloqueia comandos de escrita/DDL/administracao

    Retorno:
    - (True, "") se a SQL for considerada segura
    - (False, "mensagem de erro") se a SQL violar as regras
    """

    if not sql or not sql.strip():
        return False, "SQL vazia."

    sql_limpa = sql.strip()

    # Remove ';' final opcional para facilitar regras.
    sql_sem_final = sql_limpa[:-1] if sql_limpa.endswith(";") else sql_limpa

    if ";" in sql_sem_final:
        return False, "Multiplas statements nao sao permitidas."

    inicio = sql_sem_final.lstrip().lower()
    if not (inicio.startswith("select") or inicio.startswith("with")):
        return False, "Apenas consultas SELECT/CTE (WITH) sao permitidas."

    if BLOQUEIOS_REGEX.search(sql_sem_final):
        return False, "SQL contem comando bloqueado por politica de seguranca."

    return True, ""

import time

def executar_sql_sqlite(
    db_path: str,
    sql: str,
    limite_preview: int = 5,
    timeout_segundos: float = 15.0,
) -> dict[str, Any]:
    """
    Executa SQL validada em SQLite modo read-only e retorna resultado estruturado.
    Possui um timeout embutido para evitar queries infinitas (ex: cross joins enormes).
    """
    ok, erro_validacao = validar_sql_segura(sql)
    if not ok:
        return {
            "ok": False,
            "erro_execucao": erro_validacao,
            "linhas_resultado_preview": [],
            "linhas_resultado_completo": [],
            "total_linhas_resultado": 0,
            "saida_terminal": f"[SANDBOX] SQL invalida: {erro_validacao}",
        }

    caminho = Path(db_path)
    if not caminho.exists():
        msg = f"Arquivo de banco nao encontrado: {db_path}"
        return {
            "ok": False,
            "erro_execucao": msg,
            "linhas_resultado_preview": [],
            "linhas_resultado_completo": [],
            "total_linhas_resultado": 0,
            "saida_terminal": f"[SANDBOX] {msg}",
        }

    try:
        conn = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        
        # Define um handler para monitorar o tempo de execução e abortar se passar do limite
        start_time = time.time()
        def _progress_handler():
            if time.time() - start_time > timeout_segundos:
                return 1 # abortar query
            return 0
            
        # Invoca a cada 1000 instruções da máquina virtual do SQLite
        conn.set_progress_handler(_progress_handler, 1000)
        
        try:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
            total = len(rows)
            preview_rows = [dict(r) for r in rows[:limite_preview]]
            all_rows = [dict(r) for r in rows]

            return {
                "ok": True,
                "erro_execucao": "",
                "linhas_resultado_preview": preview_rows,
                "linhas_resultado_completo": all_rows,
                "total_linhas_resultado": total,
                "saida_terminal": (
                    f"[SANDBOX] Execucao OK | linhas_total={total} "
                    f"| preview={min(total, limite_preview)}"
                ),
            }
        finally:
            conn.close()
    except sqlite3.OperationalError as e:
        erro_msg = str(e)
        if "interrupted" in erro_msg.lower():
            erro_msg = f"Query abortada por timeout (> {timeout_segundos}s)."
        return {
            "ok": False,
            "erro_execucao": f"Falha ao executar SQL: {erro_msg}",
            "linhas_resultado_preview": [],
            "linhas_resultado_completo": [],
            "total_linhas_resultado": 0,
            "saida_terminal": f"[SANDBOX] Erro de execucao: {erro_msg}",
        }
    except Exception as e:
        return {
            "ok": False,
            "erro_execucao": f"Falha ao executar SQL: {e}",
            "linhas_resultado_preview": [],
            "linhas_resultado_completo": [],
            "total_linhas_resultado": 0,
            "saida_terminal": f"[SANDBOX] Erro de execucao: {e}",
        }


def _erro_execucao(msg: str) -> dict[str, Any]:
    """Monta a estrutura de retorno padrao para uma falha de execucao."""
    return {
        "ok": False,
        "erro_execucao": msg,
        "linhas_resultado_preview": [],
        "linhas_resultado_completo": [],
        "total_linhas_resultado": 0,
        "saida_terminal": f"[SANDBOX] {msg}",
    }


def _normalizar_db_url(db_url: str) -> str:
    """
    Garante que "postgresql://" e "mysql://" sem driver explicito usem os
    drivers que este projeto de fato instala (psycopg2 e pymysql). Sem isso,
    o SQLAlchemy tenta o driver padrao dele (ex: psycopg v3 para postgres),
    que nao esta instalado aqui, e a conexao falha com "No module named ...".
    """
    if db_url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + db_url[len("postgresql://"):]
    if db_url.startswith("mysql://"):
        return "mysql+pymysql://" + db_url[len("mysql://"):]
    return db_url

def _criar_engine_de_url(db_url: str, timeout_segundos=15.0):
    """ Cria uma engine com timeout de conexão pra evitar problema de 
    runtime com URL"""
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    url =_normalizar_db_url(db_url=db_url)
    dialeto = make_url(db_url).get_backend_name()
    segundos = max(1, int(timeout_segundos))
    connect_args = {}
    if dialeto == "postgresql":
        connect_args = {"connect_timeout": segundos}
    elif dialeto == "mysql":
        connect_args = {"connect_timeout": segundos, "read_timeout": segundos}
    return create_engine(url, connect_args=connect_args)


def executar_sql_via_engine(
    engine: Any,
    sql: str,
    limite_preview: int = 5,
    timeout_segundos: float = 15.0,
) -> dict[str, Any]:
    """
    Executa SQL usando uma Engine SQLAlchemy ja construida pelo chamador
    (o dialeto e lido de `engine.dialect.name`, sem precisar de
    `db_url` no estado do grafo).

    Ao contrario de `executar_sql_via_url`, esta funcao NAO cria nem
    descarta (`dispose`) a engine: o ciclo de vida da conexao e
    responsabilidade de quem a construiu, nao desta biblioteca.
    """
    ok, erro_validacao = validar_sql_segura(sql)
    if not ok:
        return _erro_execucao(f"SQL invalida: {erro_validacao}")

    from sqlalchemy import text

    dialeto = engine.dialect.name

    try:
        opcoes = {}
        if dialeto == "postgresql":
            opcoes["postgresql_readonly"] = True

        with engine.connect() as conn:
            conn = conn.execution_options(**opcoes) if opcoes else conn
            dbapi_conn = None

            # A engine é do chamador e suas conexões voltam para o pool dele,
            # então nenhum ajuste de sessão feito aqui pode sobreviver à chamada.
            try:
                if dialeto == "postgresql":
                    # Roda dentro da transação aberta pelo autobegin, que é
                    # desfeita (rollback) ao fechar: o SET não vaza para o pool.
                    conn.execute(text(f"SET statement_timeout = {int(timeout_segundos * 1000)}"))
                elif dialeto == "mysql":
                    conn.execute(text("SET SESSION TRANSACTION READ ONLY"))
                    conn.execute(text(f"SET SESSION max_execution_time = {int(timeout_segundos * 1000)}"))
                elif dialeto == "sqlite":
                    dbapi_conn = getattr(conn.connection, "dbapi_connection", conn.connection)
                    inicio = time.time()

                    def _progress_handler():
                        return 1 if time.time() - inicio > timeout_segundos else 0

                    dbapi_conn.set_progress_handler(_progress_handler, 1000)

                resultado = conn.execute(text(sql))
                rows = [dict(linha) for linha in resultado.mappings().all()]
            finally:
                if dbapi_conn is not None:
                    dbapi_conn.set_progress_handler(None, 0)
                elif dialeto == "mysql":
                    # SET SESSION não é transacional no MySQL: descarta a conexão
                    # em vez de devolvê-la read-only/com timeout ao pool do chamador.
                    conn.invalidate()
    except Exception as e:
        erro_msg = str(e)
        if "interrupted" in erro_msg.lower() or "timeout" in erro_msg.lower() or "canceling statement" in erro_msg.lower():
            return _erro_execucao(f"Query abortada por timeout (> {timeout_segundos}s).")
        return _erro_execucao(f"Falha ao executar SQL: {erro_msg}")

    total = len(rows)
    return {
        "ok": True,
        "erro_execucao": "",
        "linhas_resultado_preview": rows[:limite_preview],
        "linhas_resultado_completo": rows,
        "total_linhas_resultado": total,
        "saida_terminal": (
            f"[SANDBOX] Execucao OK | linhas_total={total} "
            f"| preview={min(total, limite_preview)}"
        ),
    }


def executar_sql_via_url(
    db_url: str,
    sql: str,
    limite_preview: int = 5,
    timeout_segundos: float = 15.0,
) -> dict[str, Any]:
    """
    Executa SQL usando uma unica URL de conexao (SQLAlchemy detecta o
    dialeto sozinho a partir do prefixo da URL: sqlite:///, postgresql://,
    mysql+pymysql://, etc).

    Cria e descarta a engine internamente. Se o chamador ja possui uma
    Engine (e quer controlar seu ciclo de vida/pool), use
    `executar_sql_via_engine` em vez desta funcao.
    """
    try:
        engine = _criar_engine_de_url(db_url, timeout_segundos)
    except Exception as e:
        return _erro_execucao(f"URL de conexao invalida: {e}")

    try:
        return executar_sql_via_engine(engine, sql, limite_preview, timeout_segundos)
    finally:
        engine.dispose()