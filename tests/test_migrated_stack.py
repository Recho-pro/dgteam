from pathlib import Path

from dgteam.core.project_config import load_project_config
from dgteam.core.storage import DGTeamStorage
from dgteam.market.rules import load_rules
from dgteam.query_api.server import DEFAULT_DB_PATH, QueryApp


def test_project_config_defaults_to_dgteam_runtime_paths():
    config = load_project_config()
    assert config.paths.db_path.name == "dgteam.db"
    assert config.paths.rules_path.name == "default_rules.json"


def test_rules_load_from_dgteam_root():
    rules = load_rules()
    assert "crawler" in rules
    assert "cleaning" in rules
    assert "quality_engine" in rules


def test_query_app_reads_migrated_database():
    storage = DGTeamStorage(DEFAULT_DB_PATH)
    storage.init_db()
    payload = QueryApp(DEFAULT_DB_PATH).status_payload()
    assert payload["ok"] is True
    live = payload.get("live", {})
    assert isinstance(live, dict)
    assert isinstance(payload.get("hot_queries"), list)
    if live:
        assert str(live.get("run_key") or "").strip()
    else:
        assert payload.get("summary", {}).get("quote_count") == 0


def test_migrated_database_exists():
    assert Path(DEFAULT_DB_PATH).exists()
