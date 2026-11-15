"""
src/spider/__init__.py

Spider Benchmark Evaluation module.
Provides dataset loading, query execution, metrics evaluation, CSV reporting, and detailed execution logging.
"""

from src.spider.csv_reporter import CSVReporter
from src.spider.data_loader import (
    filter_by_db_id,
    get_unique_db_ids,
    load_spider_dev_examples,
    sample_examples,
)
from src.spider.logger import SpiderExecutionLogger
from src.spider.metrics import (
    build_comparison_row,
    compare_multi_pandas_table,
    compare_pandas_table,
    normalize_sql,
    results_exact_match,
    results_f1_score,
    sql_similarity_score,
)
from src.spider.query_executor import (
    BaseSpiderQueryExecutor,
    Spider2QueryExecutor,
    SpiderBigQueryExecutor,
    SpiderQueryExecutor,
    SpiderSQLiteExecutor,
    SpiderSnowflakeExecutor,
    SpiderSQLAlchemyExecutor,
    get_spider_executor,
    mask_db_url,
)

__all__ = [
    # Data loading
    "load_spider_dev_examples",
    "sample_examples",
    "filter_by_db_id",
    "get_unique_db_ids",
    # Query execution
    "BaseSpiderQueryExecutor",
    "SpiderSQLiteExecutor",
    "SpiderQueryExecutor",
    "Spider2QueryExecutor",
    "SpiderBigQueryExecutor",
    "SpiderSnowflakeExecutor",
    "SpiderSQLAlchemyExecutor",
    "get_spider_executor",
    "mask_db_url",
    # Metrics
    "normalize_sql",
    "sql_similarity_score",
    "compare_pandas_table",
    "compare_multi_pandas_table",
    "results_exact_match",
    "results_f1_score",
    "build_comparison_row",
    # Reporting & Logging
    "CSVReporter",
    "SpiderExecutionLogger",
]
