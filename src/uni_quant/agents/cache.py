"""Decision cache — avoid re-calling LLM for the same (symbol, date, role) tuple.

Stores per-stock per-day agent outputs as JSON. TTL controlled by config.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def _content_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


class DecisionCache:
    """JSON-on-disk cache keyed by (symbol, as_of_date, role, content_hash)."""

    def __init__(self, root: str | Path = "data/agents_cache", ttl_days: int = 7):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_days = ttl_days

    def _path(self, symbol: str, as_of: str, role: str, payload_hash: str) -> Path:
        return self.root / symbol / f"{as_of}_{role}_{payload_hash}.json"

    def get(self, symbol: str, as_of: str, role: str, payload: str) -> dict | None:
        h = _content_hash(payload)
        p = self._path(symbol, as_of, role, h)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text())
            cached_at = datetime.fromisoformat(data["_cached_at"])
            if datetime.now() - cached_at > timedelta(days=self.ttl_days):
                return None
            return data.get("result")
        except Exception:
            return None

    def put(self, symbol: str, as_of: str, role: str, payload: str, result: Any) -> None:
        h = _content_hash(payload)
        p = self._path(symbol, as_of, role, h)
        p.parent.mkdir(parents=True, exist_ok=True)
        serializable = asdict(result) if is_dataclass(result) else result
        p.write_text(json.dumps({
            "_cached_at": datetime.now().isoformat(timespec="seconds"),
            "symbol": symbol, "as_of": as_of, "role": role,
            "payload_hash": h,
            "result": serializable,
        }, ensure_ascii=False, indent=2))
