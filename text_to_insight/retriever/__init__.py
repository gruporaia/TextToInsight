from .engine import SchemaGraphRAG

#após terminar o SchemaGraphRAG, vamos ter que adicionar um nó dentro do grafo para fazer a ponte entre o SCHEMA e o Planner, passando só o necessário para o Planner e demais nós
#(opcional) o schema_vault deve ser um json com o mapping feito para que o modelo de API não identifique nomes e colunas sensíveis || essa configuração seria para garantir
#segurança dos dados em questão, essa abordagem vai meio que se alinhar com os modelos locais para gerar código, já que dados sensíveis como os da Heineken não estariam em perigo com o nosso sistema.

__all__ = ["SchemaGraphRAG"]