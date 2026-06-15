#!/usr/bin/env python3
import argparse
import contextlib
import csv
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import time
from copy import deepcopy
from datetime import datetime, timezone
from glob import glob
from pathlib import Path


DEFAULT_DB = "/config/booktree.db"
DEFAULT_CONFIG = "/config/config.json"
STATUSES = [
    "needs_metadata",
    "no_match",
    "multiple_matches",
    "matched",
    "processed",
    "failed",
    "ignored",
]


class DictConfig:
    def __init__(self, data):
        self._data = data

    def get(self, path=None, default=None):
        if path is None:
            return deepcopy(self._data)

        value = self._data
        for item in path.split("/"):
            if not isinstance(value, dict):
                return default
            value = value.get(item, default)
        return value


def now():
    return datetime.now(timezone.utc).isoformat()


def db_path(args):
    return args.db or os.environ.get("BOOKTREE_DB") or DEFAULT_DB


def config_path(args):
    return args.config or os.environ.get("BOOKTREE_CONFIG") or DEFAULT_CONFIG


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity_hash TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            file TEXT,
            source_path TEXT,
            media_path TEXT,
            paths TEXT,
            status TEXT NOT NULL DEFAULT 'needs_metadata',
            failure_reason TEXT,
            asin TEXT,
            title TEXT,
            subtitle TEXT,
            authors TEXT,
            narrators TEXT,
            series TEXT,
            series_part TEXT,
            language TEXT,
            metadata_source TEXT,
            mam_count INTEGER NOT NULL DEFAULT 0,
            audible_count INTEGER NOT NULL DEFAULT 0,
            is_matched INTEGER NOT NULL DEFAULT 0,
            is_hardlinked INTEGER NOT NULL DEFAULT 0,
            last_searched_at TEXT,
            updated_at TEXT NOT NULL,
            raw_log_json TEXT
        );

        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            external_id TEXT,
            asin TEXT,
            title TEXT,
            subtitle TEXT,
            authors TEXT,
            narrators TEXT,
            series TEXT,
            language TEXT,
            duration TEXT,
            match_rate REAL,
            is_accepted INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS search_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            query_json TEXT NOT NULL,
            result_count INTEGER NOT NULL DEFAULT 0,
            best_match_id INTEGER,
            error TEXT,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            book_id INTEGER REFERENCES books(id) ON DELETE SET NULL,
            status TEXT NOT NULL,
            logs TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER REFERENCES books(id) ON DELETE CASCADE,
            type TEXT NOT NULL,
            message TEXT,
            payload_json TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()


def row_to_dict(row):
    return dict(row) if row is not None else None


def clean(value):
    if value is None:
        return ""
    value = str(value).strip()
    return "" if value.lower() in {"none", "null"} else value


def first(*values):
    for value in values:
        value = clean(value)
        if value:
            return value
    return ""


def truthy(value):
    return clean(value).lower() in {"1", "true", "yes", "y"}


def to_int(value):
    try:
        return int(float(clean(value) or 0))
    except ValueError:
        return 0


def identity_for(row):
    key = "|".join([clean(row.get("sourcePath")), clean(row.get("file")), clean(row.get("book"))])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def status_from_log(row, existing_status=None):
    if existing_status == "ignored":
        return "ignored"
    if truthy(row.get("isHardLinked")):
        return "processed"
    if truthy(row.get("isMatched")):
        return "matched"
    title = first(row.get("id3-title"), row.get("mam-title"), row.get("adb-title"), row.get("book"))
    authors = first(row.get("id3-authors"), row.get("mam-authors"), row.get("adb-authors"))
    asin = first(row.get("id3-asin"), row.get("mam-asin"), row.get("adb-asin"))
    if not (title and (authors or asin)):
        return "needs_metadata"
    if to_int(row.get("mamCount")) + to_int(row.get("audibleMatchCount")) > 1:
        return "multiple_matches"
    return "no_match"


