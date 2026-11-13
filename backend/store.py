"""Local SQLite and Supabase Data API share the same domain records."""

import json
import sqlite3
from contextlib import closing, contextmanager
from copy import deepcopy
from threading import Lock
from time import monotonic

import httpx

from . import config, topics

JSON_FIELDS = {
    "tags",
    "question_images",
    "choice_options",
    "images",
    "correct_options",
    "image_ids",
    "filters",
    "points",
    "knowledge_points",
    "stats",
    "report",
    "blueprint",
    "profile",
    "items",
    "knowledge_summary",
}
BOOL_FIELDS = {
    "multiple",
    "verified",
    "answer_verified",
    "manual_revision",
    "ever_correct",
    "ever_incorrect",
    "is_read",
    "active",
    "published",
}
TABLES = {
    "profiles",
    "local_users",
    "local_sessions",
    "problem_sources",
    "problems",
    "problem_answers",
    "problem_rubrics",
    "problem_grading",
    "submission_transcripts",
    "learning_analyses",
    "mock_papers",
    "practice_variants",
    "variant_cache",
    "user_problem_progress",
    "daily_assignments",
    "answer_access",
    "user_mistakes",
    "media_assets",
    "solutions",
    "comments",
    "likes",
    "notifications",
    "moderation_logs",
    "rate_limits",
    "problem_feedback",
    "announcements",
    "announcement_reads",
    "invite_codes",
    "invite_registrations",
}
PRIMARY = {
    "problem_answers": "problem_id",
    "problem_rubrics": "problem_id",
    "local_sessions": "token_hash",
}


