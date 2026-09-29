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
#   - PostgreSQL: pg_read_file/pg_read_binary_file/pg_ls_dir leem arquivos e
#            diretórios do servidor; lo_import/lo_export leem/escrevem via
#            large objects — todas exigem privilégio elevado, mas são
#            bloqueadas aqui como defesa em profundidade.
#   - SQLite: load_extension() carrega uma biblioteca nativa arbitrária.
BLOQUEIOS_REGEX = re.compile(
    r"\b("
    r"insert|update|delete|drop|alter|truncate|create|attach|detach|pragma|"
    r"vacuum|reindex|replace|"
    r"into\s+outfile|into\s+dumpfile|load_file|load_extension|"
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


def _campos_obrigatorios_faltando(db_config: dict[str, Any], campos: tuple[str, ...]) -> str:
    faltando = [c for c in campos if not db_config.get(c)]
    if faltando:
        return f"db_config incompleto, faltam: {', '.join(faltando)}"
    return ""


def executar_sql_postgres(
    db_config: dict[str, Any],
    sql: str,
    limite_preview: int = 5,
    timeout_segundos: float = 15.0,
) -> dict[str, Any]:
    """
    Executa SQL validada em PostgreSQL (read-only, via transacao somente leitura)
    e retorna resultado estruturado no mesmo formato de `executar_sql_sqlite`.

    db_config esperado: {"host", "port" (opcional, default 5432), "database", "user", "password"}.
    """
    ok, erro_validacao = validar_sql_segura(sql)
    if not ok:
        return _erro_execucao(f"SQL invalida: {erro_validacao}")

    faltando = _campos_obrigatorios_faltando(db_config, ("host", "database", "user", "password"))
    if faltando:
        return _erro_execucao(faltando)

    import psycopg2
    import psycopg2.extras

    try:
        conn = psycopg2.connect(
            host=db_config["host"],
            port=db_config.get("port", 5432),
            dbname=db_config["database"],
            user=db_config["user"],
            password=db_config["password"],
            connect_timeout=int(timeout_segundos),
        )
        try:
            conn.set_session(readonly=True, autocommit=True)
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(f"SET statement_timeout = {int(timeout_segundos * 1000)}")
                cur.execute(sql)
                rows = [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except psycopg2.errors.QueryCanceled:
        return _erro_execucao(f"Query abortada por timeout (> {timeout_segundos}s).")
    except Exception as e:
        return _erro_execucao(f"Falha ao executar SQL: {e}")

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


def executar_sql_mysql(
    db_config: dict[str, Any],
    sql: str,
    limite_preview: int = 5,
    timeout_segundos: float = 15.0,
) -> dict[str, Any]:
    """
    Executa SQL validada em MySQL (conexao read-only) e retorna resultado
    estruturado no mesmo formato de `executar_sql_sqlite`.

    db_config esperado: {"host", "port" (opcional, default 3306), "database", "user", "password"}.
    """
    ok, erro_validacao = validar_sql_segura(sql)
    if not ok:
        return _erro_execucao(f"SQL invalida: {erro_validacao}")

    faltando = _campos_obrigatorios_faltando(db_config, ("host", "database", "user", "password"))
    if faltando:
        return _erro_execucao(faltando)

    import pymysql
    import pymysql.cursors

    try:
        conn = pymysql.connect(
            host=db_config["host"],
            port=db_config.get("port", 3306),
            database=db_config["database"],
            user=db_config["user"],
            password=db_config["password"],
            connect_timeout=int(timeout_segundos),
            read_timeout=int(timeout_segundos),
            cursorclass=pymysql.cursors.DictCursor,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
                cur.execute(f"SET SESSION max_execution_time = {int(timeout_segundos * 1000)}")
                cur.execute(sql)
                rows = list(cur.fetchall())
        finally:
            conn.close()
    except pymysql.err.OperationalError as e:
        erro_msg = str(e)
        if "1317" in erro_msg or "max_execution_time" in erro_msg.lower():
            return _erro_execucao(f"Query abortada por timeout (> {timeout_segundos}s).")
        return _erro_execucao(f"Falha ao executar SQL: {erro_msg}")
    except Exception as e:
        return _erro_execucao(f"Falha ao executar SQL: {e}")

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


def executar_sql(
    dialeto: str,
    sql: str,
    db_path: str = "",
    db_config: dict[str, Any] | None = None,
    limite_preview: int = 5,
    timeout_segundos: float = 15.0,
) -> dict[str, Any]:
    """
    Dispatcher unico do executor: roteia para o backend correto conforme `dialeto`.

    - "sqlite" (padrao): usa `db_path` (arquivo local).
    - "postgresql" / "mysql": usa `db_config` (host/port/database/user/password).
    """
    if dialeto == "postgresql":
        return executar_sql_postgres(db_config or {}, sql, limite_preview, timeout_segundos)
    if dialeto == "mysql":
        return executar_sql_mysql(db_config or {}, sql, limite_preview, timeout_segundos)
    return executar_sql_sqlite(db_path, sql, limite_preview, timeout_segundos)


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


def executar_sql_via_engine(
    engine: Any,
    sql: str,
    limite_preview: int = 5,
    timeout_segundos: float = 15.0,
) -> dict[str, Any]:
    """
    Executa SQL usando uma Engine SQLAlchemy ja construida pelo chamador
    (o dialeto e lido de `engine.dialect.name`, sem precisar de
    `db_dialeto`/`db_config`/`db_url` no estado do grafo).

    Ao contrario de `executar_sql_via_url`, esta funcao NAO cria nem
    descarta (`dispose`) a engine: o ciclo de vida da conexao e
    responsabilidade de quem a construiu, nao desta biblioteca.
    """
    ok, erro_validacao = validar_sql_segura(sql)
    if not ok:
        return _erro_execucao(f"SQL invalida: {erro_validacao}")

    from sqlalchemy import text

    try:
        opcoes = {}
        if engine.dialect.name == "postgresql":
            opcoes["postgresql_readonly"] = True

        with engine.connect() as conn:
            conn = conn.execution_options(**opcoes) if opcoes else conn

            # Aplica timeout e modo somente-leitura conforme o dialeto detectado.
            if engine.dialect.name == "postgresql":
                conn.execute(text(f"SET statement_timeout = {int(timeout_segundos * 1000)}"))
            elif engine.dialect.name == "mysql":
                conn.execute(text("SET SESSION TRANSACTION READ ONLY"))
                conn.execute(text(f"SET SESSION max_execution_time = {int(timeout_segundos * 1000)}"))
            elif engine.dialect.name == "sqlite":
                dbapi_conn = getattr(conn.connection, "dbapi_connection", conn.connection)
                inicio = time.time()

                def _progress_handler():
                    return 1 if time.time() - inicio > timeout_segundos else 0

                dbapi_conn.set_progress_handler(_progress_handler, 1000)

            resultado = conn.execute(text(sql))
            rows = [dict(linha) for linha in resultado.mappings().all()]
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
    mysql+pymysql://, etc). Alternativa mais simples ao `executar_sql`
    quando o estado traz `db_url` em vez de `db_dialeto`/`db_config`.

    Cria e descarta a engine internamente. Se o chamador ja possui uma
    Engine (e quer controlar seu ciclo de vida/pool), use
    `executar_sql_via_engine` em vez desta funcao.
    """
    db_url = _normalizar_db_url(db_url)

    from sqlalchemy import create_engine

    try:
        engine = create_engine(db_url)
    except Exception as e:
        return _erro_execucao(f"URL de conexao invalida: {e}")

    try:
        return executar_sql_via_engine(engine, sql, limite_preview, timeout_segundos)
    finally:
        engine.dispose()