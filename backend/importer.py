import hashlib

from . import config
from .auth import now
from .models import ProblemRecord
from .store import store

IMAGE_SOURCE_ROOT = "devided-math"
TEXT_SOURCE_ROOT = "devided-math-text"


def _source_roots():
    return (
        (config.ROOT / IMAGE_SOURCE_ROOT).resolve(),
        (config.ROOT / TEXT_SOURCE_ROOT).resolve(),
    )


def asset_source(asset):
    """Validate an asset source path and its content-addressed target name."""
    source = (config.ROOT / asset["source_path"]).resolve()
    if not any(source.is_relative_to(root) for root in _source_roots()):
        raise ValueError("Image source is invalid: " + asset["id"])
    if not source.is_file():
        raise ValueError("Image source is invalid: " + asset["id"])
    expected = hashlib.sha256(source.read_bytes()).hexdigest() + source.suffix.lower()
    if expected != asset["path"]:
        raise ValueError(
            "Image checksum changed; create a revised manifest: " + asset["id"]
        )
    return source


def _prepare_manifest(manifest):
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported manifest schema_version")
    manifest = dict(manifest)
    raw_problems = manifest["problems"]
    provided = {
        p.get("id"): (
            "question_text" in p,
            "text" in (p.get("answer") or {}),
        )
        for p in raw_problems
        if isinstance(p, dict)
    }
    manifest["problems"] = [
        ProblemRecord.model_validate(
            {
                k: v
                for k, v in p.items()
                if k not in ("knowledge_points", "knowledge_source")
            }
        ).model_dump(exclude_none=False)
        for p in raw_problems
    ]
    ids = [p["id"] for p in manifest["problems"]]
    # Old generated manifests must not turn the initial bank back into drafts.
    for p in manifest["problems"]:
        if (
            p["id"].startswith("cmc-nonmath-")
            and not p["manual_revision"]
            and p["status"] == "draft"
            and p["answer"]["images"]
        ):
            p["status"] = "published"
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate problem IDs")
    sources = {s["id"]: s for s in manifest["sources"]}
    known = {a["id"] for a in manifest.get("assets", [])} | {
        a["id"] for a in store.list("media_assets")
    }
    for p in manifest["problems"]:
        source = store.get("problem_sources", id=p["source_id"]) or sources.get(
            p["source_id"]
        )
        if not source:
            raise ValueError("Missing source: " + p["id"])
        if (
            not p["question_images"]
            or not set(p["question_images"] + p["answer"]["images"]) <= known
        ):
            raise ValueError("Missing image references: " + p["id"])
        if p["status"] == "published" and not p["answer"]["images"]:
            raise ValueError("Published problems need answer images: " + p["id"])
    for a in manifest.get("assets", []):
        asset_source(a)
    return manifest, provided


