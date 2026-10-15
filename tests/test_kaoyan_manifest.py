"""Offline manifest checks. Run with unittest; no app/store initialization needed."""
import hashlib
import json
import sys
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.models import ProblemRecord
from scripts.build_kaoyan_manifest import parse_part


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


class KaoyanManifestTests(unittest.TestCase):
    def test_fragment_order(self):
        names = ["21下", "21中2", "21上", "21中1"]
        self.assertEqual(sorted(names, key=parse_part), ["21上", "21中1", "21中2", "21下"])
        with self.assertRaises(ValueError):
            parse_part("21未命名")

    def test_manifests_and_complete_inventory(self):
        pending = load("data/kaoyan/pending.json")["problems"]
        keys = load("data/kaoyan-choice-answers.json")
        all_ids, all_images = set(), set()
        for bank in (1, 2, 3):
            manifest = load(f"data/kaoyan/math{bank}-manifest.json")
            self.assertEqual(manifest["schema_version"], 1)
            sources = {s["id"] for s in manifest["sources"]}
            self.assertEqual(len(sources), 17)
            assets = {a["id"]: a for a in manifest["assets"]}
            self.assertEqual(len(assets), len(manifest["assets"]))
            references = set()
            for raw in manifest["problems"]:
                p = ProblemRecord.model_validate(raw)
                self.assertNotIn(p.id, all_ids)
                all_ids.add(p.id)
                self.assertIn(p.source_id, sources)
                self.assertEqual(p.status, "published")
                self.assertTrue(p.answer.images)
                n = int(p.id.rsplit("-", 1)[1])
                choice_end, blank_end = (8, 14) if p.year < 2021 else (10, 16)
                self.assertEqual(p.type, "choice" if n <= choice_end else "blank" if n <= blank_end else "solution")
                if p.type == "choice":
                    self.assertEqual(p.answer.correct_options, [keys[str(bank)][str(p.year)].replace(" ", "")[n - 1]])
                    self.assertNotIn(p.id, keys["conflicts"])
                for aid in p.question_images + p.answer.images:
                    self.assertEqual(assets[aid]["problem_id"], p.id)
                    references.add(aid)
                for aid in p.question_images:
                    self.assertEqual(assets[aid]["purpose"], "question")
                for aid in p.answer.images:
                    self.assertEqual(assets[aid]["purpose"], "answer")
            self.assertEqual(references, set(assets))
            for a in assets.values():
                path = ROOT / a["source_path"]
                self.assertTrue(path.resolve().is_relative_to(ROOT / "devided-math"))
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest() + ".png", a["path"])
                with Image.open(path) as im:
                    self.assertEqual(im.size, (a["width"], a["height"]))
                    im.verify()
                self.assertNotIn(a["source_path"], all_images)
                all_images.add(a["source_path"])
        for p in pending:
            self.assertNotIn(p["id"], all_ids)
            all_ids.add(p["id"])
            self.assertTrue(p["reasons"])
            for path in p["question"] + p["answer"]:
                self.assertNotIn(path, all_images)
                all_images.add(path)
        expected = {f"kaoyan-math{b}-{y}-{n:02d}" for b in (1, 2, 3)
                    for y in range(2010, 2027) for n in range(1, 24 if y < 2021 else 23)}
        self.assertEqual(all_ids, expected)
        screenshots = {p.relative_to(ROOT).as_posix() for p in (ROOT / "devided-math/考研数学").rglob("*.png")
                       if p.parent.name in ("题目", "答案") and p.parent.parent.name in {str(y) for y in range(2010, 2027)}}
        self.assertEqual(all_images, screenshots)


if __name__ == "__main__":
    unittest.main()
