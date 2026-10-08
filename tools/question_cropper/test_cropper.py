"""Persistence and image-integrity checks for the correction workflow."""
import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image, ImageChops

from app import Conflict, Store, pixel_box


class CropperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name))

    def test_corrections_survive_reload_and_previous_version_is_retained(self):
        state = self.store.get()["state"]
        original = copy.deepcopy(state)
        state["questions"][0]["regions"][0]["bbox"][3] += .01
        state["page_notes"]["single-p1"] = "用时 3 分钟，调大下边缘"
        saved = self.store.save(state)
        self.assertEqual(saved["revision"], 1)
        reopened = Store(self.store.data_dir).get()["state"]
        self.assertEqual(reopened["questions"], state["questions"])
        self.assertEqual(reopened["page_notes"], state["page_notes"])
        self.assertEqual(json.loads((self.store.data_dir / "annotations.previous.json").read_text("utf-8")), original)
        self.assertNotEqual(self.store.sample["questions"][0], state["questions"][0])

    def test_stale_window_cannot_overwrite_saved_work(self):
        old = self.store.get()["state"]
        new = copy.deepcopy(old)
        new["questions"][0]["number"] = "修改后的题号"
        self.store.save(new)
        with self.assertRaises(Conflict):
            self.store.save(old)
        self.assertEqual(self.store.get()["state"]["questions"][0]["number"], "修改后的题号")

    def test_invalid_coordinates_ids_and_cross_document_regions_do_not_write(self):
        baseline = self.store.path.read_bytes()
        for mutation in (
            lambda r: r.update(bbox=[0, 0, float("nan"), .2]),
            lambda r: r.update(bbox=[0, .5, .1, .2]),
            lambda r: r.update(bbox=[-.1, 0, .2, .3]),
            lambda r: r.update(id="../../escape"),
            lambda r: r.update(page_id="scan-p2"),
            lambda r: r.update(role="answer"),
        ):
            with self.subTest(mutation=mutation):
                state = self.store.get()["state"]
                mutation(state["questions"][0]["regions"][0])
                with self.assertRaises(ValueError):
                    self.store.save(state)
                self.assertEqual(self.store.path.read_bytes(), baseline)

    def test_only_confirmed_questions_export_and_cross_page_pixels_match(self):
        state = self.store.get()["state"]
        q = next(q for q in state["questions"] if q["id"] == "single-q6")
        q["reviewed"] = True
        self.store.save(state)
        body = self.store.export()
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            manifest = json.loads(archive.read("questions.json"))
            self.assertEqual([q["id"] for q in manifest["questions"]], ["single-q6"])
            exported = manifest["questions"][0]
            combined = Image.open(io.BytesIO(archive.read(exported["files"]["stem"]))).convert("RGB")
            y = 0
            for region in exported["regions"]:
                source = Image.open(io.BytesIO(self.store.page_png(region["page_id"], "export"))).convert("RGB")
                expected = source.crop(pixel_box(region["bbox"], source.size))
                actual = Image.open(io.BytesIO(archive.read(region["image"]))).convert("RGB")
                self.assertEqual(actual.size, expected.size)
                self.assertIsNone(ImageChops.difference(actual, expected).getbbox())
                stitched = combined.crop((0, y, actual.width, y + actual.height))
                self.assertIsNone(ImageChops.difference(stitched, expected).getbbox())
                y += actual.height + 24
            self.assertEqual(combined.height, y - 24)
            self.assertEqual(json.loads(archive.read("trial-report.json"))["confirmed"], 1)

    def test_answers_are_exported_separately(self):
        state = self.store.get()["state"]
        q = state["questions"][0]
        answer = copy.deepcopy(q["regions"][0])
        answer.update(id="answer-region", role="answer")
        q["regions"].append(answer); q["reviewed"] = True
        self.store.save(state)
        with zipfile.ZipFile(io.BytesIO(self.store.export())) as archive:
            item = json.loads(archive.read("questions.json"))["questions"][0]
            self.assertEqual(set(item["files"]), {"stem", "answer"})
            self.assertNotEqual(item["files"]["stem"], item["files"]["answer"])


if __name__ == "__main__":
    unittest.main()
