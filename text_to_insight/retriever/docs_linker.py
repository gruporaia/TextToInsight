import re
import unicodedata
from pathlib import Path

LIMITE_CARACTERES = 2000
_TITULO = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_ITEM_REGRA = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+\*\*(.+?)\*\*\s*[:=]?\s*(.*)$")
_CRASE = re.compile(r"`([^`]+)`")
_QUALIFICADO = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b")


def _slug(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", sem_acento.lower()).strip("_")[:60]


def _secoes(texto: str) -> list[tuple[str, list[str]]]:
    secoes, titulo, linhas, cerca = [], "", [], False
    for linha in texto.splitlines():
        if linha.lstrip().startswith("```"):
            cerca = not cerca
        achou = None if cerca else _TITULO.match(linha)
        if achou:
            secoes.append((titulo, linhas))
            titulo, linhas = achou.group(2), []
        else:
            linhas.append(linha)
    secoes.append((titulo, linhas))
    return [(t, l) for t, l in secoes if "\n".join(l).strip()]


def _itens_de_regra(linhas: list[str]) -> list[tuple[str, str]]:
    itens, aberto = [], False
    for linha in linhas:
        achou = _ITEM_REGRA.match(linha)
        if achou:
            itens.append([achou.group(1).strip(), achou.group(2).strip()])
            aberto = True
        elif aberto and linha.strip():
            itens[-1][1] = f"{itens[-1][1]} {linha.strip()}".strip()
        else:
            aberto = False
    return [tuple(i) for i in itens]


def _partes(texto: str) -> list[str]:
    partes, atual = [], []
    for linha in texto.split("\n"):
        if atual and len("\n".join(atual + [linha])) > LIMITE_CARACTERES:
            partes.append("\n".join(atual))
            atual = []
        atual.append(linha)
    partes.append("\n".join(atual))
    return partes


def catalogo_do_schema(contexto_schema: str) -> dict[str, set[str]]:
    catalogo, atual = {}, None
    for linha in contexto_schema.splitlines():
        if linha.startswith("Tabela: "):
            atual = linha[len("Tabela: "):].strip()
            catalogo[atual] = set()
        elif atual and linha.startswith("- ") and ":" in linha:
            catalogo[atual].add(linha[2:].split(":", 1)[0].strip())
    return catalogo


def _alvos(texto: str, catalogo: dict[str, set[str]]) -> tuple[list[str], list[str]]:
    tabelas, colunas = {}, {}
    dono_coluna: dict[str, list[str]] = {}
    for tab, cols in catalogo.items():
        for col in cols:
            dono_coluna.setdefault(col, []).append(tab)

    trechos_crase = _CRASE.findall(texto)
    for nome in trechos_crase + [f"{t}.{c}" for t, c in _QUALIFICADO.findall(texto)]:
        nome = nome.strip()
        if nome in catalogo:
            tabelas[nome] = None
            continue
        tabela, _, coluna = nome.partition(".")
        if coluna and coluna in catalogo.get(tabela, ()):
            tabelas[tabela] = None
            colunas[nome] = None

    for trecho in trechos_crase:
        sem_qualificados = _QUALIFICADO.sub(" ", trecho)
        for identificador in re.findall(r"[A-Za-z_]\w*", sem_qualificados):
            if identificador in catalogo:
                tabelas[identificador] = None
            elif len(dono_coluna.get(identificador, ())) == 1:
                tabelas[dono_coluna[identificador][0]] = None

    return list(tabelas), list(colunas)


def _alvo_do_titulo(titulo: str, catalogo: dict[str, set[str]]) -> tuple[str, str] | None:
    nome = titulo.strip().strip("`").strip()
    if nome in catalogo:
        return "tabela", nome
    tabela, _, coluna = nome.partition(".")
    if coluna and coluna in catalogo.get(tabela, ()):
        return "coluna", nome
    return None


def extrair_trechos(texto_markdown: str, contexto_schema: str, origem: str = "") -> list[dict]:
    catalogo = catalogo_do_schema(contexto_schema)
    trechos, usados = [], set()
    for titulo_secao, linhas in _secoes(texto_markdown):
        alvo = _alvo_do_titulo(titulo_secao, catalogo)
        itens = [] if alvo else _itens_de_regra(linhas)
        if not alvo and not itens:
            itens = [(titulo_secao.strip() or "introducao", parte) for parte in _partes("\n".join(linhas).strip())]
        if alvo:
            tipo, nome = alvo
            base = f"{'tbl' if tipo == 'tabela' else 'col'}:{nome}"
            partes = _partes("\n".join(linhas).strip())
            for n, parte in enumerate(partes, 1):
                varias = len(partes) > 1
                trechos.append({"tipo": tipo, "id": f"{base}#{n}" if varias and n > 1 else base,
                                "titulo": f"{nome} (parte {n})" if varias else nome, "descricao": parte,
                                "tabelas_alvo": [nome.split(".")[0]],
                                "colunas_alvo": [nome] if tipo == "coluna" else [], "origem_documento": origem,
                                "precisa_llm": False})
        for titulo, descricao in itens:
            base = f"rule:{_slug(titulo)}"
            id_regra, n = base, 2
            while id_regra in usados:
                id_regra, n = f"{base}_{n}", n + 1
            usados.add(id_regra)
            tabelas, colunas = _alvos(f"{titulo} {descricao}", catalogo)
            trechos.append({"tipo": "regra", "id": id_regra, "titulo": titulo, "descricao": descricao,
                            "tabelas_alvo": tabelas, "colunas_alvo": colunas, "origem_documento": origem,
                            "precisa_llm": not tabelas})
    return trechos


def carregar_documentos_md(docs_dir: str | Path | None) -> list[dict]:
    """
    Lê todos os arquivos `.md` dentro de `docs_dir` (ou um arquivo `.md` único)
    e transforma cada documento em string no formato `{"origem": ..., "contexto_regras": ...}`.
    """
    if not docs_dir:
        return []
    caminho_base = Path(docs_dir)
    if not caminho_base.exists():
        print(f"[DOCS_LINKER] Aviso: caminho de documentos '{docs_dir}' não encontrado.")
        return []

    if caminho_base.is_file():
        arquivos = [caminho_base] if caminho_base.suffix.lower() == ".md" else []
        raiz = caminho_base.parent
    else:
        arquivos = sorted(
            arq for arq in caminho_base.rglob("*")
            if arq.is_file() and arq.suffix.lower() == ".md"
        )
        raiz = caminho_base

    documentos: list[dict] = []
    for arq in arquivos:
        try:
            conteudo = arq.read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"[DOCS_LINKER] Não foi possível ler '{arq}': {e}")
            continue
        if conteudo:
            documentos.append(
                {
                    "origem": str(arq.relative_to(raiz)),
                    "contexto_regras": conteudo,
                }
            )
    return documentos

# SUPORTE GERAL:
#TODO adicionar suporte a pdf, txt e linking automático para descrições de Tabelas em metadados do banco

# ABRANGÊNCIA:
#TODO adicionar uso de LLM para inferência da ligação de regras e tabelas/colunas alvo
#TODO adicionar verificação das respostas da LLM cruzando tabelas/colunas com a regra para verificar integridade de resultado