def _upload_assets(assets, existing_ids=None):
    existing_ids = (
        existing_ids
        if existing_ids is not None
        else {a["id"] for a in store.list("media_assets")}
    )
    for a in assets:
        if a["id"] in existing_ids:
            continue
        source = asset_source(a)
        raw = source.read_bytes()
        if config.MODE == "local":
            target = config.ROOT / "data/assets" / a["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(raw)
        else:
            store.request(
                "POST",
                f"/storage/v1/object/{config.BUCKET}/{a['path']}",
                headers={"Content-Type": a["content_type"], "x-upsert": "true"},
                content=raw,
            )


def _ensure_asset_rows(assets, existing_ids=None):
    existing_ids = (
        existing_ids
        if existing_ids is not None
        else {a["id"] for a in store.list("media_assets")}
    )
    added = 0
    for a in assets:
        if a["id"] not in existing_ids:
            store.put("media_assets", {**a, "created_at": now()})
            existing_ids.add(a["id"])
            added += 1
    return added


def import_manifest(manifest, override_manual=False):
    manifest, provided = _prepare_manifest(manifest)
    counts = {"created": 0, "updated": 0, "unchanged": 0, "protected": 0}
    for source in manifest["sources"]:
        existing = store.get("problem_sources", id=source["id"])
        if not existing:
            store.put("problem_sources", source)
    # Upload content-addressed objects before database references, preserving old assets.
    existing_assets = {a["id"] for a in store.list("media_assets")}
    _upload_assets(manifest.get("assets", []), existing_assets)
    for p in manifest["problems"]:
        p = dict(p)
        answer = p.pop("answer")
        existing = store.get("problems", id=p["id"])
        old_answer = store.get("problem_answers", problem_id=p["id"])
        # An old image manifest has no text fields. Never erase text that is
        # already in the database just because the caller used an old file.
        has_question_text, has_answer_text = provided.get(p["id"], (False, False))
        if existing and not has_question_text:
            p["question_text"] = existing.get("question_text", "")
        if old_answer and not has_answer_text:
            answer["text"] = old_answer.get("text", "")
        if existing and existing["manual_revision"] and not override_manual:
            counts["protected"] += 1
            continue
        if existing:
            # difficulty is internal metadata owned by the tagging job now.
            # A manifest (which always normalizes it to None) must not wipe it
            # or bump the version just because the numbers differ.
            p["difficulty"] = existing.get("difficulty")
        compare = {
            k: v
            for k, v in p.items()
            if k not in ("version", "manual_revision", "updated_at", "difficulty")
        }
        if (
            existing
            and all(existing.get(k) == v for k, v in compare.items())
            and old_answer
            and all(old_answer.get(k) == v for k, v in answer.items())
        ):
            counts["unchanged"] += 1
            continue
        p["version"] = (existing["version"] + 1) if existing else 1
        p["updated_at"] = now()
        answer = {"problem_id": p["id"], **answer}
        if config.MODE == "local":
            with store.transaction() as tx:
                tx.put("problems", p)
                tx.put("problem_answers", answer)
        else:
            store.rpc(
                "save_problem",
                {
                    "p_problem": p,
                    "p_answer": answer,
                    "p_expected": existing["version"] if existing else 0,
                },
            )
        counts["updated" if existing else "created"] += 1
    _ensure_asset_rows(manifest.get("assets", []), existing_assets)
    return counts


def apply_text_manifest(manifest, only_empty=True, dry_run=False):
    """Backfill text columns without touching versions, images or history.

    This is deliberately separate from import_manifest: the production bank
    already exists, so adding a rendering layer must not look like a content
    revision to students or to the answer-unlock/version checks.
    """
    manifest, _ = _prepare_manifest(manifest)
    updates = []
    missing = []
    image_mismatch = []
    skipped = 0
    existing_text = {}
    # One bulk read per table: the production bank is ~1,900 rows, so per-row
    # requests would be needlessly slow and could hit platform rate limits.
    problems_by_id = {p["id"]: p for p in store.list("problems")}
    answers_by_id = {
        a["problem_id"]: a for a in store.list("problem_answers")
    }
    for p in manifest["problems"]:
        existing = problems_by_id.get(p["id"])
        if not existing:
            missing.append(p["id"])
            continue
        old_answer = answers_by_id.get(p["id"]) or {}
        # Never attach text derived from an old scan to a problem whose images
        # were already replaced by an administrator.
        if existing.get("question_images") != p["question_images"] or old_answer.get(
            "images"
        ) != p["answer"]["images"]:
            image_mismatch.append(p["id"])
            continue
        question_text = p.get("question_text", "")
        answer_text = p["answer"].get("text", "")
        if only_empty and existing.get("question_text"):
            question_text = existing["question_text"]
        if only_empty and old_answer.get("text"):
            answer_text = old_answer["text"]
        if (
            question_text == existing.get("question_text", "")
            and answer_text == old_answer.get("text", "")
        ):
            skipped += 1
            continue
        existing_text[p["id"]] = (question_text, answer_text)
        updates.append(
            {
                "id": p["id"],
                "question_text": question_text,
                "answer_text": answer_text,
            }
        )
    result = {
        "updated": len(updates),
        "skipped": skipped,
        "missing": missing,
        "image_mismatch": len(image_mismatch),
        "assets_added": 0,
        "dry_run": dry_run,
    }
    if dry_run or not updates:
        return result
    # Upload only assets from problems that actually receive text. Existing
    # image assets are skipped by their stored ids and are never rewritten.
    updated_ids = set(existing_text)
    assets = [
        a for a in manifest.get("assets", []) if a.get("problem_id") in updated_ids
    ]
    existing_assets = {a["id"] for a in store.list("media_assets")}
    _upload_assets(assets, existing_assets)
    result["assets_added"] = _ensure_asset_rows(assets, existing_assets)
    updated = 0
    for start in range(0, len(updates), 200):
        updated += store.apply_problem_texts(updates[start : start + 200])
    result["updated"] = updated
    return result
