import hashlib

from . import config
from .auth import now
from .models import ProblemRecord
from .store import store


def import_manifest(manifest, override_manual=False):
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported manifest schema_version")
    manifest = dict(manifest)
    manifest["problems"] = [
        ProblemRecord.model_validate(p).model_dump(exclude_none=False)
        for p in manifest["problems"]
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
    known = {a["id"] for a in manifest.get("assets", [])} | {
        a["id"] for a in store.list("media_assets")
    }
    sources = {s["id"]: s for s in manifest["sources"]}
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
        source = (config.ROOT / a["source_path"]).resolve()
        if (
            not source.is_relative_to(config.ROOT / "devided-math")
            or not source.is_file()
        ):
            raise ValueError("Image source is invalid: " + a["id"])
        if hashlib.sha256(source.read_bytes()).hexdigest() + ".png" != a["path"]:
            raise ValueError(
                "Image checksum changed; create a revised manifest: " + a["id"]
            )
    counts = {"created": 0, "updated": 0, "unchanged": 0, "protected": 0}
    for source in manifest["sources"]:
        existing = store.get("problem_sources", id=source["id"])
        if not existing:
            store.put("problem_sources", source)
    # Upload content-addressed objects before database references, preserving old assets.
    for a in manifest.get("assets", []):
        if store.get("media_assets", id=a["id"]):
            continue
        raw = (config.ROOT / a["source_path"]).read_bytes()
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
    # Insert asset rows after problems exist (foreign keys).
    for p in manifest["problems"]:
        p = dict(p)
        answer = p.pop("answer")
        existing = store.get("problems", id=p["id"])
        if existing and existing["manual_revision"] and not override_manual:
            counts["protected"] += 1
            continue
        old_answer = store.get("problem_answers", problem_id=p["id"])
        compare = {
            k: v
            for k, v in p.items()
            if k not in ("version", "manual_revision", "updated_at")
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
    for a in manifest.get("assets", []):
        if not store.get("media_assets", id=a["id"]):
            store.put("media_assets", {**a, "created_at": now()})
    return counts
