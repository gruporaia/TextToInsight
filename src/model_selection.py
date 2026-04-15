from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

def get_model(model_name: str, api_key: str):
    if "gemini" in model_name.lower():
        return ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key)
    elif "gpt" in model_name.lower():
        return ChatOpenAI(model=model_name, openai_api_key=api_key)
    else:
        raise ValueError(f"Modelo '{model_name}' não reconhecido. Use 'gemini' ou 'gpt' no nome do modelo.")
