import re
import json
from pathlib import Path
from typing import Any

from ..cache import salvar_schema_enriquecido
from ..state import EstadoTextToInsight
from ..token_tracking import build_batch_token_usage_update


def nos_nodo_enrich(estado: EstadoTextToInsight, llm: Any, cache_dir: str | Path | None = None) -> dict:
    print("[SCHEMA-ENRICHMENT] Iniciando enriquecimento do schema (Modo Seguro JSON)...")

    raw_schema = estado.get("contexto_schema", "").strip()

    regex_pattern = r"(Tabela: [\s\S]*?)(?=\nTabela: |$)"
    table_chunks = re.findall(regex_pattern, raw_schema)

    TAMANHO_LOTE = 5
    lotes = [table_chunks[i:i + TAMANHO_LOTE] for i in range(0, len(table_chunks), TAMANHO_LOTE)]

    prompts = []
    for lote in lotes:
        texto_tabelas = "\n\n".join([t.strip() for t in lote])

        prompt = (
            "Você é um especialista em banco de dados. "
            "Sua tarefa é ler as tabelas abaixo e gerar descrições semânticas BREVES para as colunas.\n\n"
            "Retorne APENAS um objeto JSON válido, sem formatação markdown (```json), seguindo EXATAMENTE esta estrutura:\n"
            "{\n"
            '  "nome_da_tabela": {\n'
            '    "nome_da_coluna": "descrição curta",\n'
            '    "outra_coluna": "outra descrição"\n'
            "  }\n"
            "}\n\n"
            f"Tabelas a processar:\n{texto_tabelas}"
        )
        prompts.append(prompt)

    print(f"[SCHEMA-ENRICHMENT] Processando {len(table_chunks)} tabelas divididas em {len(lotes)} lotes...")

    llm_temp = llm.bind(temperature=0)
    respostas = llm_temp.batch(prompts, config={"max_concurrency": 3}) if prompts else []
    token_update = build_batch_token_usage_update("rag_enrichment", respostas)

    dicionario_descricoes = {}
    for resp in respostas:
        texto_limpo = resp.content.replace("```json\n", "").replace("```\n", "").replace("```", "").strip()
        try:
            lote_json = json.loads(texto_limpo)
            dicionario_descricoes.update(lote_json)
        except json.JSONDecodeError as e:
            print(f"[SCHEMA-ENRICHMENT] Aviso: Falha ao fazer parse de um lote JSON. {e}")

    linhas_originais = raw_schema.split('\n')
    linhas_enriquecidas = []
    tabela_atual = None

    for linha in linhas_originais:
        if linha.startswith("Tabela: "):
            tabela_atual = linha.replace("Tabela: ", "").strip()
            linhas_enriquecidas.append(linha)

        elif linha.startswith("- ") and ":" in linha and tabela_atual:
            nome_coluna = linha.split(":")[0].replace("- ", "").strip()

            desc = dicionario_descricoes.get(tabela_atual, {}).get(nome_coluna)

            if desc:
                linhas_enriquecidas.append(f"{linha} -- [{desc}]")
            else:
                linhas_enriquecidas.append(linha)

        else:
            linhas_enriquecidas.append(linha)

    contexto_enriquecido = "\n".join(linhas_enriquecidas)

    # O cache é indexado pelo hash do schema original (o mesmo texto que o nó
    # de schema produz), então a próxima execução sobre este banco o encontra.
    cache_path = salvar_schema_enriquecido(cache_dir, estado.get("contexto_schema", ""), contexto_enriquecido)
    if cache_path is not None:
        print(f"[SCHEMA-ENRICHMENT] Schema enriquecido salvo em {cache_path}.")

    return {
        "tem_descricao": True,
        "contexto_schema": contexto_enriquecido,
        **token_update,
    }