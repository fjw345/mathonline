from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi.testclient import TestClient

from backend import config, main
from backend.outreach import available
from backend.store import SupabaseStore


def test_mailbox_permissions_revisions_and_account_isolation(env):
    admin, one, two, guest = (env[k] for k in ("admin", "one", "two", "guest"))
    body = {"title": "更新说明", "body": "第一行\n<script>alert(1)</script>"}
    assert guest.get("/api/v1/mailbox").status_code == 401
    assert one.post("/api/v1/admin/announcements", json=body).status_code == 403
    created = admin.post("/api/v1/admin/announcements", json=body)
    assert created.status_code == 200, created.text
    row = created.json()
    aid = row["id"]
    data = one.get("/api/v1/mailbox").json()
    assert data["total"] == 1
    assert data["items"][0]["body"] == body["body"]
    assert "author_id" not in data["items"][0]
    assert not data["items"][0]["is_read"]
    assert (
        one.put(f"/api/v1/mailbox/{aid}/read", json={"revision": 1}).status_code == 200
    )
    assert one.get("/api/v1/mailbox").json()["items"][0]["is_read"]
    assert not two.get("/api/v1/mailbox").json()["items"][0]["is_read"]
    updated = admin.put(
        f"/api/v1/admin/announcements/{aid}", json={**body, "revision": 1}
    ).json()
    assert updated["revision"] == 2
    assert updated["created_at"] == row["created_at"]
    assert not one.get("/api/v1/mailbox").json()["items"][0]["is_read"]
    assert (
        admin.put(
            f"/api/v1/admin/announcements/{aid}", json={**body, "revision": 1}
        ).status_code
        == 409
    )
    assert (
        one.put(f"/api/v1/mailbox/{aid}/read", json={"revision": 1}).status_code == 409
    )
    assert (
        admin.put(
            f"/api/v1/admin/announcements/{aid}",
            json={**body, "revision": 2, "published": False},
        ).status_code
        == 200
    )
    assert one.get("/api/v1/mailbox").json() == {"total": 0, "items": []}
    assert (
        one.put(f"/api/v1/mailbox/{aid}/read", json={"revision": 3}).status_code == 404
    )
    assert admin.get("/api/v1/admin/announcements").json()["total"] == 1
    assert one.get("/api/v1/admin/announcements").status_code == 403


def test_announcement_pagination_and_concurrent_edit(env):
    admin = env["admin"]
    for i in range(14):
        assert (
            admin.post(
                "/api/v1/admin/announcements", json={"title": str(i), "body": "更新"}
            ).status_code
            == 200
        )
    first = env["one"].get("/api/v1/mailbox").json()
    second = env["one"].get("/api/v1/mailbox?page=2").json()
    assert first["total"] == second["total"] == 14
    assert len(first["items"]) == 12 and len(second["items"]) == 2
    assert set(r["id"] for r in first["items"]).isdisjoint(
        r["id"] for r in second["items"]
    )
    row = env["db"].get("announcements", id=first["items"][0]["id"])

    def update(title):
        return env["db"].save_announcement({**row, "title": title, "revision": 2}, 1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["甲", "乙"]))
    assert sum(r is not None for r in results) == 1


