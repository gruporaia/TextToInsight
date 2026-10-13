"""
src/bird/__init__.py

BIRD (Big Bench for Large-scale Database Grounded Text-to-SQL Evaluation) module.
Provides dataset loading, query execution, metrics evaluation, and CSV reporting.
"""

from src.bird.data_loader import (
    get_database_path,
    get_database_uri,
    get_difficulty_distribution,
    get_unique_db_ids,
    load_bird_dev_examples,
    prepare_prompt_question,
    sample_examples,
)
from src.bird.query_executor import BirdQueryExecutor
from src.bird.metrics import (
    build_bird_comparison_row,
    compare_bird_results,
    normalize_sql,
    normalize_value,
    sql_similarity_score,
)
from src.bird.csv_reporter import (
    BirdCSVReporter,
    format_result_rows_markdown_table,
)

__all__ = [
    "load_bird_dev_examples",
    "prepare_prompt_question",
    "sample_examples",
    "get_unique_db_ids",
    "get_difficulty_distribution",
    "get_database_path",
    "get_database_uri",
    "BirdQueryExecutor",
    "compare_bird_results",
    "normalize_sql",
    "normalize_value",
    "sql_similarity_score",
    "build_bird_comparison_row",
    "BirdCSVReporter",
    "format_result_rows_markdown_table",
]
