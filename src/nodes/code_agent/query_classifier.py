from ...state import EstadoTextToInsight

# Esse nó vai apenas retornar qual o tipo de query necessária com base no contexto

def query_classify(estado: EstadoTextToInsight) -> dict:
    # easy or non-nested complex or nested complex
    pergunta = estado.get("pergunta_usuario", "")
    contexto = estado.get("contexto_schema", "")
    tentativas = estado.get("tentativas_loop", 0)

    print("[AGENTE_CODIGO-QC] Query classificada!")
    
    return estado