from __future__ import annotations

from copy import deepcopy

from dgteam.query_api.snapshot_refinement import refine_snapshot


def make_snapshot() -> dict[str, object]:
    return {
        "ok": True,
        "contract_version": "query-ui.v2",
        "header": {"title": "iPhone 17 Pro Max"},
        "resolution": {"contract_version": "sku-detail.v2"},
        "branches": [
            {
                "branch_title": "iPhone 17 Pro Max",
                "capacity_groups": [
                    {
                        "capacity_label": "256G",
                        "price_range": "7000-7200",
                        "colors": [
                            {"color_label": "黑色", "price_range": "7000-7100"},
                            {"color_label": "白色", "price_range": "7100-7200"},
                        ],
                    },
                    {
                        "capacity_label": "512G",
                        "price_range": "7900-8200",
                        "colors": [
                            {"color_label": "黑色", "price_range": "7900-8000"},
                            {"color_label": "白色", "price_range": "8050-8200"},
                        ],
                    },
                ],
            },
            {
                "branch_title": "iPhone 17 Pro",
                "capacity_groups": [
                    {
                        "capacity_label": "256G",
                        "price_range": "6500-6800",
                        "colors": [
                            {"color_label": "黑色", "price_range": "6500-6600"},
                        ],
                    }
                ],
            },
        ],
    }


def test_refine_snapshot_filters_capacity_and_color_from_backend_contract() -> None:
    outcome = refine_snapshot(make_snapshot(), "512 白色")

    assert outcome.applied is True
    assert outcome.reason == "applied"
    assert outcome.summary == "512 · 白色"
    assert outcome.matched_branches == 1
    assert outcome.matched_capacity_groups == 1
    assert outcome.matched_colors == 1
    branch = outcome.snapshot["branches"][0]
    assert branch["branch_title"] == "iPhone 17 Pro Max"
    group = branch["capacity_groups"][0]
    assert group["capacity_label"] == "512G"
    assert group["colors"][0]["color_label"] == "白色"
    assert group["colors"][0]["__matched"] is True


def test_refine_snapshot_rejects_global_model_search_queries() -> None:
    original = make_snapshot()
    outcome = refine_snapshot(original, "iPhone 17 Pro Max")

    assert outcome.applied is False
    assert outcome.reason == "not_applicable"
    assert outcome.summary == ""
    assert outcome.snapshot["branches"] == original["branches"]


def test_refine_snapshot_returns_original_snapshot_when_no_match_exists() -> None:
    original = make_snapshot()
    expected = deepcopy(original["branches"])

    outcome = refine_snapshot(original, "1TB 金色")

    assert outcome.applied is False
    assert outcome.reason == "no_match"
    assert outcome.snapshot["branches"] == expected


def test_refine_snapshot_filters_keyword_variant_subset_and_drops_component_case() -> None:
    snapshot = {
        "ok": True,
        "header": {"title": "Air Pods 4"},
        "resolution": {"contract_version": "sku-detail.v2"},
        "branches": [
            {
                "branch_title": "Air Pods 4",
                "capacity_groups": [
                    {
                        "capacity_label": "default",
                        "price_range": "755-1085",
                        "colors": [
                            {"color_label": "Standard White P63", "price_range": "755-758"},
                            {"color_label": "Noise Cancel White P93", "price_range": "1084-1085"},
                            {"color_label": "Noise Cancel P93 Charging Case", "price_range": "420-450"},
                        ],
                    }
                ],
            }
        ],
    }

    outcome = refine_snapshot(snapshot, "noise cancel")

    assert outcome.applied is True
    assert outcome.matched_colors == 1
    group = outcome.snapshot["branches"][0]["capacity_groups"][0]
    assert [item["color_label"] for item in group["colors"]] == ["Noise Cancel White P93"]
    assert group["price_range"] == "1084-1085"


def test_refine_snapshot_can_match_capacity_inside_variant_titles() -> None:
    snapshot = {
        "ok": True,
        "header": {"title": "Laptop 14"},
        "resolution": {"contract_version": "sku-detail.v2"},
        "branches": [
            {
                "branch_title": "Laptop 14",
                "capacity_groups": [
                    {
                        "capacity_label": "16G",
                        "price_range": "3700-5200",
                        "colors": [
                            {"group_title": "R7 16G 512G Gray", "price_range": "3700-3720"},
                            {"group_title": "i7 16G 1T Gray", "price_range": "5100-5200"},
                        ],
                    }
                ],
            }
        ],
    }

    outcome = refine_snapshot(snapshot, "512")

    assert outcome.applied is True
    assert outcome.matched_capacity_groups == 1
    group = outcome.snapshot["branches"][0]["capacity_groups"][0]
    assert [item["group_title"] for item in group["colors"]] == ["R7 16G 512G Gray"]
    assert group["price_range"] == "3700-3720"
