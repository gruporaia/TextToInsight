def extrair_tokens(resposta) -> tuple[int, int, int]:
    """Extrai (input, output, total) das respostas do LLM."""
    
    if hasattr(resposta, "usage_metadata") and resposta.usage_metadata:
        usage = resposta.usage_metadata
        return (
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            usage.get("total_tokens", 0)
        )

    # Se nada der certo, retorna zero
    print("[AVISO] Não foi possível extrair os tokens da resposta do LLM.")
    return 0, 0, 0

import os
import csv
from datetime import datetime
def salvar_metricas_csv(resultado: dict, latencia: float, arquivo_csv="data/metricas_execucao.csv"):
    """Salva as métricas de uma execução em um arquivo CSV."""
    arquivo_existe = os.path.isfile(arquivo_csv)
    
    # Colunas 
    dados = {
        "data_hora": datetime.now(),
        "pergunta": resultado.get("pergunta_usuario", ""),
        "status_final": resultado.get("status", ""),
        "tentativas": resultado.get("tentativas_loop", 0),
        "tokens_input": resultado.get("tokens_input", 0),
        "tokens_output": resultado.get("tokens_output", 0),
        "tokens_total": resultado.get("tokens_total", 0),
        "latencia_segundos": round(latencia,2),
        "erro": resultado.get("erro_execucao", "")
    }

    # Garante que o diretório existe
    os.makedirs(os.path.dirname(arquivo_csv), exist_ok=True)

    with open(arquivo_csv, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=dados.keys())
        
        if not arquivo_existe:
            writer.writeheader() # Escreve o cabeçalho na primeira vez
            
        writer.writerow(dados)
    print(f"[MÉTRICAS] Salvas com sucesso em {arquivo_csv}")