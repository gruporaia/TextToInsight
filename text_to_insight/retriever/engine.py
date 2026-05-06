#aqui vamos ter a integração da lógica do grafo com a lógica do RAG
#a ideia é que o RAG seja responsável por recuperar as informações relevantes para responder às perguntas, usando o grafo como guia para navegar pelas relações entre os dados

class SchemaGraphRAG:
    def __init__(self):
        #definição do grafo após o get do schema
        pass

    def retrieve(self, query):
        #a query no no schema vai rolar aqui, essa função deve:
        # 1. acessar as tables e colunas relevantes
        # 2. usar as relações do grafo para navegar entre as tabelas e colunas
        # 3. recuperar os dados relevantes para responder à pergunta com as relações necessárias para ligar os dados
        pass