def book_payload_from_log(row, existing_status=None):
    status = status_from_log(row, existing_status)
    return {
        "identity_hash": identity_for(row),
        "name": first(row.get("book"), row.get("file"), "Unknown book"),
        "file": clean(row.get("file")),
        "source_path": clean(row.get("sourcePath")),
        "media_path": clean(row.get("mediaPath")),
        "paths": clean(row.get("paths")),
        "status": status,
        "failure_reason": "" if status != "no_match" else "No accepted Booktree match",
        "asin": first(row.get("id3-asin"), row.get("mam-asin"), row.get("adb-asin")),
        "title": first(row.get("id3-title"), row.get("mam-title"), row.get("adb-title"), row.get("book")),
        "subtitle": first(row.get("id3-subtitle"), row.get("mam-subtitle"), row.get("adb-subtitle")),
        "authors": first(row.get("id3-authors"), row.get("mam-authors"), row.get("adb-authors")),
        "narrators": first(row.get("id3-narrators"), row.get("mam-narrators"), row.get("adb-narrators")),
        "series": first(row.get("id3-series"), row.get("mam-series"), row.get("adb-series")),
        "series_part": first(row.get("id3-seriesparts"), row.get("mam-seriesparts"), row.get("adb-seriesparts")),
        "language": first(row.get("id3-language"), row.get("mam-language"), row.get("adb-language"), "english"),
        "metadata_source": clean(row.get("metadatasource")),
        "mam_count": to_int(row.get("mamCount")),
        "audible_count": to_int(row.get("audibleMatchCount")),
        "is_matched": 1 if truthy(row.get("isMatched")) else 0,
        "is_hardlinked": 1 if truthy(row.get("isHardLinked")) else 0,
        "updated_at": now(),
        "raw_log_json": json.dumps(row),
    }


