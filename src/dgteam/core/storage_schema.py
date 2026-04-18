from __future__ import annotations


CORE_TABLE_COLUMNS: dict[str, set[str]] = {
    "runs": {
        "run_key",
        "source_dir",
        "started_at",
        "finished_at",
        "status",
        "summary_json",
        "created_at",
    },
    "tasks": {
        "run_key",
        "task_key",
        "event_time",
        "status",
        "code",
        "msg",
        "error_text",
        "total_rows_seen",
        "row_count",
        "payload_json",
    },
    "quote_rows": {
        "id",
        "run_key",
        "task_key",
        "brand_title",
        "series_title",
        "model_title",
        "group_title",
        "condition_bucket",
        "price_text",
        "imported_at",
    },
    "market_snapshots": {
        "run_key",
        "brand_title",
        "series_title",
        "model_title",
        "group_title",
        "condition_bucket",
        "market_price",
        "source_row_count",
        "published_at",
        "search_text",
        "search_text_normalized",
    },
    "app_state": {
        "state_key",
        "state_value",
        "updated_at",
    },
}


HOT_QUERY_INDEX_COLUMNS: dict[str, list[str]] = {
    "idx_tasks_status": ["run_key", "status"],
    "idx_quote_rows_sku_lookup": [
        "run_key",
        "brand_title",
        "series_title",
        "model_title",
        "group_title",
        "condition_bucket",
    ],
    "idx_quote_rows_family_lookup": [
        "run_key",
        "brand_title",
        "series_title",
        "model_title",
        "condition_bucket",
    ],
    "idx_quote_rows_run_catalog": [
        "run_key",
        "brand_title",
        "series_title",
        "model_title",
        "group_title",
        "condition_bucket",
    ],
    "idx_market_snapshots_run_query": [
        "run_key",
        "brand_title",
        "series_title",
        "model_title",
    ],
    "idx_market_snapshots_run_sort": [
        "run_key",
        "source_row_count",
        "latest_imported_at",
    ],
}