class LocalStore:
    def __init__(self, path=None):
        self.path = path or config.DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                (config.ROOT / "backend/schema.sql").read_text(encoding="utf-8")
            )
            # Version 2 upgrades databases created during initial development.
            columns = {
                row["name"]
                for row in db.execute("PRAGMA table_info(user_problem_progress)")
            }
            if "correct_version" not in columns:
                db.execute(
                    "ALTER TABLE user_problem_progress ADD COLUMN correct_version INTEGER"
                )
                db.execute(
                    "UPDATE user_problem_progress SET correct_version=problem_version WHERE ever_correct=1"
                )
            db.execute("INSERT OR IGNORE INTO schema_migrations VALUES (2)")
            if not db.execute(
                "SELECT 1 FROM schema_migrations WHERE version=4"
            ).fetchone():
                # Change collection metadata once without revising questions or answers.
                db.execute(
                    "UPDATE problems SET tags=? WHERE id LIKE 'cmc-nonmath-%' AND manual_revision=0",
                    (json.dumps(["全国大学生数学竞赛·非数"], ensure_ascii=False),),
                )
                db.execute("UPDATE problems SET difficulty=NULL")
                db.execute("INSERT INTO schema_migrations VALUES (4)")
            if not db.execute(
                "SELECT 1 FROM schema_migrations WHERE version=5"
            ).fetchone():
                # Publish the initial bank without claiming it was manually checked.
                db.execute(
                    "UPDATE problems SET status='published' WHERE id LIKE 'cmc-nonmath-%' AND status='draft' AND manual_revision=0 "
                    "AND EXISTS (SELECT 1 FROM problem_answers WHERE problem_id=problems.id AND json_array_length(images)>0)"
                )
                db.execute("INSERT INTO schema_migrations VALUES (5)")
            # Version 7 adds the optional Markdown/LaTeX text for old databases.
            # Additive only: existing rows keep their images and versions.
            if "question_text" not in {
                row["name"] for row in db.execute("PRAGMA table_info(problems)")
            }:
                db.execute(
                    "ALTER TABLE problems ADD COLUMN question_text TEXT NOT NULL DEFAULT ''"
                )
            if "text" not in {
                row["name"] for row in db.execute("PRAGMA table_info(problem_answers)")
            }:
                db.execute(
                    "ALTER TABLE problem_answers ADD COLUMN text TEXT NOT NULL DEFAULT ''"
                )
            db.execute("INSERT OR IGNORE INTO schema_migrations VALUES (7)")
            # Version 8 binds cached rubric points to the answer text they came from.
            if "answer_hash" not in {
                row["name"] for row in db.execute("PRAGMA table_info(problem_rubrics)")
            }:
                db.execute(
                    "ALTER TABLE problem_rubrics ADD COLUMN answer_hash "
                    "TEXT NOT NULL DEFAULT ''"
                )
            db.execute("INSERT OR IGNORE INTO schema_migrations VALUES (8)")
            # Version 9 adds knowledge points and their provenance. The
            # difficulty column already exists but is now written by the
            # tagging job for mock-exam assembly, never as a public rating.
            problem_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(problems)")
            }
            if "knowledge_points" not in problem_columns:
                db.execute(
                    "ALTER TABLE problems ADD COLUMN knowledge_points "
                    "TEXT NOT NULL DEFAULT '[]'"
                )
            if "knowledge_source" not in problem_columns:
                db.execute(
                    "ALTER TABLE problems ADD COLUMN knowledge_source "
                    "TEXT NOT NULL DEFAULT ''"
                )
            db.execute("INSERT OR IGNORE INTO schema_migrations VALUES (9)")
            # Version 10 adds the shared AI-variant pool link. Older per-user
            # rows keep cache_id empty and simply bypass the pool.
            variant_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(practice_variants)")
            }
            if "cache_id" not in variant_columns:
                db.execute("ALTER TABLE practice_variants ADD COLUMN cache_id TEXT")
            if "source_hash" not in variant_columns:
                db.execute(
                    "ALTER TABLE practice_variants ADD COLUMN source_hash "
                    "TEXT NOT NULL DEFAULT ''"
                )
            if "reused" not in variant_columns:
                db.execute(
                    "ALTER TABLE practice_variants ADD COLUMN reused "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            db.execute("INSERT OR IGNORE INTO schema_migrations VALUES (10)")
            # Version 11 keeps the image-answer transcript next to the AI
            # snapshot; the transcript cache table is created by schema.sql.
            grading_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(problem_grading)")
            }
            if "transcript" not in grading_columns:
                db.execute(
                    "ALTER TABLE problem_grading ADD COLUMN transcript "
                    "TEXT NOT NULL DEFAULT ''"
                )
            db.execute("INSERT OR IGNORE INTO schema_migrations VALUES (11)")
            db.commit()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def reader(self):
        with closing(self.connect()) as db:
            db.execute("BEGIN")
            yield LocalStoreView(db)

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield LocalStoreView(db)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def list(self, table, **where):
        with self.reader() as view:
            return view.list(table, **where)

    def get(self, table, **where):
        records = self.list(table, **where)
        return records[0] if records else None

    def put(self, table, data):
        with self.transaction() as tx:
            return tx.put(table, data)

    def delete(self, table, **where):
        with self.transaction() as tx:
            tx.delete(table, **where)

    def apply_problem_texts(self, items):
        """Fill text columns only; never bumps versions or rewrites images."""
        updated = 0
        with self.transaction() as tx:
            for item in items:
                if not tx.get("problems", id=item["id"]):
                    continue
                tx.db.execute(
                    "UPDATE problems SET question_text=? WHERE id=?",
                    (item.get("question_text", ""), item["id"]),
                )
                tx.db.execute(
                    "UPDATE problem_answers SET text=? WHERE problem_id=?",
                    (item.get("answer_text", ""), item["id"]),
                )
                updated += 1
        return updated

    def apply_knowledge(self, items):
        """Write only knowledge columns; content, version and images stay put."""
        updated = 0
        with self.transaction() as tx:
            for item in items:
                if not tx.get("problems", id=item["id"]):
                    continue
                tx.db.execute(
                    "UPDATE problems SET knowledge_points=?,difficulty=?,"
                    "knowledge_source=? WHERE id=?",
                    (
                        json.dumps(
                            item.get("knowledge_points") or [], ensure_ascii=False
                        ),
                        item.get("difficulty"),
                        item.get("knowledge_source", ""),
                        item["id"],
                    ),
                )
                updated += 1
        return updated

    def forum_page(self, page, page_size):
        with self.reader() as reader:
            return reader.forum_page(page, page_size)

    def announcement_page(self, user_id, page, page_size, admin=False):
        with self.reader() as reader:
            where = "" if admin else " WHERE a.published=1"
            total = reader.db.execute(
                "SELECT count(*) FROM announcements a" + where
            ).fetchone()[0]
            rows = reader.db.execute(
                "SELECT a.*, coalesce(r.revision,0)>=a.revision AS is_read "
                "FROM announcements a LEFT JOIN announcement_reads r "
                "ON r.announcement_id=a.id AND r.user_id=?"
                + where
                + " ORDER BY a.created_at DESC,a.id DESC LIMIT ? OFFSET ?",
                (user_id, page_size, (page - 1) * page_size),
            ).fetchall()
            return {"total": total, "items": [dict(r) for r in rows]}

    def invite_status(self, user_id):
        with self.reader() as reader:
            count = reader.db.execute(
                "SELECT count(*) FROM invite_registrations WHERE inviter_id=?",
                (user_id,),
            ).fetchone()[0]
            return {"confirmed": count, "pending": 0}

    def bind_invite(self, user_id, inviter_id, timestamp):
        with self.transaction() as tx:
            old = tx.get("invite_registrations", id=user_id)
            if old:
                return "ok" if old["inviter_id"] == inviter_id else "conflict"
            tx.put(
                "invite_registrations",
                {"id": user_id, "inviter_id": inviter_id, "created_at": timestamp},
            )
            return "ok"

    def ensure_invite_code(self, user_id, code, created_at):
        with self.transaction() as tx:
            row = tx.get("invite_codes", id=user_id)
            return row or tx.put(
                "invite_codes", {"id": user_id, "code": code, "created_at": created_at}
            )

    def save_announcement(self, item, expected):
        with self.transaction() as tx:
            old = tx.get("announcements", id=item["id"])
            if (old["revision"] if old else 0) != expected:
                return None
            return tx.put("announcements", item)


