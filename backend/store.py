"""Local SQLite and Supabase Data API share the same domain records."""

import json
import sqlite3
from contextlib import closing, contextmanager

import httpx

from . import config

JSON_FIELDS = {
    "tags",
    "question_images",
    "choice_options",
    "images",
    "correct_options",
    "image_ids",
    "filters",
}
BOOL_FIELDS = {
    "multiple",
    "verified",
    "answer_verified",
    "manual_revision",
    "ever_correct",
    "ever_incorrect",
    "is_read",
}
TABLES = {
    "profiles",
    "local_users",
    "local_sessions",
    "problem_sources",
    "problems",
    "problem_answers",
    "user_problem_progress",
    "daily_assignments",
    "answer_access",
    "media_assets",
    "solutions",
    "comments",
    "likes",
    "notifications",
    "moderation_logs",
    "rate_limits",
    "problem_feedback",
}
PRIMARY = {"problem_answers": "problem_id", "local_sessions": "token_hash"}


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
        return self.request("POST", f"/rest/v1/rpc/{name}", json=payload)


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
