import hashlib
from datetime import UTC, datetime, timedelta

from .auth import now
from .store import store


def allow(identity, limit, window=60):
    key = hashlib.sha256(identity.encode()).hexdigest()
    if hasattr(store, "rpc"):
        return store.rpc(
            "check_rate_limit", {"p_key": key, "p_limit": limit, "p_window": window}
        )
    with store.transaction() as tx:
        row = tx.get("rate_limits", id=key)
        expired = not row or datetime.fromisoformat(row["started_at"]) + timedelta(
            seconds=window
        ) < datetime.now(UTC)
        if expired:
            row = {"id": key, "started_at": now(), "count": 0}
        row["count"] += 1
        tx.put("rate_limits", row)
        return row["count"] <= limit
