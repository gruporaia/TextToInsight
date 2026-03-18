from ...state import EstadoTextToInsight

# Aqui vou implementar nós diferentes para tipos diferentes de queries

def easy_query(estado: EstadoTextToInsight) -> dict:
    query =  '''SELECT * FROM USUARIOS
                WHERE data_criacao > '2024-01-01'''
    #linkar a query no estado, isso vai precisar de mudanças no state e no grafo itself
    return estado

def non_nested_complex_query(estado: EstadoTextToInsight) -> dict:
    query = ''''''
    return estado

def nested_complex_query(estado: EstadoTextToInsight) -> dict:
    query = ''''''
    return estado