def test_invites_binding_after_registration_and_gated_download(env, monkeypatch):
    monkeypatch.setattr(
        config, "RESOURCE_DOWNLOAD_URL", "https://pan.quark.cn/s/test?pwd=test"
    )
    monkeypatch.setattr(config, "RESOURCE_EXTRACTION_CODE", "test")
    one, two, admin, guest = (env[k] for k in ("one", "two", "admin", "guest"))
    assert guest.get("/api/v1/me/invites").status_code == 401
    assert guest.get("/api/v1/me/resources").status_code == 401
    assert (
        guest.get("/api/v1/me/resources/download", follow_redirects=False).status_code
        == 401
    )
    code = one.get("/api/v1/me/invites").json()["code"]
    assert one.get("/api/v1/me/invites").json()["code"] == code
    assert "quark" not in one.get("/api/v1/me/invites").text
    assert one.get("/api/v1/me/resources").status_code == 403
    assert (
        one.get("/api/v1/me/resources/download", follow_redirects=False).status_code
        == 403
    )
    assert one.post("/api/v1/me/invites/bind", json={"code": code}).status_code == 400
    assert (
        two.post("/api/v1/me/invites/bind", json={"code": "XXXXXXXXXX"}).status_code
        == 400
    )
    for _ in range(2):
        assert (
            two.post("/api/v1/me/invites/bind", json={"code": code.lower()}).status_code
            == 200
        )
    assert one.get("/api/v1/me/invites").json()["confirmed"] == 1
    other_code = admin.get("/api/v1/me/invites").json()["code"]
    assert (
        two.post("/api/v1/me/invites/bind", json={"code": other_code}).status_code
        == 409
    )
    assert admin.post("/api/v1/me/invites/bind", json={"code": code}).status_code == 200
    data = one.get("/api/v1/me/invites").json()
    assert data["confirmed"] == 2 and data["unlocked"]
    assert two.get("/api/v1/me/invites").json()["bound"]
    link = one.get("/api/v1/me/resources")
    assert link.status_code == 200 and link.json()["extraction_code"] == "test"
    assert "no-store" in link.headers["cache-control"]
    redirect = one.get("/api/v1/me/resources/download", follow_redirects=False)
    assert redirect.status_code == 303
    assert redirect.headers["location"] == link.json()["url"]
    assert "no-store" in redirect.headers["cache-control"]
    assert two.get("/api/v1/me/resources").status_code == 403
    assert admin.get("/api/v1/me/resources").status_code == 403  # no role bypass


def test_simultaneous_binding_is_immutable(env):
    profiles = {p["email"]: p for p in env["db"].list("profiles")}
    uid = profiles["two@test.local"]["id"]
    inviters = [profiles[e]["id"] for e in ("one@test.local", "admin@test.local")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(
            pool.map(
                lambda owner: env["db"].bind_invite(uid, owner, "2026-11-08"), inviters
            )
        )
    assert sorted(result) == ["conflict", "ok"]
    assert sum(env["db"].invite_status(owner)["confirmed"] for owner in inviters) == 1


def test_verified_email_is_authoritative_not_user_metadata(env, monkeypatch):
    code = env["one"].get("/api/v1/me/invites").json()["code"]
    profile = env["db"].get("profiles", email="two@test.local")
    remote = {
        "id": profile["id"],
        "email": profile["email"],
        "email_confirmed_at": None,
        "user_metadata": {"email_verified": True, "role": "admin"},
    }
    from backend import auth

    monkeypatch.setattr(config, "MODE", "supabase")
    monkeypatch.setattr(auth, "auth_request", lambda *args, **kwargs: remote)
    client = TestClient(main.app)
    response = client.post(
        "/api/v1/me/invites/bind",
        json={"code": code},
        headers={"Authorization": "Bearer test"},
    )
    assert response.status_code == 403
    assert not env["db"].get("invite_registrations", id=profile["id"])
    remote["email_confirmed_at"] = "2026-11-08T00:00:00Z"
    assert (
        client.post(
            "/api/v1/me/invites/bind",
            json={"code": code},
            headers={"Authorization": "Bearer test"},
        ).status_code
        == 200
    )


def test_cloud_rpc_contract_and_missing_migration(env):
    repo = SupabaseStore()
    calls = []

    def handle(request):
        calls.append(request)
        name = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json={
                "invite_status": {"confirmed": 2, "pending": 0},
                "bind_invite": "ok",
                "announcement_page": {"total": 0, "items": []},
                "save_announcement": None,
                "ensure_invite_code": {"code": "0123456789"},
            }[name],
        )

    repo.client.close()
    repo.client = httpx.Client(
        base_url="https://example.supabase.co", transport=httpx.MockTransport(handle)
    )
    assert repo.invite_status("one")["confirmed"] == 2
    assert repo.bind_invite("two", "one", "") == "ok"
    assert repo.announcement_page("one", 2, 12) == {"total": 0, "items": []}
    assert repo.ensure_invite_code("one", "0123456789", "")["code"] == "0123456789"
    assert repo.save_announcement({"id": "a"}, 1) is None
    assert len(calls) == 5
    repo.client.close()
    request = httpx.Request(
        "POST", "https://example.supabase.co/rest/v1/rpc/invite_status"
    )

    def missing():
        raise httpx.HTTPStatusError(
            "missing",
            request=request,
            response=httpx.Response(404, json={"code": "PGRST202"}, request=request),
        )

    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        available(missing)
    assert error.value.status_code == 503
