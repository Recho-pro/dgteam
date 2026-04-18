from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from dgteam.core.project_config import load_project_config
from dgteam.query_api.contracts import backend_owned_logic_payload, endpoint_contracts_payload
from dgteam.query_api.server import QueryApp


class QueryService:
    def __init__(self, db_path: Path | None = None):
        resolved_db_path = Path(db_path or load_project_config().paths.db_path).expanduser().resolve()
        self.app = QueryApp(resolved_db_path)

    def status_payload(self) -> Dict[str, Any]:
        return self.app.status_payload()

    def search(self, query: str, *, limit: int = 6) -> Dict[str, Any]:
        return self.app.search_payload(query, limit=limit)

    def snapshot(self, **kwargs: Any) -> Dict[str, Any]:
        return self.app.snapshot_payload(**kwargs)

    def endpoint_contracts(self) -> Dict[str, Any]:
        return endpoint_contracts_payload()

    def backend_owned_logic(self) -> Dict[str, Any]:
        return backend_owned_logic_payload()