class LocalStoreView:
    def __init__(self, db):
        self.db = db
        self.columns = {}

    def table_columns(self, table):
        if table not in self.columns:
            self.columns[table] = {
                row["name"] for row in self.db.execute(f"PRAGMA table_info({table})")
            }
        return self.columns[table]

    def list(self, table, **where):
        assert table in TABLES
        columns = self.table_columns(table)
        assert set(where) <= columns
        clause = " AND ".join(f"{k}=?" for k in where)
        rows = self.db.execute(
            f"SELECT * FROM {table}" + (" WHERE " + clause if clause else ""),
            list(where.values()),
        ).fetchall()
        return [
            {
                k: json.loads(v)
                if k in JSON_FIELDS
                else bool(v)
                if k in BOOL_FIELDS
                else v
                for k, v in dict(row).items()
            }
            for row in rows
        ]

    def get(self, table, **where):
        rows = self.list(table, **where)
        return rows[0] if rows else None

    def forum_page(self, page, page_size):
        visible = (
            " FROM solutions s JOIN problems p ON p.id=s.problem_id "
            "WHERE s.status='approved' AND p.status='published'"
        )
        total = self.db.execute("SELECT count(*)" + visible).fetchone()[0]
        rows = self.db.execute(
            "SELECT s.*" + visible + " ORDER BY s.created_at DESC,s.id DESC LIMIT ? OFFSET ?",
            (page_size, (page - 1) * page_size),
        ).fetchall()
        result = []
        for row in rows:
            s = dict(row)
            s["image_ids"] = json.loads(s["image_ids"])
            s["author"] = self.get("profiles", id=s["user_id"])
            s["problem"] = self.get("problems", id=s["problem_id"])
            result.append(s)
        return {"total": total, "rows": result}

    def put(self, table, data):
        assert table in TABLES
        columns = self.table_columns(table)
        assert set(data) <= columns
        pk = PRIMARY.get(table, "id")
        keys = list(data)
        updates = ",".join(f"{k}=excluded.{k}" for k in keys if k != pk)
        values = [
            json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS else v
            for k, v in data.items()
        ]
        self.db.execute(
            f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)}) ON CONFLICT({pk}) DO UPDATE SET {updates}",
            values,
        )
        return self.get(table, **{pk: data[pk]})

    def delete(self, table, **where):
        assert table in TABLES and where
        columns = self.table_columns(table)
        assert set(where) <= columns
        self.db.execute(
            f"DELETE FROM {table} WHERE " + " AND ".join(f"{k}=?" for k in where),
            list(where.values()),
        )


