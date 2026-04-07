"""
Módulo Spider: Integração com dataset Spider para avaliação de queries SQL.

Submódulos:
- data_loader: Carregar exemplos de dev.json
- query_executor: Executar queries em bancos SQLite do spider
- metrics: Comparar queries (similarity score, resultado exato)
- csv_reporter: Salvar métricas em CSV por tentativa
"""
