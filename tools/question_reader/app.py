"""Local, read-only browser for the extracted mathematics question bank.

The server intentionally uses only Python's standard library.  It opens the
SQLite database with ``mode=ro`` and does not expose any write endpoint.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sqlite3
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


APP_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = APP_ROOT.parents[1]
DEFAULT_DB = PROJECT_ROOT / "data" / "problem_bank.sqlite3"
DEFAULT_RESOURCE_ROOT = PROJECT_ROOT / "resource"
STATIC_ROOT = APP_ROOT / "static"


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _parse_int(value: str | None, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _like(value: str) -> str:
    """Make a LIKE search value, escaping wildcard characters."""

    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


class ReaderStore:
    def __init__(self, db_path: Path, resource_root: Path) -> None:
        self.db_path = db_path.resolve()
        self.resource_root = resource_root.resolve()
        if not self.db_path.is_file():
            raise FileNotFoundError(f"database not found: {self.db_path}")

    def connect(self) -> sqlite3.Connection:
        # URI mode=ro is an extra guard against accidental changes from this UI.
        connection = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    @staticmethod
    def _tags_for(connection: sqlite3.Connection, problem_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT t.dimension, t.name
            FROM problem_tags pt JOIN tags t ON t.id = pt.tag_id
            WHERE pt.problem_id = ?
            ORDER BY t.dimension, t.name
            """,
            (problem_id,),
        ).fetchall()
        return [_row_dict(row) for row in rows]

    def summary(self) -> dict[str, Any]:
        with self.connect() as db:
            counts = db.execute(
                """
                SELECT
                  (SELECT count(*) FROM problems) AS problems,
                  (SELECT count(*) FROM problem_sources WHERE included = 1) AS included_sources,
                  (SELECT count(*) FROM problem_sources WHERE included = 0) AS excluded_sources,
                  (SELECT count(*) FROM problem_sources) AS source_count,
                  (SELECT count(*) FROM extraction_issues) AS issues,
                  (SELECT count(DISTINCT source_id) FROM extraction_issues) AS issue_files,
                  (SELECT count(*) FROM problem_aliases) AS aliases,
                  (SELECT count(*) FROM problems WHERE requires_review = 1) AS review_required
                """
            ).fetchone()
            type_rows = db.execute(
                "SELECT question_type AS label, count(*) AS count FROM problems GROUP BY question_type ORDER BY count DESC"
            ).fetchall()
            issue_rows = db.execute(
                "SELECT issue_type AS label, count(*) AS count FROM extraction_issues GROUP BY issue_type ORDER BY count DESC"
            ).fetchall()
            return {
                **_row_dict(counts),
                "question_types": [_row_dict(row) for row in type_rows],
                "issue_types": [_row_dict(row) for row in issue_rows],
            }

    def filters(self) -> dict[str, Any]:
        with self.connect() as db:
            source_kinds = db.execute(
                "SELECT source_kind AS value, count(*) AS count FROM problem_sources WHERE included = 1 GROUP BY source_kind ORDER BY source_kind"
            ).fetchall()
            types = db.execute(
                "SELECT question_type AS value, count(*) AS count FROM problems GROUP BY question_type ORDER BY question_type"
            ).fetchall()
            years = db.execute(
                "SELECT name AS value, count(*) AS count FROM tags WHERE dimension = 'year' GROUP BY name ORDER BY CAST(replace(name, '年', '') AS INTEGER) DESC, name DESC"
            ).fetchall()
            tags = db.execute(
                "SELECT dimension, name AS value, count(*) AS count FROM tags GROUP BY dimension, name ORDER BY dimension, name"
            ).fetchall()
            return {
                "source_kinds": [_row_dict(row) for row in source_kinds],
                "question_types": [_row_dict(row) for row in types],
                "years": [_row_dict(row) for row in years],
                "tags": [_row_dict(row) for row in tags],
            }

    def _problem_where(self, params: dict[str, str]) -> tuple[str, list[Any]]:
        clauses = ["1 = 1"]
        values: list[Any] = []
        search = params.get("search", "").strip()
        if search:
            pattern = _like(search)
            clauses.append(
                """(
                    p.content_markdown LIKE ? ESCAPE '\\'
                    OR COALESCE(pa.standard_answer, '') LIKE ? ESCAPE '\\'
                    OR COALESCE(pa.explanation_markdown, '') LIKE ? ESCAPE '\\'
                    OR s.filename LIKE ? ESCAPE '\\'
                    OR s.path LIKE ? ESCAPE '\\'
                )"""
            )
            values.extend([pattern] * 5)
        if params.get("source_kind"):
            clauses.append("s.source_kind = ?")
            values.append(params["source_kind"])
        if params.get("year"):
            clauses.append(
                "EXISTS (SELECT 1 FROM problem_tags pyt JOIN tags pytg ON pytg.id = pyt.tag_id WHERE pyt.problem_id = p.id AND pytg.dimension = 'year' AND pytg.name = ?)"
            )
            values.append(params["year"])
        if params.get("difficulty"):
            difficulty = _parse_int(params["difficulty"])
            if difficulty is not None and 1 <= difficulty <= 5:
                clauses.append("p.difficulty = ?")
                values.append(difficulty)
        if params.get("question_type"):
            clauses.append("p.question_type = ?")
            values.append(params["question_type"])
        if params.get("tag"):
            clauses.append(
                "EXISTS (SELECT 1 FROM problem_tags ptt JOIN tags tt ON tt.id = ptt.tag_id WHERE ptt.problem_id = p.id AND tt.dimension || ':' || tt.name = ?)"
            )
            values.append(params["tag"])
        if params.get("review") in {"0", "1"}:
            clauses.append("p.requires_review = ?")
            values.append(int(params["review"]))
        return " AND ".join(clauses), values

    def problems(self, params: dict[str, str]) -> dict[str, Any]:
        where, values = self._problem_where(params)
        page = max(1, _parse_int(params.get("page"), 1) or 1)
        page_size = min(100, max(10, _parse_int(params.get("page_size"), 25) or 25))
        offset = (page - 1) * page_size
        with self.connect() as db:
            total = db.execute(
                f"SELECT count(*) FROM problems p JOIN problem_sources s ON s.id = p.source_id LEFT JOIN problem_answers pa ON pa.problem_id = p.id WHERE {where}",
                values,
            ).fetchone()[0]
            rows = db.execute(
                f"""
                SELECT p.id, p.external_id, p.question_number, p.question_type,
                       substr(replace(replace(p.content_markdown, char(10), ' '), char(13), ' '), 1, 360) AS preview,
                       p.source_page_start, p.source_page_end, p.difficulty, p.status,
                       p.extraction_method, p.requires_review,
                       s.id AS source_id, s.filename, s.path, s.source_kind, s.included,
                       pa.standard_answer,
                       (SELECT group_concat(t.dimension || ':' || t.name, '||')
                          FROM problem_tags pt JOIN tags t ON t.id = pt.tag_id
                         WHERE pt.problem_id = p.id) AS tags,
                       (SELECT count(*) FROM problem_aliases al WHERE al.canonical_problem_id = p.id) AS alias_count
                FROM problems p
                JOIN problem_sources s ON s.id = p.source_id
                LEFT JOIN problem_answers pa ON pa.problem_id = p.id
                WHERE {where}
                ORDER BY s.path COLLATE NOCASE, p.source_page_start, p.question_number, p.id
                LIMIT ? OFFSET ?
                """,
                [*values, page_size, offset],
            ).fetchall()
            items = []
            for row in rows:
                item = _row_dict(row)
                item["tags"] = self._split_tags(item.pop("tags"))
                item["source_url"] = f"/files/{row['source_id']}#page={row['source_page_start']}"
                items.append(item)
            return {
                "items": items,
                "page": page,
                "page_size": page_size,
                "total": total,
                "pages": max(1, (total + page_size - 1) // page_size),
            }

    @staticmethod
    def _split_tags(value: str | None) -> list[dict[str, str]]:
        if not value:
            return []
        result: list[dict[str, str]] = []
        for item in value.split("||"):
            if ":" in item:
                dimension, name = item.split(":", 1)
                result.append({"dimension": dimension, "name": name})
        return result

    def problem(self, problem_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT p.*, s.filename, s.path, s.source_kind, s.included, s.page_count,
                       s.text_page_count, s.rights_status,
                       pa.standard_answer, pa.grading_config_json, pa.explanation_markdown,
                       pa.explanation_source, pa.verified
                FROM problems p
                JOIN problem_sources s ON s.id = p.source_id
                LEFT JOIN problem_answers pa ON pa.problem_id = p.id
                WHERE p.id = ?
                """,
                (problem_id,),
            ).fetchone()
            if row is None:
                return None
            result = _row_dict(row)
            result["tags"] = self._tags_for(db, problem_id)
            try:
                result["options"] = json.loads(result.pop("options_json") or "[]")
            except (TypeError, ValueError):
                result["options"] = []
                result.pop("options_json", None)
            try:
                result["grading_config"] = json.loads(result.pop("grading_config_json") or "null")
            except (TypeError, ValueError):
                result["grading_config"] = None
                result.pop("grading_config_json", None)
            links = db.execute(
                """
                SELECT psl.source_id, psl.source_page_start, psl.source_page_end,
                       psl.source_question_number, psl.extraction_method,
                       s.filename, s.path, s.source_kind
                FROM problem_source_links psl JOIN problem_sources s ON s.id = psl.source_id
                WHERE psl.problem_id = ? ORDER BY psl.is_primary DESC, s.path
                """,
                (problem_id,),
            ).fetchall()
            result["source_links"] = [
                {
                    **_row_dict(link),
                    "source_url": f"/files/{link['source_id']}#page={link['source_page_start']}",
                }
                for link in links
            ]
            aliases = db.execute(
                "SELECT alias_problem_id, match_type, similarity, alias_content_markdown FROM problem_aliases WHERE canonical_problem_id = ? ORDER BY similarity DESC",
                (problem_id,),
            ).fetchall()
            result["aliases"] = [_row_dict(alias) for alias in aliases]
            result["source_url"] = f"/files/{result['source_id']}#page={result['source_page_start']}"
            return result

    def issues(self, params: dict[str, str]) -> dict[str, Any]:
        clauses = ["1 = 1"]
        values: list[Any] = []
        search = params.get("search", "").strip()
        if search:
            pattern = _like(search)
            clauses.append("(e.details LIKE ? ESCAPE '\\' OR s.filename LIKE ? ESCAPE '\\' OR s.path LIKE ? ESCAPE '\\')")
            values.extend([pattern] * 3)
        if params.get("issue_type"):
            clauses.append("e.issue_type = ?")
            values.append(params["issue_type"])
        page = max(1, _parse_int(params.get("page"), 1) or 1)
        page_size = min(100, max(10, _parse_int(params.get("page_size"), 30) or 30))
        offset = (page - 1) * page_size
        with self.connect() as db:
            where = " AND ".join(clauses)
            total = db.execute(
                f"SELECT count(*) FROM extraction_issues e LEFT JOIN problem_sources s ON s.id = e.source_id WHERE {where}",
                values,
            ).fetchone()[0]
            rows = db.execute(
                f"""
                SELECT e.id, e.page, e.issue_type, e.details, e.source_id,
                       s.filename, s.path, s.source_kind, s.included
                FROM extraction_issues e LEFT JOIN problem_sources s ON s.id = e.source_id
                WHERE {where}
                ORDER BY s.path COLLATE NOCASE, e.page, e.id
                LIMIT ? OFFSET ?
                """,
                [*values, page_size, offset],
            ).fetchall()
            items = []
            for row in rows:
                item = _row_dict(row)
                item["source_url"] = f"/files/{row['source_id']}#page={row['page'] or 1}" if row["source_id"] else None
                items.append(item)
            return {"items": items, "page": page, "page_size": page_size, "total": total, "pages": max(1, (total + page_size - 1) // page_size)}

    def source_file(self, source_id: str) -> tuple[Path, str] | None:
        with self.connect() as db:
            row = db.execute("SELECT path, filename FROM problem_sources WHERE id = ?", (source_id,)).fetchone()
        if row is None:
            return None
        relative = Path(str(row["path"]).replace("/", os.sep))
        candidate = (PROJECT_ROOT / relative).resolve()
        try:
            candidate.relative_to(self.resource_root)
        except ValueError:
            return None
        if not candidate.is_file():
            return None
        return candidate, str(row["filename"])


class ReaderHandler(BaseHTTPRequestHandler):
    store: ReaderStore
    server_version = "QuestionReader/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[reader] " + (fmt % args) + "\n")

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        parsed = urlparse(self.path)
        path = parsed.path
        query = {key: values[-1] for key, values in parse_qs(parsed.query, keep_blank_values=True).items()}
        try:
            if path == "/" or path == "/index.html":
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path.removeprefix("/static/"))
            if path == "/api/summary":
                return self._json_response(self.store.summary())
            if path == "/api/filters":
                return self._json_response(self.store.filters())
            if path == "/api/problems":
                return self._json_response(self.store.problems(query))
            if path.startswith("/api/problems/"):
                problem_id = unquote(path.removeprefix("/api/problems/")).strip("/")
                result = self.store.problem(problem_id)
                if result is None:
                    return self._json_response({"error": "problem not found"}, HTTPStatus.NOT_FOUND)
                return self._json_response(result)
            if path == "/api/issues":
                return self._json_response(self.store.issues(query))
            if path.startswith("/files/"):
                source_id = unquote(path.removeprefix("/files/")).strip("/")
                return self._file(source_id)
            return self._json_response({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # Keep API errors readable while developing locally.
            self.log_message("request failed: %s", exc)
            return self._json_response({"error": "internal error", "detail": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _json_response(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = _json(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, requested: str) -> None:
        candidate = (STATIC_ROOT / requested).resolve()
        try:
            candidate.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            return self._json_response({"error": "not found"}, HTTPStatus.NOT_FOUND)
        if not candidate.is_file():
            return self._json_response({"error": "not found"}, HTTPStatus.NOT_FOUND)
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, source_id: str) -> None:
        result = self.store.source_file(source_id)
        if result is None:
            return self._json_response({"error": "source file not found"}, HTTPStatus.NOT_FOUND)
        path, filename = result
        body = path.read_bytes()
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{_quote_filename(filename)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _quote_filename(filename: str) -> str:
    from urllib.parse import quote

    return quote(filename, safe="")


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only local question-bank browser")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database path")
    parser.add_argument("--resource-root", type=Path, default=DEFAULT_RESOURCE_ROOT, help="root used to validate source files")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    store = ReaderStore(args.db, args.resource_root)
    ReaderHandler.store = store
    server = ThreadingHTTPServer((args.host, args.port), ReaderHandler)
    print(f"Question reader: http://{args.host}:{args.port}/")
    print(f"Database (read-only): {store.db_path}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