class SupabaseStore:
    def __init__(self):
        self._cache = {}
        self._cache_lock = Lock()
        self.client = httpx.Client(
            base_url=config.SB_URL,
            headers={
                "apikey": config.SB_KEY,
                "Authorization": f"Bearer {config.SB_KEY}",
            },
            timeout=25,
        )

    def request(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    def list(self, table, **where):
        assert table in TABLES
        params = {
            k: "eq." + str(v).lower() if isinstance(v, bool) else "eq." + str(v)
            for k, v in where.items()
        }
        rows, offset = [], 0
        while True:
            part = self.request(
                "GET",
                f"/rest/v1/{table}",
                params={
                    **params,
                    "select": "*",
                    "order": PRIMARY.get(table, "id"),
                    "offset": offset,
                    "limit": 500,
                },
            )
            rows.extend(part)
            if len(part) < 500:
                return rows
            offset += 500

    def get(self, table, **where):
        rows = self.list(table, **where)
        return rows[0] if rows else None

    def put(self, table, data):
        assert table in TABLES
        rows = self.request(
            "POST",
            f"/rest/v1/{table}",
            params={"on_conflict": PRIMARY.get(table, "id")},
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            json=data,
        )
        return rows[0]

    def delete(self, table, **where):
        assert table in TABLES and where
        self.request(
            "DELETE",
            f"/rest/v1/{table}",
            params={k: "eq." + str(v) for k, v in where.items()},
        )

    def rpc(self, name, payload):
        result = self.request("POST", f"/rest/v1/rpc/{name}", json=payload)
        if name == "save_problem":
            with self._cache_lock:
                self._cache.clear()
        return result

    def apply_problem_texts(self, items):
        """Bulk text backfill through one additive SQL function."""
        return self.rpc("apply_problem_texts", {"p_items": items})

    def apply_knowledge(self, items):
        """Bulk knowledge backfill; chunks stay under Data API request limits."""
        updated = 0
        for start in range(0, len(items), 200):
            updated += self.rpc(
                "apply_problem_knowledge", {"p_items": items[start : start + 200]}
            )
        return updated

    def announcement_page(self, user_id, page, page_size, admin=False):
        return self.rpc(
            "announcement_page",
            {"p_user": user_id, "p_page": page, "p_size": page_size, "p_admin": admin},
        )

    def invite_status(self, user_id):
        return self.rpc("invite_status", {"p_user": user_id})

    def bind_invite(self, user_id, inviter_id, timestamp):
        return self.rpc("bind_invite", {"p_user": user_id, "p_inviter": inviter_id})

    def ensure_invite_code(self, user_id, code, created_at):
        return self.rpc("ensure_invite_code", {"p_user": user_id, "p_code": code})

    def save_announcement(self, item, expected):
        return self.rpc("save_announcement", {"p_item": item, "p_expected": expected})

    def media_references(self, table, column, aid):
        return self.request(
            "GET",
            "/rest/v1/" + table,
            params={column: "cs." + json.dumps([aid]), "select": "*"},
        )

    def catalog(self):
        # Cache public metadata only; never cache profiles or answer permissions.
        with self._cache_lock:
            cached = self._cache.get("catalog")
            if cached and cached[0] > monotonic():
                return deepcopy(cached[1])
        # Statistics need no image IDs, titles or answer/grading metadata.
        rows, offset = [], 0
        while True:
            part = self.request(
                "GET", "/rest/v1/problems",
                params={"status": "eq.published",
                        "select": "id,year,type,tags,knowledge_points",
                        "order": "id", "offset": offset, "limit": 500},
            )
            rows.extend(part)
            if len(part) < 500:
                break
            offset += 500
        knowledge_counts = {}
        for row in rows:
            for code in row.get("knowledge_points") or []:
                if topics.knows(code):
                    knowledge_counts[code] = knowledge_counts.get(code, 0) + 1
        result = {
            "total": len(rows),
            "years": sorted({p["year"] for p in rows}, reverse=True),
            "tags": sorted({t for p in rows for t in p["tags"]}),
            "knowledge": [
                {**item, "count": knowledge_counts.get(item["code"], 0)}
                for item in topics.catalog()
            ],
            "counts": {
                t: sum(p["type"] == t for p in rows)
                for t in ("blank", "solution", "choice")
            },
        }
        with self._cache_lock:
            self._cache["catalog"] = (monotonic() + 30, result)
        return deepcopy(result)

    def problem_page(self, filters, page, page_size):
        # Keyword search retains the literal substring semantics of the local app.
        # Other filters and pagination run in Postgres via PostgREST.
        params = {
            "status": "eq.published",
            "select": "*",
            "order": "year.desc,group_name.asc,id.asc",
        }
        for key in ("year", "type"):
            if filters.get(key):
                values = str(filters[key]).split(",")
                if key == "year" and not all(v.isdigit() for v in values):
                    return {"total": 0, "rows": []}
                if key == "type" and not all(
                    v in ("blank", "solution", "choice") for v in values
                ):
                    return {"total": 0, "rows": []}
                params[key] = "in.(" + ",".join(values) + ")"
        if filters.get("tag"):
            # tags is jsonb, not a PostgreSQL array: overlap (ov) is invalid.
            tags = str(filters["tag"]).split(",")
            if len(tags) == 1:
                params["tags"] = "cs." + json.dumps(tags, ensure_ascii=False)
            else:
                # Preserve the local filter's ANY semantics for multiple tags.
                terms = [
                    "tags.cs." + json.dumps(json.dumps([tag], ensure_ascii=False))
                    for tag in tags
                ]
                params["or"] = "(" + ",".join(terms) + ")"
        if filters.get("knowledge"):
            # Same ANY semantics as the local filter. A single code uses one
            # contains filter; multiple codes use PostgREST's disjunction.
            codes = topics.parse_filter(str(filters["knowledge"]))
            if not codes:
                return {"total": 0, "rows": []}
            if len(codes) == 1:
                params["knowledge_points"] = "cs." + json.dumps(
                    codes, ensure_ascii=False
                )
            else:
                params["or"] = "(" + ",".join(
                    "knowledge_points.cs."
                    + json.dumps(json.dumps([code], ensure_ascii=False))
                    for code in codes
                ) + ")"
        if filters.get("q"):
            rows, offset = [], 0
            while True:
                part = self.request(
                    "GET",
                    "/rest/v1/problems",
                    params={**params, "offset": offset, "limit": 500},
                )
                rows.extend(part)
                if len(part) < 500:
                    break
                offset += 500
            term = filters["q"].lower()
            rows = [
                p
                for p in rows
                if term
                in (
                    p["title"]
                    + " "
                    + p["id"]
                    + " "
                    + " ".join(p["tags"])
                    + " "
                    + p.get("question_text", "")
                ).lower()
            ]
            return {
                "total": len(rows),
                "rows": rows[(page - 1) * page_size : page * page_size],
            }
        response = self.client.get(
            "/rest/v1/problems",
            params={**params, "offset": (page - 1) * page_size, "limit": page_size},
            headers={"Prefer": "count=exact"},
        )
        response.raise_for_status()
        return {
            "total": int(response.headers["Content-Range"].rsplit("/", 1)[1]),
            "rows": response.json(),
        }

    def forum_page(self, page, page_size):
        response = self.client.get(
            "/rest/v1/solutions",
            params={
                "select": (
                    "id,body,image_ids,created_at,updated_at,"
                    "author:profiles(nickname),"
                    "problem:problems!inner(id,title,type,year,tags)"
                ),
                "status": "eq.approved",
                "problem.status": "eq.published",
                "order": "created_at.desc,id.desc",
                "offset": (page - 1) * page_size,
                "limit": page_size,
            },
            headers={"Prefer": "count=exact"},
        )
        # A moderation change can remove the last page between navigations.
        total = response.headers.get("Content-Range", "").rsplit("/", 1)[-1]
        if response.status_code == 416 and total.isdigit():
            return {"total": int(total), "rows": []}
        response.raise_for_status()
        return {
            "total": int(response.headers["Content-Range"].rsplit("/", 1)[1]),
            "rows": response.json(),
        }

    @contextmanager
    def reader(self):
        yield SupabaseReader(self)


class SupabaseReader:
    """Request-scoped batches; private rows are never shared between requests."""

    def __init__(self, repository):
        self.repository = repository
        self.rows = {}
        self.image_urls = {}

    def sign_question_images(self, problems):
        # Only published question images, never answer or private submission files.
        assets = {}
        for p in problems:
            if p["status"] == "published":
                for aid in p["question_images"]:
                    asset = self.get("media_assets", id=aid)
                    if (
                        asset
                        and asset.get("purpose") == "question"
                        and asset.get("path")
                    ):
                        assets[aid] = asset["path"]
        if not assets:
            return
        try:
            signed = self.repository.request(
                "POST",
                f"/storage/v1/object/sign/{config.BUCKET}",
                json={"paths": list(dict.fromkeys(assets.values())), "expiresIn": 300},
            )
        except httpx.HTTPError:
            return  # Existing authenticated media route remains the fallback.
        by_path = {
            row.get("path"): row.get("signedURL") or row.get("signedUrl")
            for row in signed
            if not row.get("error")
        }
        for aid, path in assets.items():
            url = by_path.get(path)
            if url and url.startswith("/"):
                self.image_urls[aid] = config.SB_URL + "/storage/v1" + url

    def list(self, table, **where):
        key = (table, tuple(sorted(where.items())))
        if key not in self.rows:
            self.rows[key] = self.repository.list(table, **where)
        return deepcopy(self.rows[key])

    def prime(self, table, ids, key="id"):
        ids = list(dict.fromkeys(ids))
        found = []
        # Bounded URLs and batches, including missing IDs so they aren't re-fetched.
        for start in range(0, len(ids), 60):
            values = ",".join(json.dumps(v) for v in ids[start : start + 60])
            found.extend(
                self.repository.request(
                    "GET",
                    "/rest/v1/" + table,
                    params={key: "in.(" + values + ")", "select": "*", "limit": 60},
                )
            )
        index = {row[key]: row for row in found}
        for value in ids:
            self.rows[(table, ((key, value),))] = (
                [index[value]] if value in index else []
            )

    def prepare_problems(self, problems, user=None, admin=False):
        self.prime("problem_sources", [p["source_id"] for p in problems])
        assets = [aid for p in problems for aid in p["question_images"]]
        if admin:
            self.prime("problem_answers", [p["id"] for p in problems], "problem_id")
            assets += [
                aid
                for p in problems
                for aid in self.get("problem_answers", problem_id=p["id"])["images"]
            ]
        elif user:
            ids = {p["id"] for p in problems}
            for table in ("user_problem_progress", "answer_access", "problem_grading"):
                rows = self.list(table, user_id=user["id"])
                index = {r["problem_id"]: r for r in rows if r["problem_id"] in ids}
                for pid in ids:
                    self.rows[
                        (
                            table,
                            tuple(
                                sorted(
                                    {"user_id": user["id"], "problem_id": pid}.items()
                                )
                            ),
                        )
                    ] = [index[pid]] if pid in index else []
                if table == "user_problem_progress":
                    assets += [aid for r in index.values() for aid in r["image_ids"]]
            likes = {
                r["id"]: r
                for r in self.list("likes", user_id=user["id"], kind="problem")
            }
            for pid in ids:
                lid = user["id"] + ":problem:" + pid
                self.rows[("likes", (("id", lid),))] = (
                    [likes[lid]] if lid in likes else []
                )
        self.prime("media_assets", assets)

    def get(self, table, **where):
        rows = self.list(table, **where)
        return rows[0] if rows else None


store = LocalStore() if config.MODE == "local" else SupabaseStore()


@contextmanager
def atomic_store(repository):
    """SQLite serializes local community mutations; cloud uses its Data API."""
    if hasattr(repository, "transaction"):
        with repository.transaction() as tx:
            yield tx
    else:
        yield repository


@contextmanager
def read_store(repository):
    if hasattr(repository, "reader"):
        with repository.reader() as view:
            yield view
    else:
        yield repository
