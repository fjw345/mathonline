import json

import httpx

from backend import config, main
from backend.store import SupabaseStore


def test_question_signing_never_signs_answers_and_falls_back(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            assert json.loads(request.content)["paths"] == ["question.png"]
            return httpx.Response(
                200,
                json=[
                    {
                        "path": "question.png",
                        "signedURL": "/object/sign/mathonline/question.png?token=test",
                    }
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "id": "q",
                    "path": "question.png",
                    "purpose": "question",
                    "width": 1,
                    "height": 1,
                },
                {
                    "id": "a",
                    "path": "answer.png",
                    "purpose": "answer",
                    "width": 1,
                    "height": 1,
                },
            ],
        )

    db = cloud(handler, monkeypatch)
    with db.reader() as reader:
        reader.prime("media_assets", ["q", "a"])
        reader.sign_question_images(
            [{"status": "published", "question_images": ["q", "a"]}]
        )
        assert main.asset_info("q", reader)["url"].startswith(config.SB_URL)
        assert main.asset_info("a", reader)["url"] == "/api/v1/media/a"
    with db.reader() as reader:
        reader.prime("media_assets", ["q"])
        reader.sign_question_images([{"status": "archived", "question_images": ["q"]}])
        assert reader.image_urls == {}

    def unavailable(request):
        return httpx.Response(503)

    db.client = httpx.Client(
        base_url=config.SB_URL, transport=httpx.MockTransport(unavailable)
    )
    with db.reader() as reader:
        reader.rows[("media_assets", (("id", "q"),))] = [
            {"id": "q", "purpose": "question", "path": "question.png"}
        ]
        reader.sign_question_images([{"status": "published", "question_images": ["q"]}])
        assert reader.image_urls == {}


def cloud(handler, monkeypatch):
    monkeypatch.setattr(config, "SB_URL", "https://example.supabase.co")
    db = SupabaseStore()
    db.client.close()
    db.client = httpx.Client(
        base_url=config.SB_URL, transport=httpx.MockTransport(handler)
    )
    return db


def test_cloud_page_batches_related_rows_without_answers(monkeypatch):
    requests = []
    problems = [
        dict(
            id=f"p{i}",
            source_id="s",
            question_images=["img"],
            tags=[],
            version=1,
            status="published",
        )
        for i in range(12)
    ]

    def handler(request):
        requests.append(request)
        table = request.url.path.rsplit("/", 1)[-1]
        data = {
            "problems": problems,
            "problem_sources": [{"id": "s"}],
            "media_assets": [{"id": "img", "width": 100, "height": 50}],
            "user_problem_progress": [],
            "answer_access": [],
            "likes": [],
        }[table]
        return httpx.Response(200, json=data, headers={"Content-Range": "0-11/222"})

    db = cloud(handler, monkeypatch)
    page = db.problem_page({"year": "2025", "type": "solution", "tag": "竞赛"}, 2, 12)
    assert page["total"] == 222
    params = requests[0].url.params
    assert params["offset"] == "12" and params["limit"] == "12"
    assert params["year"] == "in.(2025)" and params["status"] == "eq.published"
    assert json.loads(params["tags"][3:]) == ["竞赛"]
    with db.reader() as reader:
        reader.prepare_problems(page["rows"], {"id": "alice"})
        result = [main.public_problem(p, {"id": "alice"}, reader) for p in page["rows"]]
    assert len(requests) == 6  # One page + sources + assets + three user batches.
    assert all(not p["answer_unlocked"] and "answer" not in p for p in result)
    assert all("problem_answers" not in str(r.url) for r in requests)
    for r in requests:
        if r.url.path.endswith(("user_problem_progress", "answer_access", "likes")):
            assert r.url.params["user_id"] == "eq.alice"


def test_cloud_permissions_not_cached_between_requests(monkeypatch):
    unlocked = False

    def handler(request):
        table = request.url.path.rsplit("/", 1)[-1]
        data = {
            "problem_sources": [{"id": "s"}],
            "media_assets": [],
            "user_problem_progress": [],
            "likes": [],
            "answer_access": [{"problem_id": "p", "user_id": "alice"}]
            if unlocked and request.url.params.get("user_id") == "eq.alice"
            else [],
        }[table]
        return httpx.Response(200, json=data)

    db = cloud(handler, monkeypatch)
    p = {"id": "p", "source_id": "s", "question_images": []}
    for uid, expected in [("alice", False), ("alice", True), ("bob", False)]:
        with db.reader() as reader:
            reader.prepare_problems([p], {"id": uid})
            assert (
                main.public_problem(p, {"id": uid}, reader)["answer_unlocked"]
                == expected
            )
        unlocked = True


def test_cloud_catalog_cache_is_public_and_expires(monkeypatch):
    calls, clock = [], [10]
    monkeypatch.setattr("backend.store.monotonic", lambda: clock[0])

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, json=[{"year": 2025, "type": "blank", "tags": ["竞赛"]}]
        )

    db = cloud(handler, monkeypatch)
    first = db.catalog()
    first["tags"].append("must not change cached value")
    assert db.catalog()["tags"] == ["竞赛"] and len(calls) == 1
    clock[0] += 31
    assert db.catalog()["total"] == 1 and len(calls) == 2


def test_cloud_media_query_uses_array_containment(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=[])

    db = cloud(handler, monkeypatch)
    assert db.media_references("problem_answers", "images", "img") == []
    assert json.loads(calls[0].url.params["images"][3:]) == ["img"]


def test_cloud_literal_keyword_and_invalid_filter(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json=[
                {"id": "p", "title": "50%_test", "tags": []},
                {"id": "p2", "title": "other", "tags": []},
            ],
        )

    db = cloud(handler, monkeypatch)
    assert db.problem_page({"q": "%_"}, 1, 12)["total"] == 1
    assert db.problem_page({"year": "2025),id.eq.p"}, 1, 12)["total"] == 0
    assert len(calls) == 1


def test_cloud_admin_batches_large_bank_and_keeps_image_order(monkeypatch):
    calls = []
    problems = [
        dict(id=f"p{i}", source_id="s", question_images=[f"q{i}"]) for i in range(222)
    ]
    tables = {
        "problem_sources": [{"id": "s"}],
        "problem_answers": [
            {"problem_id": f"p{i}", "images": [f"a{i}", f"q{i}"]} for i in range(222)
        ],
        "media_assets": [
            {"id": key, "width": 10, "height": 10}
            for i in range(222)
            for key in (f"q{i}", f"a{i}")
        ],
    }

    def handler(request):
        calls.append(request)
        table = request.url.path.rsplit("/", 1)[-1]
        key = "problem_id" if table == "problem_answers" else "id"
        values = json.loads("[" + request.url.params[key][4:-1] + "]")
        return httpx.Response(200, json=[r for r in tables[table] if r[key] in values])

    db = cloud(handler, monkeypatch)
    with db.reader() as reader:
        reader.prepare_problems(problems, admin=True)
        for p in problems:
            a = reader.get("problem_answers", problem_id=p["id"])
            assert [r["id"] for r in main.images_info(a["images"], reader)] == a[
                "images"
            ]
            assert reader.get("problem_sources", id="s")["id"] == "s"
    assert len(calls) == 13  # One source, four answer batches, eight asset batches.
