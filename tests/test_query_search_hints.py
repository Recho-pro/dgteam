from __future__ import annotations

from dgteam.query_api.server import prepare_query_context


def test_prepare_query_context_detects_lenovo_laptop_brand_hints():
    context = prepare_query_context("联想 小新14 SE 512G")

    assert "laptop" in context.category_intents
    assert {"联想", "联想电脑"} & context.brand_hints


def test_prepare_query_context_detects_lenovo_brand_from_xiaoxin_only():
    context = prepare_query_context("小新14 SE")

    assert "laptop" in context.category_intents
    assert {"联想", "联想电脑"} & context.brand_hints
