"""
Nó para execução de SQL em SQLite, PostgreSQL e MySQL.

Este módulo NÃO gera Python dinamicamente
Ele expõe funções utilitárias para validar e executar SQL em modo seguro

"""
from __future__ import annotations

import re
import time
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


def executar_sql_via_engine(
    engine: Any,
    sql: str,
    limite_preview: int = 5,
    timeout_segundos: float = 15.0,
) -> dict[str, Any]:
    """
    Executa SQL usando uma Engine SQLAlchemy ja construida pelo chamador
    (o dialeto e lido de `engine.dialect.name`).

    Esta funcao NAO cria nem descarta (`dispose`) a engine: o ciclo de vida
    da conexao e responsabilidade de quem a construiu, nao desta biblioteca.
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

                # exec_driver_sql manda a SQL direto ao driver; text() trataria
                # ":palavra" dentro de literais como parâmetro de bind.
                resultado = conn.exec_driver_sql(sql)
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