def upsert_book(conn, payload):
    existing = conn.execute(
        "SELECT id, status FROM books WHERE identity_hash = ?", (payload["identity_hash"],)
    ).fetchone()
    if existing:
        if existing["status"] == "ignored":
            payload["status"] = "ignored"
        fields = [key for key in payload.keys() if key != "identity_hash"]
        conn.execute(
            f"UPDATE books SET {', '.join(f'{field} = ?' for field in fields)} WHERE identity_hash = ?",
            [payload[field] for field in fields] + [payload["identity_hash"]],
        )
        return existing["id"]

    fields = list(payload.keys())
    conn.execute(
        f"INSERT INTO books ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
        [payload[field] for field in fields],
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def config_data(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def log_dir_from_config(path):
    try:
        return config_data(path).get("Config", {}).get("log_path") or "/logs"
    except Exception:
        return "/logs"


def latest_log(path):
    candidates = sorted(glob(os.path.join(path, "booktree_log_*.csv")))
    return candidates[-1] if candidates else None


def import_logs(args):
    cfg_path = config_path(args)
    log_file = args.log_file or latest_log(args.log_dir or log_dir_from_config(cfg_path))
    if not log_file or not os.path.exists(log_file):
        raise ValueError("No Booktree log file found to import")

    imported = 0
    with connect(db_path(args)) as conn:
        init_db(conn)
        with open(log_file, newline="", errors="ignore", encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            for row in reader:
                if not clean(row.get("book")) and not clean(row.get("file")):
                    continue
                payload = book_payload_from_log(row)
                book_id = upsert_book(conn, payload)
                add_event(conn, book_id, "imported", f"Imported from {log_file}", {"log_file": log_file})
                imported += 1
        conn.commit()
    return {"ok": True, "log_file": log_file, "imported": imported}


def add_event(conn, book_id, event_type, message="", payload=None):
    conn.execute(
        "INSERT INTO events (book_id, type, message, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (book_id, event_type, message, json.dumps(payload or {}), now()),
    )


def stats(args):
    with connect(db_path(args)) as conn:
        init_db(conn)
        rows = conn.execute("SELECT status, COUNT(*) AS count FROM books GROUP BY status").fetchall()
    counts = {status: 0 for status in STATUSES}
    for row in rows:
        counts[row["status"]] = row["count"]
    counts["total"] = sum(counts.values())
    return {"ok": True, "counts": counts}


def list_books(args):
    clauses = []
    values = []
    if args.status and args.status != "all":
        clauses.append("status = ?")
        values.append(args.status)
    if args.q:
        clauses.append("(name LIKE ? OR title LIKE ? OR authors LIKE ? OR asin LIKE ? OR failure_reason LIKE ?)")
        values.extend([f"%{args.q}%"] * 5)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect(db_path(args)) as conn:
        init_db(conn)
        rows = conn.execute(
            f"""
            SELECT * FROM books
            {where}
            ORDER BY
              CASE status
                WHEN 'needs_metadata' THEN 1
                WHEN 'no_match' THEN 2
                WHEN 'multiple_matches' THEN 3
                WHEN 'failed' THEN 4
                WHEN 'matched' THEN 5
                WHEN 'processed' THEN 6
                ELSE 7
              END,
              updated_at DESC
            LIMIT ?
            """,
            values + [args.limit],
        ).fetchall()
    return {"ok": True, "books": [row_to_dict(row) for row in rows]}


def get_book(conn, book_id):
    book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        raise ValueError(f"Book {book_id} was not found")
    matches = conn.execute(
        "SELECT * FROM matches WHERE book_id = ? ORDER BY is_accepted DESC, match_rate DESC, id DESC",
        (book_id,),
    ).fetchall()
    attempts = conn.execute(
        "SELECT * FROM search_attempts WHERE book_id = ? ORDER BY id DESC LIMIT 10", (book_id,)
    ).fetchall()
    return {
        "book": row_to_dict(book),
        "matches": [row_to_dict(row) for row in matches],
        "search_attempts": [row_to_dict(row) for row in attempts],
    }


def get_book_command(args):
    with connect(db_path(args)) as conn:
        init_db(conn)
        payload = get_book(conn, args.id)
    return {"ok": True, **payload}


def update_book(args):
    payload = json.loads(args.payload)
    allowed = [
        "asin",
        "title",
        "subtitle",
        "authors",
        "narrators",
        "series",
        "series_part",
        "language",
        "failure_reason",
    ]
    fields = [field for field in allowed if field in payload]
    if not fields:
        return get_book_command(args)
    with connect(db_path(args)) as conn:
        init_db(conn)
        conn.execute(
            f"UPDATE books SET {', '.join(f'{field} = ?' for field in fields)}, updated_at = ? WHERE id = ?",
            [clean(payload[field]) for field in fields] + [now(), args.id],
        )
        add_event(conn, args.id, "updated", "Metadata updated from web UI", {field: payload[field] for field in fields})
        conn.commit()
        payload = get_book(conn, args.id)
    return {"ok": True, **payload}


def query_from_book(book):
    keywords = " ".join(value for value in [book["title"], book["series"], book["name"]] if value)
    return {
        "asin": book["asin"] or "",
        "title": book["title"] or book["name"] or "",
        "authors": book["authors"] or "",
        "narrators": book["narrators"] or "",
        "keywords": keywords,
        "language": book["language"] or "english",
    }


def book_to_match(provider, item, raw=None):
    raw = raw if raw is not None else {}
    return {
        "provider": provider,
        "external_id": getattr(item, "id", "") or getattr(item, "asin", "") or raw.get("asin", ""),
        "asin": getattr(item, "asin", "") or raw.get("asin", ""),
        "title": getattr(item, "title", "") or raw.get("title", ""),
        "subtitle": getattr(item, "subtitle", "") or raw.get("subtitle", ""),
        "authors": item.getAuthors() if hasattr(item, "getAuthors") else "",
        "narrators": item.getNarrators() if hasattr(item, "getNarrators") else "",
        "series": item.getSeriesParts() if hasattr(item, "getSeriesParts") else "",
        "language": getattr(item, "language", "") or raw.get("language", ""),
        "duration": str(getattr(item, "duration", "") or getattr(item, "length", "") or raw.get("runtime_length_min", "")),
        "match_rate": float(getattr(item, "matchRate", 0) or 0),
        "raw_json": json.dumps(raw or safe_book_dict(item)),
    }


def safe_book_dict(item):
    if item is None:
        return {}
    return {
        "asin": getattr(item, "asin", ""),
        "title": getattr(item, "title", ""),
        "subtitle": getattr(item, "subtitle", ""),
        "authors": item.getAuthors() if hasattr(item, "getAuthors") else "",
        "narrators": item.getNarrators() if hasattr(item, "getNarrators") else "",
        "series": item.getSeriesParts() if hasattr(item, "getSeriesParts") else "",
        "language": getattr(item, "language", ""),
        "matchRate": getattr(item, "matchRate", 0),
    }


def insert_matches(conn, book_id, matches):
    ids = []
    for match in matches:
        fields = [
            "book_id",
            "provider",
            "external_id",
            "asin",
            "title",
            "subtitle",
            "authors",
            "narrators",
            "series",
            "language",
            "duration",
            "match_rate",
            "raw_json",
            "created_at",
        ]
        values = [book_id] + [match.get(field, "") for field in fields[1:-1]] + [now()]
        conn.execute(
            f"INSERT INTO matches ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
            values,
        )
        ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    return ids


def search_provider(conn, cfg, book, provider):
    start = time.monotonic()
    query = query_from_book(book)
    matches = []
    error = ""
    output = ""
    try:
        noisy = io.StringIO()
        with contextlib.redirect_stdout(noisy):
            if provider == "mam":
                import myx_mam

                results = myx_mam.getMAMBook(
                    cfg,
                    titleFilename=query["title"],
                    authors=query["authors"],
                    extension=os.path.splitext(book["file"] or "")[1].replace(".", ""),
                )
                matches = [book_to_match("mam", item) for item in results]
            elif provider == "audible":
                import httpx
                import myx_audible

                products = myx_audible.getAudibleBook(
                    httpx,
                    cfg,
                    asin=query["asin"],
                    title=query["title"],
                    authors=query["authors"],
                    narrators=query["narrators"],
                    keywords=query["keywords"],
                    language=query["language"] or "english",
                )
                matches = [
                    book_to_match("audible", myx_audible.product2Book(product), product)
                    for product in products
                ]
        output = noisy.getvalue()
    except Exception as exc:
        error = str(exc)

    ids = insert_matches(conn, book["id"], matches)
    best_id = ids[0] if ids else None
    duration_ms = int((time.monotonic() - start) * 1000)
    conn.execute(
        """
        INSERT INTO search_attempts
          (book_id, provider, query_json, result_count, best_match_id, error, duration_ms, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (book["id"], provider, json.dumps(query), len(matches), best_id, error, duration_ms, now()),
    )
    add_event(
        conn,
        book["id"],
        "searched",
        f"{provider} search returned {len(matches)} matches",
        {"provider": provider, "error": error, "output": output[-4000:]},
    )
    return {"provider": provider, "matches": matches, "error": error, "duration_ms": duration_ms}


def search_book(args):
    providers = ["mam", "audible"] if args.provider == "both" else [args.provider]
    cfg = DictConfig(config_data(config_path(args)))
    with connect(db_path(args)) as conn:
        init_db(conn)
        conn.execute("DELETE FROM matches WHERE book_id = ? AND is_accepted = 0", (args.id,))
        book = row_to_dict(conn.execute("SELECT * FROM books WHERE id = ?", (args.id,)).fetchone())
        if not book:
            raise ValueError(f"Book {args.id} was not found")
        results = [search_provider(conn, cfg, book, provider) for provider in providers]
        mam_count = sum(len(result["matches"]) for result in results if result["provider"] == "mam")
        audible_count = sum(len(result["matches"]) for result in results if result["provider"] == "audible")
        status = "multiple_matches" if mam_count + audible_count > 1 else "no_match"
        if mam_count + audible_count == 1:
            status = "matched"
        if any(result["error"] for result in results) and mam_count + audible_count == 0:
            status = "failed"
        conn.execute(
            """
            UPDATE books
            SET status = ?, mam_count = ?, audible_count = ?, last_searched_at = ?, updated_at = ?,
                failure_reason = ?
            WHERE id = ?
            """,
            (
                status,
                mam_count,
                audible_count,
                now(),
                now(),
                "; ".join(result["error"] for result in results if result["error"]),
                args.id,
            ),
        )
        conn.commit()
        payload = get_book(conn, args.id)
    return {"ok": True, "results": results, **payload}


def accept_match(args):
    with connect(db_path(args)) as conn:
        init_db(conn)
        match = conn.execute(
            "SELECT * FROM matches WHERE id = ? AND book_id = ?", (args.match_id, args.id)
        ).fetchone()
        if not match:
            raise ValueError("Match was not found")
        conn.execute("UPDATE matches SET is_accepted = 0 WHERE book_id = ?", (args.id,))
        conn.execute("UPDATE matches SET is_accepted = 1 WHERE id = ?", (args.match_id,))
        conn.execute(
            """
            UPDATE books
            SET asin = ?, title = ?, subtitle = ?, authors = ?, narrators = ?, series = ?,
                language = ?, status = 'matched', is_matched = 1, metadata_source = ?,
                failure_reason = '', updated_at = ?
            WHERE id = ?
            """,
            (
                match["asin"],
                match["title"],
                match["subtitle"],
                match["authors"],
                match["narrators"],
                match["series"],
                match["language"] or "english",
                match["provider"],
                now(),
                args.id,
            ),
        )
        add_event(conn, args.id, "match_accepted", f"Accepted {match['provider']} match", {"match_id": args.match_id})
        conn.commit()
        payload = get_book(conn, args.id)
    return {"ok": True, **payload}


def mark_ignored(args):
    with connect(db_path(args)) as conn:
        init_db(conn)
        conn.execute(
            "UPDATE books SET status = 'ignored', updated_at = ? WHERE id = ?", (now(), args.id)
        )
        add_event(conn, args.id, "ignored", "Marked ignored from web UI")
        conn.commit()
        payload = get_book(conn, args.id)
    return {"ok": True, **payload}


def create_job(conn, job_type, book_id):
    ts = now()
    conn.execute(
        "INSERT INTO jobs (type, book_id, status, logs, error, created_at, updated_at) VALUES (?, ?, 'running', '', '', ?, ?)",
        (job_type, book_id, ts, ts),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def complete_job(conn, job_id, status, logs="", error=""):
    conn.execute(
        "UPDATE jobs SET status = ?, logs = ?, error = ?, updated_at = ? WHERE id = ?",
        (status, logs, error, now(), job_id),
    )


def log_row_for_book(book, match):
    raw = json.loads(book["raw_log_json"] or "{}")
    raw.update(
        {
            "book": book["name"],
            "file": book["file"],
            "paths": book["paths"],
            "isMatched": "True",
            "isHardLinked": "False",
            "metadatasource": match["provider"],
            "id3-asin": book["asin"] or "",
            "id3-title": book["title"] or "",
            "id3-subtitle": book["subtitle"] or "",
            "id3-authors": book["authors"] or "",
            "id3-narrators": book["narrators"] or "",
            "id3-seriesparts": book["series_part"] or book["series"] or "",
            "id3-language": book["language"] or "english",
            "sourcePath": book["source_path"],
            "mediaPath": book["media_path"],
        }
    )
    prefix = "mam" if match["provider"] == "mam" else "adb"
    raw.update(
        {
            f"{prefix}-asin": match["asin"] or "",
            f"{prefix}-title": match["title"] or "",
            f"{prefix}-subtitle": match["subtitle"] or "",
            f"{prefix}-authors": match["authors"] or "",
            f"{prefix}-narrators": match["narrators"] or "",
            f"{prefix}-seriesparts": match["series"] or "",
            f"{prefix}-language": match["language"] or "english",
        }
    )
    return raw


def process_book(args):
    with connect(db_path(args)) as conn:
        init_db(conn)
        book = row_to_dict(conn.execute("SELECT * FROM books WHERE id = ?", (args.id,)).fetchone())
        if not book:
            raise ValueError(f"Book {args.id} was not found")
        match = row_to_dict(
            conn.execute(
                "SELECT * FROM matches WHERE book_id = ? AND is_accepted = 1 ORDER BY id DESC LIMIT 1",
                (args.id,),
            ).fetchone()
        )
        if not match:
            raise ValueError("Accept a match before processing this book")
        job_id = create_job(conn, "process_book", args.id)
        conn.commit()

    logs = io.StringIO()
    error = ""
    status = "complete"
    try:
        import booktree

        cfg_data = config_data(config_path(args))
        cfg_data.setdefault("Config", {})["metadata"] = "log"
        headers = list(log_row_for_book(book, match).keys())
        try:
            import myx_utilities

            headers = list(myx_utilities.getLogHeaders().keys())
        except Exception:
            pass
        row = log_row_for_book(book, match)
        with tempfile.TemporaryDirectory(prefix="booktree-webui-") as tmp:
            input_csv = os.path.join(tmp, "input.csv")
            output_csv = os.path.join(tmp, "output.csv")
            with open(input_csv, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=headers)
                writer.writeheader()
                writer.writerow({field: row.get(field, "") for field in headers})
            with contextlib.redirect_stdout(logs):
                booktree.buildTreeFromLog(input_csv, output_csv, DictConfig(cfg_data))
    except Exception as exc:
        error = str(exc)
        status = "failed"

    with connect(db_path(args)) as conn:
        init_db(conn)
        complete_job(conn, job_id, status, logs.getvalue(), error)
        conn.execute(
            "UPDATE books SET status = ?, is_hardlinked = ?, failure_reason = ?, updated_at = ? WHERE id = ?",
            ("processed" if status == "complete" else "failed", 1 if status == "complete" else 0, error, now(), args.id),
        )
        add_event(conn, args.id, "processed" if status == "complete" else "process_failed", error or "Processed from web UI")
        conn.commit()
        payload = get_book(conn, args.id)
    return {"ok": status == "complete", "job_id": job_id, "logs": logs.getvalue(), "error": error, **payload}


def output(payload):
    print(json.dumps(payload, default=str))


def main():
    parser = argparse.ArgumentParser(description="Booktree web UI worker")
    parser.add_argument("--db")
    parser.add_argument("--config")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db")
    import_parser = sub.add_parser("import-logs")
    import_parser.add_argument("--log-file")
    import_parser.add_argument("--log-dir")
    sub.add_parser("stats")
    list_parser = sub.add_parser("list-books")
    list_parser.add_argument("--status", default="all")
    list_parser.add_argument("--q", default="")
    list_parser.add_argument("--limit", type=int, default=200)
    get_parser = sub.add_parser("get-book")
    get_parser.add_argument("--id", type=int, required=True)
    update_parser = sub.add_parser("update-book")
    update_parser.add_argument("--id", type=int, required=True)
    update_parser.add_argument("--payload", required=True)
    search_parser = sub.add_parser("search")
    search_parser.add_argument("--id", type=int, required=True)
    search_parser.add_argument("--provider", choices=["mam", "audible", "both"], default="both")
    accept_parser = sub.add_parser("accept-match")
    accept_parser.add_argument("--id", type=int, required=True)
    accept_parser.add_argument("--match-id", type=int, required=True)
    ignore_parser = sub.add_parser("mark-ignored")
    ignore_parser.add_argument("--id", type=int, required=True)
    process_parser = sub.add_parser("process")
    process_parser.add_argument("--id", type=int, required=True)

    args = parser.parse_args()
    try:
        if args.command == "init-db":
            with connect(db_path(args)) as conn:
                init_db(conn)
            output({"ok": True, "db": db_path(args)})
        elif args.command == "import-logs":
            output(import_logs(args))
        elif args.command == "stats":
            output(stats(args))
        elif args.command == "list-books":
            output(list_books(args))
        elif args.command == "get-book":
            output(get_book_command(args))
        elif args.command == "update-book":
            output(update_book(args))
        elif args.command == "search":
            output(search_book(args))
        elif args.command == "accept-match":
            output(accept_match(args))
        elif args.command == "mark-ignored":
            output(mark_ignored(args))
        elif args.command == "process":
            output(process_book(args))
    except Exception as exc:
        output({"ok": False, "error": str(exc)})
        raise SystemExit(1)


if __name__ == "__main__":
    main()
