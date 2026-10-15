from pathlib import Path
import json
import hashlib
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "data/math2_2010_2026"
BASE = ROOT / "devided-math/考研数学/考研数学 (二) 真题及答案解析（1987-2026）"
data = json.loads((RUN / "manifest.json").read_text("utf8"))
errors = []
stats = {"questions": len(data["questions"]), "stem_images": 0, "answer_images": 0}

for year in sorted({q["year"] for q in data["questions"]}):
    qs = [q for q in data["questions"] if q["year"] == year]
    expected = 23 if year < 2021 else 22
    if sorted(q["number"] for q in qs) != list(range(1, expected + 1)):
        errors.append([year, "numbers", "not contiguous"])
    for q in qs:
        for role in ("stem", "answer"):
            if not q[role]:
                errors.append([q["id"], role, "empty"])
            for i, r in enumerate(q[role], 1):
                if r.get("order") != i:
                    errors.append([q["id"], role, "order"])
                p = ROOT / r["image"]
                try:
                    with Image.open(p) as im:
                        if im.width < 10 or im.height < 10:
                            errors.append([q["id"], role, "small"])
                    if hashlib.sha256(p.read_bytes()).hexdigest() != r["image_sha256"]:
                        errors.append([q["id"], role, "hash"])
                except Exception as exc:
                    errors.append([q["id"], role, str(exc)])
                stats["stem_images" if role == "stem" else "answer_images"] += 1

for s in data["sources"]:
    p = ROOT / s["path"]
    original = ROOT / s["original_path"]
    if not p.exists() or not original.exists():
        errors.append([s["id"], "source", "missing/hash"])
    elif hashlib.sha256(original.read_bytes()).hexdigest() != s["original_sha256"]:
        errors.append([s["id"], "source", "original hash"])

result = {
    "stats": stats,
    "errors": errors,
    "visual_review_status": "partial_manual_review; not mathematical answer validation",
    "excluded": data.get("excluded", []),
}
(RUN / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
print(json.dumps(result, ensure_ascii=False))
