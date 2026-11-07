"""Read-only snapshot, verification and text-only restore helpers.

This module is used by the production migration scripts. It never runs DDL and
never touches images, versions, learning records or answer permissions.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from . import config
from .store import store


def project_ref() -> str | None:
    host = urlparse(config.SB_URL).hostname or ""
    if host.endswith(".supabase.co"):
        return host[: -len(".supabase.co")]
    return host or None


def snapshot_text(repository=None) -> dict:
    """Capture the current text plus the fields that must not change."""
    repository = repository or store
    problems = {p["id"]: p for p in repository.list("problems")}
    answers = {a["problem_id"]: a for a in repository.list("problem_answers")}
    rows = []
    for problem_id in sorted(problems):
        problem = problems[problem_id]
        answer = answers.get(problem_id, {})
        rows.append(
            {
                "id": problem_id,
                "version": problem.get("version"),
                "updated_at": problem.get("updated_at"),
                "manual_revision": bool(problem.get("manual_revision")),
                "question_images": problem.get("question_images", []),
                "question_text": problem.get("question_text", ""),
                "answer_images": answer.get("images", []),
                "answer_text": answer.get("text", ""),
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": config.MODE,
        "project_ref": project_ref(),
        "problems": rows,
    }


def write_snapshot(path: Path, *, overwrite: bool = False, repository=None) -> Path:
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot_text(repository), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def restore_text(backup: dict, *, dry_run: bool = True, repository=None) -> dict:
    """Restore only question_text/answer_text from a snapshot."""
    repository = repository or store
    current = {p["id"] for p in repository.list("problems")}
    items, missing = [], []
    for row in backup.get("problems", []):
        if row["id"] not in current:
            missing.append(row["id"])
            continue
        items.append(
            {
                "id": row["id"],
                "question_text": row.get("question_text", ""),
                "answer_text": row.get("answer_text", ""),
            }
        )
    result = {
        "updated": 0 if dry_run else len(items),
        "planned": len(items),
        "missing": missing,
        "dry_run": dry_run,
    }
    if dry_run:
        return result
    updated = 0
    for start in range(0, len(items), 200):
        updated += repository.apply_problem_texts(items[start : start + 200])
    result["updated"] = updated
    return result


def _text_counts(rows: list[dict], key: str) -> int:
    return sum(bool(row.get(key)) for row in rows)


def verify_text(
    manifest: dict,
    *,
    backup: dict | None = None,
    strict: bool = False,
    repository=None,
) -> dict:
    """Compare the database with the generated text manifest.

    Missing rows, inline-assets failures, version changes and inaccessible
    storage make ``ok`` false. Text/image differences are reported and only
    make ``ok`` false under ``strict`` because administrators may have edited
    them intentionally after the backup was taken.
    """
    repository = repository or store
    problems = {p["id"]: p for p in repository.list("problems")}
    answers = {a["problem_id"]: a for a in repository.list("problem_answers")}
    media = {a["id"]: a for a in repository.list("media_assets")}
    columns_present = not problems or "question_text" in next(iter(problems.values()))
    missing: list[str] = []
    text_mismatch: list[dict] = []
    image_mismatch: list[dict] = []
    for expected in manifest["problems"]:
        problem_id = expected["id"]
        current = problems.get(problem_id)
        if not current:
            missing.append(problem_id)
            continue
        answer = answers.get(problem_id, {})
        actual_question = current.get("question_text", "")
        actual_answer = answer.get("text", "")
        if actual_question != expected.get("question_text", ""):
            text_mismatch.append(
                {
                    "id": problem_id,
                    "field": "question",
                    "expected_nonempty": bool(expected.get("question_text")),
                    "actual_nonempty": bool(actual_question),
                }
            )
        if actual_answer != expected["answer"].get("text", ""):
            text_mismatch.append(
                {
                    "id": problem_id,
                    "field": "answer",
                    "expected_nonempty": bool(expected["answer"].get("text")),
                    "actual_nonempty": bool(actual_answer),
                }
            )
        if current.get("question_images") != expected["question_images"]:
            image_mismatch.append({"id": problem_id, "field": "question"})
        if answer.get("images") != expected["answer"]["images"]:
            image_mismatch.append({"id": problem_id, "field": "answer"})
    inline_assets = [
        asset
        for asset in manifest.get("assets", [])
        if asset.get("source_path", "").startswith("devided-math-text/")
    ]
    inline_missing = [
        asset["id"]
        for asset in inline_assets
        if asset["id"] not in media
        or media[asset["id"]].get("path") != asset["path"]
        or media[asset["id"]].get("problem_id") != asset["problem_id"]
    ]
    storage = {
        "checked": bool(inline_assets),
        "ok": True,
        "error": "",
    }
    if inline_assets:
        paths = sorted({asset["path"] for asset in inline_assets})
        if config.MODE == "local":
            missing_files = [
                path
                for path in paths
                if not (config.ROOT / "data/assets" / path).is_file()
            ]
            storage["ok"] = not missing_files
            storage["missing"] = missing_files
        else:
            try:
                signed = repository.request(
                    "POST",
                    f"/storage/v1/object/sign/{config.BUCKET}",
                    json={"paths": paths, "expiresIn": 60},
                )
                failed = [
                    row.get("path") or row.get("error")
                    for row in signed
                    if row.get("error")
                ]
                storage["ok"] = not failed
                storage["missing"] = failed
            except Exception as exc:  # noqa: BLE001 - report, do not crash verify
                storage["ok"] = False
                storage["error"] = str(exc)
    version_changes: list[dict] = []
    backup_project = backup.get("project_ref") if backup else None
    project_mismatch = bool(
        backup_project and project_ref() and backup_project != project_ref()
    )
    if backup:
        previous = {row["id"]: row for row in backup.get("problems", [])}
        for problem_id, current in problems.items():
            old = previous.get(problem_id)
            if not old:
                continue
            if old.get("version") != current.get("version"):
                version_changes.append(
                    {
                        "id": problem_id,
                        "old": old.get("version"),
                        "new": current.get("version"),
                    }
                )
            elif old.get("updated_at") != current.get("updated_at"):
                version_changes.append(
                    {
                        "id": problem_id,
                        "field": "updated_at",
                        "old": old.get("updated_at"),
                        "new": current.get("updated_at"),
                    }
                )
    expected_question = sum(
        bool(problem.get("question_text")) for problem in manifest["problems"]
    )
    expected_answer = sum(
        bool(problem["answer"].get("text")) for problem in manifest["problems"]
    )
    actual_rows = [
        {
            "id": expected["id"],
            "question_text": problems[expected["id"]].get("question_text", ""),
            "answer_text": answers.get(expected["id"], {}).get("text", ""),
        }
        for expected in manifest["problems"]
        if expected["id"] in problems
    ]
    ok = (
        columns_present
        and not missing
        and not inline_missing
        and not version_changes
        and not project_mismatch
        and storage["ok"]
        and (not strict or not text_mismatch)
        and (not strict or not image_mismatch)
    )
    return {
        "ok": ok,
        "strict": strict,
        "mode": config.MODE,
        "project_ref": project_ref(),
        "columns_present": columns_present,
        "expected": {
            "problems": len(manifest["problems"]),
            "question_text": expected_question,
            "answer_text": expected_answer,
            "inline_assets": len(inline_assets),
        },
        "actual": {
            "problems": len(problems),
            "question_text": _text_counts(actual_rows, "question_text"),
            "answer_text": _text_counts(actual_rows, "answer_text"),
        },
        "missing": missing,
        "text_mismatch": text_mismatch,
        "image_mismatch": image_mismatch,
        "inline_missing": inline_missing,
        "storage": storage,
        "project_mismatch": project_mismatch,
        "version_changes": version_changes,
    }
