import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from .auth import now
from .store import store


def today():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def visible(problem):
    from .config import PREVIEW

    return problem["status"] == "published" or (
        PREVIEW and problem["status"] == "draft"
    )


def problem_or_404(pid, admin=False):
    problem = store.get("problems", id=pid)
    if not problem or (not admin and not visible(problem)):
        raise HTTPException(404, "题目不存在或已下架。")
    return problem


def filtered(filters, repository=None):
    repository = repository or store
    rows = [p for p in repository.list("problems") if visible(p)]
    for key in ("year", "type"):
        values = filters.get(key)
        if values:
            values = values.split(",") if isinstance(values, str) else values
            rows = [p for p in rows if str(p.get(key)) in [str(v) for v in values]]
    if filters.get("tag"):
        tags = filters["tag"].split(",")
        rows = [p for p in rows if any(t in p["tags"] for t in tags)]
    if filters.get("q"):
        term = filters["q"].lower()
        rows = [
            p
            for p in rows
            if term in (p["title"] + " " + p["id"] + " " + " ".join(p["tags"])).lower()
        ]
    return sorted(rows, key=lambda p: (-p["year"], p["group_name"], p["id"]))


def choose(filters, exclude=None, resume=None):
    rows = filtered(filters)
    if not rows:
        raise HTTPException(404, "当前筛选下没有题目，请调整条件。")
    if resume and resume != exclude:
        previous = next((p for p in rows if p["id"] == resume), None)
        if previous:
            return previous
    others = [p for p in rows if p["id"] != exclude]
    return secrets.choice(others or rows)


def progress(user, pid, repository=None):
    repository = repository or store
    return (
        repository.get("user_problem_progress", user_id=user["id"], problem_id=pid)
        if user
        else None
    )


def apply_progress(user, problem, text, images, result, revision=None):
    """Serialize submit/self-assessment and daily check-in in one transaction."""
    if hasattr(store, "rpc"):
        try:
            return store.rpc(
                "record_progress",
                {
                    "p_user": user["id"],
                    "p_problem": problem["id"],
                    "p_text": text,
                    "p_images": images,
                    "p_result": result,
                    "p_revision": revision,
                    "p_version": problem["version"],
                    "p_date": today(),
                },
            )
        except Exception as exc:
            import httpx

            if isinstance(exc, httpx.HTTPStatusError) and "stale" in exc.response.text:
                raise HTTPException(
                    409, "作答或题目已更新，请刷新后重新核对。"
                ) from exc
            raise
    with store.transaction() as tx:
        latest = tx.get("problems", id=problem["id"])
        if latest["version"] != problem["version"]:
            raise HTTPException(409, "题目已更新，请刷新后重试。")
        old = tx.get(
            "user_problem_progress", user_id=user["id"], problem_id=problem["id"]
        )
        if revision is not None:
            if (
                not old
                or old["revision"] != revision
                or old["problem_version"] != problem["version"]
                or old["result"] != "pending"
            ):
                raise HTTPException(409, "作答已更新或已自评，请刷新后重新核对。")
            text, images = old["text"], old["image_ids"]
        record = {
            "id": user["id"] + ":" + problem["id"],
            "user_id": user["id"],
            "problem_id": problem["id"],
            "result": result,
            "result_source": "automatic"
            if problem["grading_mode"] == "choice"
            else "self_assessment",
            "ever_correct": bool(old and old["ever_correct"]) or result == "correct",
            "ever_incorrect": bool(old and old["ever_incorrect"])
            or result == "incorrect",
            "correct_version": problem["version"]
            if result == "correct"
            else old.get("correct_version")
            if old
            else None,
            "revision": revision
            if revision is not None
            else (old["revision"] if old else 0) + 1,
            "problem_version": problem["version"],
            "text": text,
            "image_ids": images,
            "last_at": now(),
        }
        tx.put("user_problem_progress", record)
        daily = tx.get("daily_assignments", user_id=user["id"], local_date=today())
        if (
            result != "pending"
            and daily
            and daily["problem_id"] == problem["id"]
            and not daily["completed_at"]
        ):
            daily["completed_at"] = now()
            tx.put("daily_assignments", daily)
        return record


def assign_daily(user, filters):
    existing = store.get("daily_assignments", user_id=user["id"], local_date=today())
    if existing:
        return existing
    problem = choose(filters)
    record = {
        "id": user["id"] + ":" + today(),
        "user_id": user["id"],
        "local_date": today(),
        "problem_id": problem["id"],
        "filters": filters,
        "completed_at": None,
    }
    if hasattr(store, "rpc"):
        return store.rpc("assign_daily", {"p_record": record})
    with store.transaction() as tx:
        existing = tx.get("daily_assignments", user_id=user["id"], local_date=today())
        return existing or tx.put("daily_assignments", record)
