"""Offline checks; importing this module never initializes the application/store."""

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.models import ProblemRecord
from scripts.build_bjtu_final_manifest import BASE, build, choice_keys, parse_name


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


class BjtuFinalManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load("data/bjtu-final-config.json")
        cls.manifests = {code: load(f"data/bjtu-finals/{code}-manifest.json")
                         for code in cls.config["courses"]}
        cls.pending = load("data/bjtu-finals/pending.json")["problems"]
        cls.inventory = load("data/bjtu-finals/inventory.json")
        cls.problems = {p["id"]: p for m in cls.manifests.values() for p in m["problems"]}

    def test_numbering_and_fragment_order(self):
        self.assertEqual(parse_name("二11"), ((2, 11), 0, None))
        self.assertEqual(parse_name("二1下"), ((2, 1), 2, "下"))
        self.assertEqual(parse_name("1(10)"), ((1, 10), 0, None))
        self.assertEqual(parse_name("六上"), ((6, None), 0, "上"))
        self.assertEqual(parse_name("12"), ((12, None), 0, None))
        with self.assertRaises(ValueError):
            parse_name("二1未命名")

    def test_choice_keys_reject_ambiguous_configuration(self):
        self.assertEqual(choice_keys({"numbers": [1, 2], "choice_keys": ["AB", "D"]}),
                         {1: "AB", 2: "D"})
        for numbers, keys in [([1], "AB"), ([1, 1], "AB"), ([1], ["AA"]),
                              ([1], ["A?"]), ([1], [""]), ([1], ["E"])]:
            with self.assertRaises(ValueError):
                choice_keys({"numbers": numbers, "choice_keys": keys})

    def test_schema_and_asset_integrity(self):
        ids, paths = set(), set()
        for code, manifest in self.manifests.items():
            self.assertEqual(manifest["schema_version"], 1)
            sources = {s["id"] for s in manifest["sources"]}
            self.assertEqual(len(sources), len(self.config["courses"][code]["exams"]))
            assets = {a["id"]: a for a in manifest["assets"]}
            self.assertEqual(len(assets), len(manifest["assets"]))
            used = set()
            for raw in manifest["problems"]:
                p = ProblemRecord.model_validate(raw)
                self.assertNotIn(p.id, ids)
                ids.add(p.id)
                self.assertIn(p.source_id, sources)
                self.assertEqual(p.status, "published")
                self.assertTrue(p.answer.images)
                self.assertEqual(p.tags, [f"北交期末·{self.config['courses'][code]['name']}"])
                for role, refs in (("question", p.question_images), ("answer", p.answer.images)):
                    for aid in refs:
                        self.assertEqual(assets[aid]["problem_id"], p.id)
                        self.assertEqual(assets[aid]["purpose"], role)
                        used.add(aid)
                if p.type == "choice":
                    self.assertNotIn("?", p.answer.correct_options)
                else:
                    self.assertEqual(p.grading_mode, "self_assessment")
            self.assertEqual(used, set(assets))
            for a in assets.values():
                path = ROOT / a["source_path"]
                self.assertTrue(path.resolve().is_relative_to(BASE))
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest() + ".png", a["path"])
                with Image.open(path) as im:
                    self.assertEqual(im.size, (a["width"], a["height"]))
                    im.verify()
                self.assertNotIn(a["source_path"], paths)
                paths.add(a["source_path"])
        self.assertEqual(len(ids), len(self.problems))
        pending_ids = {p["id"] for p in self.pending}
        self.assertFalse(ids & pending_ids)
        for p in self.pending:
            self.assertTrue(p["reasons"])
            for path in p["question"] + p["answer"]:
                self.assertNotIn(path, paths)
                paths.add(path)
        self.assertEqual(paths, {p.relative_to(ROOT).as_posix() for p in BASE.rglob("*.png")})
        self.assertEqual(len(paths), self.inventory["screenshots"])
        inventory = {p["id"]: p for p in self.inventory["problems"]}
        self.assertEqual(ids | pending_ids, set(inventory))
        self.assertEqual(Counter(p for row in inventory.values() for role in ("question", "answer")
                                 for p in row[role]), Counter(paths))
        self.assertEqual({p for row in inventory.values() if row["disposition"] == "ready"
                          for role in ("question", "answer") for p in row[role]},
                         {a["source_path"] for m in self.manifests.values() for a in m["assets"]})

    def test_types_and_answer_mapping_exceptions(self):
        def get(suffix):
            return self.problems["bjtu-final-" + suffix]
        self.assertEqual(get("linear-algebra-2024-s02-q11")["type"], "blank")
        self.assertEqual(get("probability-2021-s02-q11")["type"], "choice")
        self.assertEqual(get("probability-2020-s01-q15")["type"], "choice")
        self.assertEqual(get("probability-2018-s12")["type"], "solution")
        self.assertEqual(get("probability-2021-s01-q03")["answer"]["correct_options"], ["D"])
        multiple = get("probability-2019-s02-q01")
        self.assertTrue(multiple["multiple"])
        self.assertEqual(multiple["answer"]["correct_options"], ["A", "B"])
        pending = {p["id"]: p for p in self.pending}
        self.assertIn("bjtu-final-probability-2021-s01-q04", pending)
        self.assertNotIn("bjtu-final-probability-2021-s01-q04", self.problems)

    def test_aliases_and_fragment_order(self):
        rows = {p["id"]: p for p in self.inventory["problems"]}
        six = rows["bjtu-final-probability-2021-s06"]
        self.assertEqual([Path(p).stem for p in six["question"]], ["六上", "六下"])
        self.assertEqual([Path(p).stem for p in six["answer"]], ["六19"])
        self.assertEqual([Path(p).stem for p in rows["bjtu-final-probability-2022-s03"]["answer"]], ["三16"])
        for row in rows.values():
            p = self.problems.get(row["id"])
            if p is None:
                continue
            assets = {a["id"]: a for a in self.manifests[row["course"]]["assets"]}
            for role, refs in (("question", p["question_images"]), ("answer", p["answer"]["images"])):
                self.assertEqual(row[role], [assets[aid]["source_path"] for aid in refs])

    def test_reproducibility_and_overwrite_protection(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            with contextlib.redirect_stdout(io.StringIO()):
                build(out)
            for original in (ROOT / "data/bjtu-finals").iterdir():
                self.assertEqual(original.read_bytes(), (out / original.name).read_bytes(), original.name)
            with self.assertRaises(SystemExit):
                build(out)


if __name__ == "__main__":
    unittest.main()
