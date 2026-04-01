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