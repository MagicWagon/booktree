#!/usr/bin/env python3
import argparse
import contextlib
import csv
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from glob import glob
from pathlib import Path


DEFAULT_DB = "/config/booktree.db"
DEFAULT_CONFIG = "/config/config.json"
DEFAULT_CONFIG_DIR = "/config"
CONFIG_DESCRIPTIONS = {
    "metadata": "Metadata search mode. Common values are mam, audible, mam-audible, or log.",
    "matchrate": "Minimum fuzzy match score required before Booktree treats a result as a match.",
    "fuzzy_match": "Fuzzy matching strategy used for title comparisons. Common values are partial, token_sort, and ratio.",
    "log_path": "Directory where Booktree writes CSV run logs.",
    "cache_path": "Directory where Booktree stores cache and state files.",
    "last_scan": "Path to the last scan log used by older workflows.",
    "session": "Static MAM session cookie value used when mousehole is disabled or unavailable.",
    "mousehole_enabled": "When enabled, Booktree reads the live MAM cookie from the mousehole state file.",
    "mousehole_state_file": "Path to mousehole state.json. Booktree reads currentCookie from this file.",
    "paths": "Input/output path mappings that tell Booktree where to scan and where to place media.",
    "files": "Glob patterns to include when scanning this source path.",
    "source_path": "Root folder where Booktree looks for source downloads.",
    "media_path": "Destination media root for linked or copied books.",
    "dry_run": "Preview actions without creating links, files, or OPF output.",
    "verbose": "Print extra processing detail while Booktree runs.",
    "multibook": "Treat files as separate books instead of grouping a folder as one book.",
    "ebooks": "Enable ebook handling in addition to audiobook handling.",
    "no_opf": "Skip writing OPF metadata files.",
    "no_cache": "Bypass cached search results and force fresh lookups.",
    "fixid3": "Attempt to repair or rewrite ID3 metadata for supported audio files.",
    "add_narrators": "Add narrator names to generated metadata/path output where supported.",
    "interactive": "Prompt for manual choices when running the CLI interactively.",
    "hardlink": "Create hardlinks instead of copying files when possible.",
    "ingest_calibre": "Enable Calibre ingest workflow for ebook imports.",
    "multi_author": "Template behavior for books with multiple authors.",
    "in_series": "Target path template for books that belong to a series.",
    "no_series": "Target path template for books without series metadata.",
    "disc_folder": "Folder naming template for multi-disc output.",
    "calibre_ingest_path": "Folder where ebook files should be staged for Calibre import.",
    "skip_series": "Ignore series metadata while building output paths.",
    "kw_ignore": "Characters removed or ignored while cleaning search terms.",
    "kw_ignore_words": "Words ignored while cleaning title/search terms.",
    "title_patterns": "Regular expression patterns stripped from titles before matching.",
}
STATUSES = [
    "needs_metadata",
    "no_match",
    "multiple_matches",
    "needs_split_review",
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


def config_root(args=None):
    return getattr(args, "config_dir", None) or os.environ.get("BOOKTREE_CONFIG_DIR") or DEFAULT_CONFIG_DIR


def safe_config_path(value, args=None, must_exist=False):
    if not clean(value):
        raise ValueError("Config path is required")
    root = Path(config_root(args)).resolve()
    raw = Path(clean(value))
    candidate = raw if raw.is_absolute() else root / raw
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Config files must be inside /config") from exc
    if resolved.name.startswith("."):
        raise ValueError("Hidden config files are not allowed")
    if resolved.suffix.lower() != ".json":
        raise ValueError("Config files must use a .json extension")
    if must_exist and not resolved.exists():
        raise ValueError(f"Config file was not found: {resolved}")
    return str(resolved)


def active_config_path(args):
    try:
        with connect(db_path(args)) as conn:
            init_db(conn)
            row = conn.execute("SELECT value FROM app_settings WHERE key = 'active_config_path'").fetchone()
            if not row:
                return None
            path = safe_config_path(row["value"], args, must_exist=True)
            return path
    except Exception:
        return None


def config_path(args):
    if getattr(args, "config", None):
        return safe_config_path(args.config, args, must_exist=True)
    active = active_config_path(args)
    if active:
        return active
    env_config = os.environ.get("BOOKTREE_CONFIG")
    if env_config:
        return safe_config_path(env_config, args, must_exist=False)
    return safe_config_path("config.json", args, must_exist=False)


@contextlib.contextmanager
def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


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

        CREATE TABLE IF NOT EXISTS log_imports (
            path TEXT PRIMARY KEY,
            size INTEGER NOT NULL,
            mtime REAL NOT NULL,
            imported_rows INTEGER NOT NULL DEFAULT 0,
            imported_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS book_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_hash TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            source_path TEXT,
            media_path TEXT,
            source_folder TEXT,
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
            file_count INTEGER NOT NULL DEFAULT 0,
            detection_reason TEXT,
            detection_confidence REAL NOT NULL DEFAULT 1.0,
            user_edited INTEGER NOT NULL DEFAULT 0,
            last_searched_at TEXT,
            updated_at TEXT NOT NULL,
            raw_log_json TEXT
        );

        CREATE TABLE IF NOT EXISTS book_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_hash TEXT NOT NULL UNIQUE,
            group_id INTEGER NOT NULL REFERENCES book_groups(id) ON DELETE CASCADE,
            legacy_book_id INTEGER REFERENCES books(id) ON DELETE SET NULL,
            book_name TEXT,
            file TEXT,
            source_path TEXT,
            media_path TEXT,
            paths TEXT,
            status TEXT NOT NULL DEFAULT 'needs_metadata',
            asin TEXT,
            title TEXT,
            authors TEXT,
            narrators TEXT,
            series TEXT,
            language TEXT,
            metadata_source TEXT,
            mam_count INTEGER NOT NULL DEFAULT 0,
            audible_count INTEGER NOT NULL DEFAULT 0,
            is_matched INTEGER NOT NULL DEFAULT 0,
            is_hardlinked INTEGER NOT NULL DEFAULT 0,
            raw_log_json TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS group_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES book_groups(id) ON DELETE CASCADE,
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

        CREATE TABLE IF NOT EXISTS group_search_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES book_groups(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            query_json TEXT NOT NULL,
            result_count INTEGER NOT NULL DEFAULT 0,
            best_match_id INTEGER,
            error TEXT,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    ensure_column(conn, "jobs", "payload_json", "TEXT")
    ensure_column(conn, "jobs", "exit_code", "INTEGER")
    ensure_column(conn, "book_groups", "output_path", "TEXT")
    ensure_column(conn, "book_files", "output_path", "TEXT")
    conn.commit()


def ensure_column(conn, table, column, definition):
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def row_to_dict(row):
    return dict(row) if row is not None else None


def clean(value):
    if value is None:
        return ""
    value = str(value).strip()
    return "" if value.lower() in {"none", "null"} else value


def sanitize_path_token(value):
    value = clean(value)
    value = re.sub(r'[<>:"/\\\\|?*]', "", value)
    value = re.sub(r"\s+", " ", value).strip().rstrip(".")
    return value


def strip_accents_local(value):
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", clean(value)) if unicodedata.category(c) != "Mn")


def cleanse_author_local(author):
    value = strip_accents_local(author)
    for token in ["- editor", "- contributor", " - ", "'"]:
        value = value.replace(token, "")
    return " ".join(value.replace(".", " ").split())


def cleanse_title_local(title):
    value = clean(title)
    for token in [" (Unabridged)", "m4b", "mp3", ",", "- "]:
        value = value.replace(token, " ")
    value = strip_accents_local(value)
    value = re.sub(r"\bBook(\s)?(\d)+\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"(:(\s)?([a-zA-Z0-9_'\.\s]{2,})*)", "", value, flags=re.IGNORECASE)
    return value.strip()


def cleanse_series_local(series):
    value = clean(series)
    for token in [":", "'"]:
        value = value.replace(token, "")
    return value.strip()


def is_multi_cd_local(parent):
    return bool(re.search(r"(cd|disc)\s?\d+$", clean(parent), re.IGNORECASE))


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


def hash_value(*parts):
    return hashlib.sha256("|".join(clean(part) for part in parts).encode("utf-8")).hexdigest()


def source_folder_for(row):
    source = clean(row.get("sourcePath"))
    file_path = clean(row.get("file"))
    if not file_path:
        return clean(row.get("book"))
    try:
        rel = os.path.relpath(file_path, source) if source and os.path.isabs(file_path) else file_path
    except ValueError:
        rel = file_path
    parent = os.path.dirname(rel)
    return parent or clean(row.get("book")) or os.path.basename(file_path)


def group_hash_for(row):
    source = clean(row.get("sourcePath"))
    media = clean(row.get("mediaPath"))
    book = clean(row.get("book"))
    folder = source_folder_for(row)
    group_name = book or folder or clean(row.get("file"))
    return hash_value(source, media, group_name)


def track_like_filename(file_path):
    name = os.path.splitext(os.path.basename(clean(file_path)))[0].lower()
    return bool(
        re.search(r"(^|\b)(cd|disc|disk|track|chapter|part)\s*[-_. ]*\d+", name)
        or re.search(r"^\d{1,4}(\s|-|_|\.|$)", name)
        or re.search(r"^\d{1,2}-\d{1,3}(\s|-|_|\.|$)", name)
    )


def suspect_group_from_files(rows):
    if len(rows) <= 1:
        return False, "single_file", 1.0
    asins = {clean(row["asin"]).lower() for row in rows if clean(row["asin"])}
    titles = {clean(row["title"]).lower() for row in rows if clean(row["title"])}
    authors = {clean(row["authors"]).lower() for row in rows if clean(row["authors"])}
    files = [clean(row["file"]) for row in rows]
    all_track_like = all(track_like_filename(file_path) for file_path in files if file_path)
    if len(asins) > 1:
        return True, "multiple_asins", 0.35
    if len(titles) > 1 and len(authors) > 1 and not all_track_like:
        return True, "multiple_titles_and_authors", 0.45
    if len(titles) > 3 and not all_track_like:
        return True, "many_distinct_titles", 0.5
    if all_track_like:
        return False, "track_like_files", 0.9
    return False, "shared_folder", 0.75


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


def status_from_counts(row, existing_status=None):
    status = status_from_log(row, existing_status)
    if status in {"matched", "processed", "needs_metadata"}:
        return status
    if to_int(row.get("mamCount")) + to_int(row.get("audibleMatchCount")) > 1:
        return "multiple_matches"
    return status


def book_payload_from_log(row, existing_status=None):
    status = status_from_counts(row, existing_status)
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


def group_payload_from_log(row, existing_status=None):
    payload = book_payload_from_log(row, existing_status)
    payload.pop("identity_hash", None)
    payload.pop("file", None)
    payload["group_hash"] = group_hash_for(row)
    payload["source_folder"] = source_folder_for(row)
    payload["file_count"] = 1
    payload["detection_reason"] = "initial_import"
    payload["detection_confidence"] = 1.0
    payload["user_edited"] = 0
    return payload


def file_payload_from_log(row, group_id, legacy_book_id=None):
    payload = book_payload_from_log(row)
    return {
        "file_hash": identity_for(row),
        "group_id": group_id,
        "legacy_book_id": legacy_book_id,
        "book_name": payload["name"],
        "file": payload["file"],
        "source_path": payload["source_path"],
        "media_path": payload["media_path"],
        "paths": payload["paths"],
        "status": payload["status"],
        "asin": payload["asin"],
        "title": payload["title"],
        "authors": payload["authors"],
        "narrators": payload["narrators"],
        "series": payload["series"],
        "language": payload["language"],
        "metadata_source": payload["metadata_source"],
        "mam_count": payload["mam_count"],
        "audible_count": payload["audible_count"],
        "is_matched": payload["is_matched"],
        "is_hardlinked": payload["is_hardlinked"],
        "output_path": clean(row.get("paths")) if payload["is_hardlinked"] else "",
        "raw_log_json": payload["raw_log_json"],
        "updated_at": payload["updated_at"],
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


def upsert_group(conn, payload):
    existing = conn.execute(
        "SELECT id, status, user_edited FROM book_groups WHERE group_hash = ?", (payload["group_hash"],)
    ).fetchone()
    if existing:
        if existing["status"] == "ignored":
            payload["status"] = "ignored"
        if existing["user_edited"]:
            for field in ["asin", "title", "subtitle", "authors", "narrators", "series", "series_part", "language"]:
                payload.pop(field, None)
            payload["user_edited"] = 1
        fields = [key for key in payload.keys() if key != "group_hash"]
        conn.execute(
            f"UPDATE book_groups SET {', '.join(f'{field} = ?' for field in fields)} WHERE group_hash = ?",
            [payload[field] for field in fields] + [payload["group_hash"]],
        )
        return existing["id"]

    fields = list(payload.keys())
    conn.execute(
        f"INSERT INTO book_groups ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
        [payload[field] for field in fields],
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def upsert_file(conn, payload):
    existing = conn.execute("SELECT id FROM book_files WHERE file_hash = ?", (payload["file_hash"],)).fetchone()
    if existing:
        fields = [key for key in payload.keys() if key != "file_hash"]
        conn.execute(
            f"UPDATE book_files SET {', '.join(f'{field} = ?' for field in fields)} WHERE file_hash = ?",
            [payload[field] for field in fields] + [payload["file_hash"]],
        )
        return existing["id"]

    fields = list(payload.keys())
    conn.execute(
        f"INSERT INTO book_files ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
        [payload[field] for field in fields],
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def group_status_from_files(rows, existing_status=None):
    if existing_status == "ignored":
        return "ignored"
    suspect, _reason, _confidence = suspect_group_from_files(rows)
    if suspect:
        return "needs_split_review"
    if rows and all(row["is_hardlinked"] for row in rows):
        return "processed"
    if rows and all(row["is_matched"] for row in rows):
        return "matched"
    if any(row["status"] == "failed" for row in rows):
        return "failed"
    if any(row["status"] == "needs_metadata" for row in rows):
        return "needs_metadata"
    if sum(int(row["mam_count"] or 0) + int(row["audible_count"] or 0) for row in rows) > 1:
        return "multiple_matches"
    return "no_match"


def recompute_group(conn, group_id):
    group = conn.execute("SELECT * FROM book_groups WHERE id = ?", (group_id,)).fetchone()
    if not group:
        return
    rows = conn.execute("SELECT * FROM book_files WHERE group_id = ? ORDER BY file", (group_id,)).fetchall()
    if not rows:
        return
    first_row = rows[0]
    suspect, reason, confidence = suspect_group_from_files(rows)
    status = group_status_from_files(rows, group["status"])
    if group["status"] == "ignored":
        status = "ignored"
    metadata = {}
    if not group["user_edited"]:
        metadata = {
            "asin": first(*(row["asin"] for row in rows)),
            "title": first(*(row["title"] for row in rows), group["name"]),
            "authors": first(*(row["authors"] for row in rows)),
            "narrators": first(*(row["narrators"] for row in rows)),
            "series": first(*(row["series"] for row in rows)),
            "language": first(*(row["language"] for row in rows), "english"),
        }
    output_path = first(*(row["output_path"] for row in rows if row["is_hardlinked"]), group["output_path"])
    conn.execute(
        f"""
        UPDATE book_groups
        SET status = ?,
            failure_reason = ?,
            mam_count = ?,
            audible_count = ?,
            is_matched = ?,
            is_hardlinked = ?,
            file_count = ?,
            detection_reason = ?,
            detection_confidence = ?,
            output_path = ?,
            updated_at = ?
            {''.join(f', {field} = ?' for field in metadata.keys())}
        WHERE id = ?
        """,
        [
            status,
            "Possible multiple books in one folder" if suspect else ("" if status != "no_match" else "No accepted Booktree match"),
            sum(int(row["mam_count"] or 0) for row in rows),
            sum(int(row["audible_count"] or 0) for row in rows),
            1 if all(row["is_matched"] for row in rows) else 0,
            1 if all(row["is_hardlinked"] for row in rows) else 0,
            len(rows),
            reason,
            confidence,
            output_path,
            now(),
            *metadata.values(),
            group_id,
        ],
    )


def upsert_grouped_row(conn, row, source="imported"):
    legacy_id = upsert_book(conn, book_payload_from_log(row))
    group_id = upsert_group(conn, group_payload_from_log(row))
    upsert_file(conn, file_payload_from_log(row, group_id, legacy_id))
    recompute_group(conn, group_id)
    add_event(conn, legacy_id, source, f"{source} grouped file row", {"group_id": group_id})
    return legacy_id, group_id


def book_to_log_rows(book, cfg):
    rows = []
    for book_file in getattr(book, "files", []):
        row = book.getLogRecord(book_file, cfg)
        rows.append({key: clean(value) for key, value in row.items()})
    return rows


def sync_books(books, cfg, db_file=None, source="cli"):
    db_file = db_file or os.environ.get("BOOKTREE_DB") or DEFAULT_DB
    synced = 0
    with connect(db_file) as conn:
        init_db(conn)
        for book in books:
            for row in book_to_log_rows(book, cfg):
                upsert_grouped_row(conn, row, "synced")
                synced += 1
        conn.commit()
    return synced


def sync_books_safely(books, cfg, db_file=None, source="cli"):
    try:
        synced = sync_books(books, cfg, db_file, source)
        if synced:
            print(f"Updated Booktree web UI state for {synced} book file(s).")
    except Exception as exc:
        print(f"Warning: failed to update Booktree web UI state: {exc}")


def config_data(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def default_config_data():
    template = Path(__file__).resolve().parent / "templates" / "default_config.cfg"
    return config_data(str(template))


def config_summary(path, args):
    resolved = safe_config_path(path, args, must_exist=True)
    stat = os.stat(resolved)
    root = Path(config_root(args)).resolve()
    return {
        "name": Path(resolved).name,
        "path": resolved,
        "relative_path": str(Path(resolved).resolve().relative_to(root)),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }


def validate_config_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("Config payload must be a JSON object")
    if "Config" not in payload or not isinstance(payload["Config"], dict):
        raise ValueError("Config payload must contain a Config object")
    return payload


def config_payload_from_args(args):
    return validate_config_payload(json.loads(args.payload))


def list_configs(args):
    root = Path(config_root(args)).resolve()
    root.mkdir(parents=True, exist_ok=True)
    configs = []
    for item in sorted(root.glob("*.json")):
        if item.name.startswith(".") or not item.is_file():
            continue
        try:
            configs.append(config_summary(str(item), args))
        except ValueError:
            continue
    active = active_config_path(args)
    if not active:
        default_path = safe_config_path("config.json", args, must_exist=False)
        active = default_path
    return {
        "ok": True,
        "config_root": str(root),
        "active_config": active,
        "configs": configs,
        "schema": CONFIG_DESCRIPTIONS,
    }


def get_config(args):
    requested = getattr(args, "path", None) or active_config_path(args) or "config.json"
    path = safe_config_path(requested, args, must_exist=True)
    data = config_data(path)
    return {"ok": True, "config": data, "file": config_summary(path, args), "schema": CONFIG_DESCRIPTIONS}


def atomic_write_json(path, payload):
    directory = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(prefix=".booktree-config-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def set_active_config_path(args, path):
    resolved = safe_config_path(path, args, must_exist=True)
    with connect(db_path(args)) as conn:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES ('active_config_path', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (resolved, now()),
        )
        conn.commit()
    return resolved


def save_config(args):
    path = safe_config_path(args.path, args, must_exist=True)
    payload = config_payload_from_args(args)
    atomic_write_json(path, payload)
    return {"ok": True, "message": "Config saved", "file": config_summary(path, args), "config": payload}


def save_config_as(args):
    path = safe_config_path(args.name, args, must_exist=False)
    payload = config_payload_from_args(args)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if os.path.exists(path) and not getattr(args, "overwrite", False):
        raise ValueError(f"Config already exists: {Path(path).name}")
    atomic_write_json(path, payload)
    active = set_active_config_path(args, path)
    return {
        "ok": True,
        "message": "Config saved as active config",
        "file": config_summary(path, args),
        "active_config": active,
        "config": payload,
    }


def set_active_config(args):
    active = set_active_config_path(args, args.path)
    return {"ok": True, "message": "Active config updated", "active_config": active}


def full_run_config_path(args):
    with connect(db_path(args)) as conn:
        init_db(conn)
        row = conn.execute("SELECT value FROM app_settings WHERE key = 'active_config_path'").fetchone()
    if row:
        return safe_config_path(row["value"], args, must_exist=True)
    if getattr(args, "config", None):
        return safe_config_path(args.config, args, must_exist=True)
    env_config = os.environ.get("BOOKTREE_CONFIG")
    if env_config:
        return safe_config_path(env_config, args, must_exist=True)
    return safe_config_path("config.json", args, must_exist=True)


def log_dir_from_config(path):
    try:
        return config_data(path).get("Config", {}).get("log_path") or "/logs"
    except Exception:
        return "/logs"


def latest_log(path):
    candidates = sorted(glob(os.path.join(path, "booktree_log_*.csv")))
    return candidates[-1] if candidates else None


def log_signature(log_file):
    stat = os.stat(log_file)
    return os.path.abspath(log_file), stat.st_size, stat.st_mtime


def was_log_imported(conn, log_file):
    path, size, mtime = log_signature(log_file)
    existing = conn.execute(
        "SELECT path FROM log_imports WHERE path = ? AND size = ? AND mtime = ?",
        (path, size, mtime),
    ).fetchone()
    return existing is not None


def mark_log_imported(conn, log_file, imported):
    path, size, mtime = log_signature(log_file)
    conn.execute(
        """
        INSERT INTO log_imports (path, size, mtime, imported_rows, imported_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
          size = excluded.size,
          mtime = excluded.mtime,
          imported_rows = excluded.imported_rows,
          imported_at = excluded.imported_at
        """,
        (path, size, mtime, imported, now()),
    )


def import_log_file(conn, log_file, force=False):
    if not log_file or not os.path.exists(log_file):
        return 0
    if not force and was_log_imported(conn, log_file):
        return 0
    imported = 0
    with open(log_file, newline="", errors="ignore", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            if not clean(row.get("book")) and not clean(row.get("file")):
                continue
            upsert_grouped_row(conn, row, "imported")
            imported += 1
    mark_log_imported(conn, log_file, imported)
    return imported


def log_files_to_import(args):
    if getattr(args, "log_file", None):
        return [args.log_file]
    cfg_path = config_path(args)
    log_dir = getattr(args, "log_dir", None) or log_dir_from_config(cfg_path)
    return sorted(glob(os.path.join(log_dir, "booktree_log_*.csv")))


def import_logs(args, force=False, missing_ok=False):
    log_files = log_files_to_import(args)
    if not log_files and not missing_ok:
        raise ValueError("No Booktree log files found to import")

    imported = 0
    scanned = 0
    with connect(db_path(args)) as conn:
        init_db(conn)
        for log_file in log_files:
            scanned += 1
            imported += import_log_file(conn, log_file, force=force or bool(getattr(args, "log_file", None)))
        conn.commit()
    return {"ok": True, "scanned": scanned, "imported": imported}


def sync_logs_if_available(args):
    try:
        result = import_logs(args, missing_ok=True)
        with connect(db_path(args)) as conn:
            init_db(conn)
            migrate_legacy_books(conn)
            conn.commit()
        return result
    except Exception:
        return {"ok": False, "scanned": 0, "imported": 0}


def row_from_legacy_book(book):
    raw = {}
    try:
        raw = json.loads(book["raw_log_json"] or "{}")
    except Exception:
        raw = {}
    raw.update(
        {
            "book": raw.get("book") or book["name"],
            "file": raw.get("file") or book["file"],
            "paths": raw.get("paths") or book["paths"],
            "isMatched": raw.get("isMatched") if "isMatched" in raw else bool(book["is_matched"]),
            "isHardLinked": raw.get("isHardLinked") if "isHardLinked" in raw else bool(book["is_hardlinked"]),
            "mamCount": raw.get("mamCount") or book["mam_count"],
            "audibleMatchCount": raw.get("audibleMatchCount") or book["audible_count"],
            "metadatasource": raw.get("metadatasource") or book["metadata_source"],
            "id3-asin": raw.get("id3-asin") or book["asin"],
            "id3-title": raw.get("id3-title") or book["title"],
            "id3-subtitle": raw.get("id3-subtitle") or book["subtitle"],
            "id3-authors": raw.get("id3-authors") or book["authors"],
            "id3-narrators": raw.get("id3-narrators") or book["narrators"],
            "id3-seriesparts": raw.get("id3-seriesparts") or book["series_part"] or book["series"],
            "id3-language": raw.get("id3-language") or book["language"],
            "sourcePath": raw.get("sourcePath") or book["source_path"],
            "mediaPath": raw.get("mediaPath") or book["media_path"],
        }
    )
    return raw


def migrate_legacy_books(conn):
    rows = conn.execute(
        """
        SELECT b.*
        FROM books b
        LEFT JOIN book_files f ON f.legacy_book_id = b.id
        WHERE f.id IS NULL
        """
    ).fetchall()
    for row in rows:
        upsert_grouped_row(conn, row_from_legacy_book(row), "migrated")


def add_event(conn, book_id, event_type, message="", payload=None):
    conn.execute(
        "INSERT INTO events (book_id, type, message, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (book_id, event_type, message, json.dumps(payload or {}), now()),
    )


def add_group_event(conn, group_id, event_type, message="", payload=None):
    conn.execute(
        "INSERT INTO events (book_id, type, message, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (None, event_type, message, json.dumps({"group_id": group_id, **(payload or {})}), now()),
    )


def stats(args):
    sync_logs_if_available(args)
    with connect(db_path(args)) as conn:
        init_db(conn)
        rows = conn.execute("SELECT status, COUNT(*) AS count FROM book_groups GROUP BY status").fetchall()
    counts = {status: 0 for status in STATUSES}
    for row in rows:
        counts[row["status"]] = row["count"]
    counts["total"] = sum(counts.values())
    return {"ok": True, "counts": counts}


def list_books(args):
    sync_logs_if_available(args)
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
            SELECT * FROM book_groups
            {where}
            ORDER BY
              CASE status
                WHEN 'needs_metadata' THEN 1
                WHEN 'no_match' THEN 2
                WHEN 'multiple_matches' THEN 3
                WHEN 'needs_split_review' THEN 4
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


def get_book(conn, book_id, args=None):
    book = conn.execute("SELECT * FROM book_groups WHERE id = ?", (book_id,)).fetchone()
    if not book:
        raise ValueError(f"Book group {book_id} was not found")
    matches = conn.execute(
        "SELECT * FROM group_matches WHERE group_id = ? ORDER BY is_accepted DESC, match_rate DESC, id DESC",
        (book_id,),
    ).fetchall()
    attempts = conn.execute(
        "SELECT * FROM group_search_attempts WHERE group_id = ? ORDER BY id DESC LIMIT 10", (book_id,)
    ).fetchall()
    files = conn.execute(
        "SELECT * FROM book_files WHERE group_id = ? ORDER BY file", (book_id,)
    ).fetchall()
    accepted_match = next((row for row in matches if row["is_accepted"]), None)
    preview = {
        "current_output_path": clean(book["output_path"]),
        "pending_output_path": "",
        "cleanup_targets": [],
        "can_reprocess": False,
    }
    if accepted_match and args is not None:
        try:
            cfg = DictConfig(config_data(config_path(args)))
            preview = destination_preview(row_to_dict(book), [row_to_dict(row) for row in files], row_to_dict(accepted_match), cfg)
        except Exception:
            pass
    return {
        "book": row_to_dict(book),
        "files": [row_to_dict(row) for row in files],
        "matches": [row_to_dict(row) for row in matches],
        "search_attempts": [row_to_dict(row) for row in attempts],
        "reprocess": preview,
    }


def get_book_command(args):
    with connect(db_path(args)) as conn:
        init_db(conn)
        payload = get_book(conn, args.id, args)
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
            f"UPDATE book_groups SET {', '.join(f'{field} = ?' for field in fields)}, user_edited = 1, updated_at = ? WHERE id = ?",
            [clean(payload[field]) for field in fields] + [now(), args.id],
        )
        add_group_event(conn, args.id, "updated", "Metadata updated from web UI", {field: payload[field] for field in fields})
        conn.commit()
        payload = get_book(conn, args.id, args)
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


def book_for_target(group, match):
    class TargetBook:
        pass

    class Contributor:
        def __init__(self, name):
            self.name = clean(name)

    class Series:
        def __init__(self, name, part):
            self.name = clean(name)
            self.part = clean(part)

    target = TargetBook()
    target.title = clean(match.get("title") or group.get("title") or group.get("name"))
    target.subtitle = clean(match.get("subtitle") or group.get("subtitle"))
    target.asin = clean(match.get("asin") or group.get("asin"))
    target.language = clean(match.get("language") or group.get("language") or "english")
    target.authors = [Contributor(name) for name in clean(match.get("authors") or group.get("authors")).split(",") if clean(name)]
    target.narrators = [Contributor(name) for name in clean(match.get("narrators") or group.get("narrators")).split(",") if clean(name)]
    series_name = clean(match.get("series") or group.get("series"))
    series_part = clean(group.get("series_part"))
    target.series = [Series(series_name, series_part)] if series_name else []
    return target


def compute_group_destination(file_row, group, match, cfg):
    probe = book_for_target(group, match)

    media_path = clean(file_row["media_path"] or group["media_path"])
    multi_author = cfg.get("Config/target_path/multi_author")
    in_series = cfg.get("Config/target_path/in_series")
    no_series = cfg.get("Config/target_path/no_series")
    disc_folder = cfg.get("Config/target_path/disc_folder")

    if not probe.authors:
        author = "Unknown"
    elif len(probe.authors) > 1 and multi_author is not None:
        match multi_author:
            case "{first_author}":
                author = probe.authors[0].name
            case "{authors}":
                author = ", ".join(item.name for item in probe.authors)
            case _:
                author = multi_author
    else:
        author = probe.authors[0].name

    author = cleanse_author_local(author) if clean(author) else "Unknown"
    narrator = ", ".join(item.name for item in probe.narrators) if len(probe.narrators) == 1 else ""

    disc = os.path.basename(os.path.dirname(clean(file_row["file"])))
    if not is_multi_cd_local(disc):
        disc = ""

    series = cleanse_series_local(probe.series[0].name) if probe.series else ""
    part = clean(probe.series[0].part) if probe.series else ""
    title = cleanse_title_local(probe.title)

    tokens = {
        "author": sanitize_path_token(author),
        "series": sanitize_path_token(series),
        "part": sanitize_path_token(part),
        "title": sanitize_path_token(probe.title),
        "cleanTitle": sanitize_path_token(title),
        "disc": sanitize_path_token(disc),
        "narrator": f"{{{sanitize_path_token(narrator)}}}" if narrator else "",
        "narrators": f"{{{sanitize_path_token(narrator)}}}" if narrator else "",
    }

    path_value = ""
    template = in_series if probe.series else no_series
    for segment in template.format(**tokens).split("/"):
        path_value = os.path.join(path_value, segment.strip())
    if disc:
        path_value = os.path.join(path_value, disc_folder.format(**tokens).strip())
    return clean(os.path.join(media_path, path_value))


def output_cleanup_targets(group, files):
    output_path = clean(group.get("output_path"))
    if not output_path:
        return []
    targets = []
    for file_row in files:
        name = os.path.basename(clean(file_row["file"]))
        if name:
            targets.append(os.path.join(output_path, name))
    targets.append(os.path.join(output_path, "metadata.opf"))
    return targets


def destination_preview(group, files, match, cfg):
    preview = compute_group_destination(files[0], group, match, cfg) if files else ""
    return {
        "current_output_path": clean(group.get("output_path")),
        "pending_output_path": preview,
        "cleanup_targets": output_cleanup_targets(group, files),
        "can_reprocess": bool(clean(group.get("output_path")) and match),
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
            "group_id",
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
            f"INSERT INTO group_matches ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
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
        INSERT INTO group_search_attempts
          (group_id, provider, query_json, result_count, best_match_id, error, duration_ms, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (book["id"], provider, json.dumps(query), len(matches), best_id, error, duration_ms, now()),
    )
    add_group_event(
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
        conn.execute("DELETE FROM group_matches WHERE group_id = ? AND is_accepted = 0", (args.id,))
        book = row_to_dict(conn.execute("SELECT * FROM book_groups WHERE id = ?", (args.id,)).fetchone())
        if not book:
            raise ValueError(f"Book group {args.id} was not found")
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
            UPDATE book_groups
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
        payload = get_book(conn, args.id, args)
    return {"ok": True, "results": results, **payload}


def accept_match(args):
    with connect(db_path(args)) as conn:
        init_db(conn)
        match = conn.execute(
            "SELECT * FROM group_matches WHERE id = ? AND group_id = ?", (args.match_id, args.id)
        ).fetchone()
        if not match:
            raise ValueError("Match was not found")
        conn.execute("UPDATE group_matches SET is_accepted = 0 WHERE group_id = ?", (args.id,))
        conn.execute("UPDATE group_matches SET is_accepted = 1 WHERE id = ?", (args.match_id,))
        conn.execute(
            """
            UPDATE book_groups
            SET asin = ?, title = ?, subtitle = ?, authors = ?, narrators = ?, series = ?,
                language = ?, status = 'matched', is_matched = 1, metadata_source = ?,
                failure_reason = '', user_edited = 1, updated_at = ?
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
        add_group_event(conn, args.id, "match_accepted", f"Accepted {match['provider']} match", {"match_id": args.match_id})
        conn.commit()
        payload = get_book(conn, args.id, args)
    return {"ok": True, **payload}


def mark_ignored(args):
    with connect(db_path(args)) as conn:
        init_db(conn)
        conn.execute(
            "UPDATE book_groups SET status = 'ignored', updated_at = ? WHERE id = ?", (now(), args.id)
        )
        add_group_event(conn, args.id, "ignored", "Marked ignored from web UI")
        conn.commit()
        payload = get_book(conn, args.id, args)
    return {"ok": True, **payload}


def split_files(args):
    file_ids = [int(value) for value in args.file_ids.split(",") if value.strip()]
    if not file_ids:
        raise ValueError("No files selected")
    with connect(db_path(args)) as conn:
        init_db(conn)
        source = conn.execute("SELECT * FROM book_groups WHERE id = ?", (args.id,)).fetchone()
        if not source:
            raise ValueError("Source group was not found")
        files = conn.execute(
            f"SELECT * FROM book_files WHERE group_id = ? AND id IN ({','.join('?' for _ in file_ids)})",
            [args.id] + file_ids,
        ).fetchall()
        if not files:
            raise ValueError("Selected files were not found in this group")
        first_file = files[0]
        group_hash = f"manual:{uuid.uuid4()}"
        payload = {
            "group_hash": group_hash,
            "name": args.name or first(first_file["title"], first_file["book_name"], source["name"]),
            "source_path": source["source_path"],
            "media_path": source["media_path"],
            "source_folder": source["source_folder"],
            "status": first_file["status"],
            "failure_reason": "",
            "asin": first_file["asin"],
            "title": first_file["title"],
            "subtitle": "",
            "authors": first_file["authors"],
            "narrators": first_file["narrators"],
            "series": first_file["series"],
            "series_part": "",
            "language": first(first_file["language"], "english"),
            "metadata_source": first_file["metadata_source"],
            "mam_count": first_file["mam_count"],
            "audible_count": first_file["audible_count"],
            "is_matched": first_file["is_matched"],
            "is_hardlinked": first_file["is_hardlinked"],
            "file_count": len(files),
            "detection_reason": "manual_split",
            "detection_confidence": 1.0,
            "user_edited": 1,
            "last_searched_at": None,
            "updated_at": now(),
            "raw_log_json": first_file["raw_log_json"],
        }
        new_group_id = upsert_group(conn, payload)
        conn.execute(
            f"UPDATE book_files SET group_id = ?, updated_at = ? WHERE id IN ({','.join('?' for _ in file_ids)})",
            [new_group_id, now()] + file_ids,
        )
        recompute_group(conn, args.id)
        recompute_group(conn, new_group_id)
        add_group_event(conn, args.id, "split", "Split selected files into a new group", {"new_group_id": new_group_id, "file_ids": file_ids})
        add_group_event(conn, new_group_id, "created_by_split", "Created from split", {"source_group_id": args.id, "file_ids": file_ids})
        conn.commit()
        payload = get_book(conn, new_group_id, args)
    return {"ok": True, "new_group_id": new_group_id, **payload}


def combine_groups(args):
    source_ids = [int(value) for value in args.source_ids.split(",") if value.strip()]
    source_ids = [value for value in source_ids if value != args.id]
    if not source_ids:
        return get_book_command(args)
    with connect(db_path(args)) as conn:
        init_db(conn)
        target = conn.execute("SELECT * FROM book_groups WHERE id = ?", (args.id,)).fetchone()
        if not target:
            raise ValueError("Target group was not found")
        conn.execute(
            f"UPDATE book_files SET group_id = ?, updated_at = ? WHERE group_id IN ({','.join('?' for _ in source_ids)})",
            [args.id, now()] + source_ids,
        )
        conn.execute(
            f"DELETE FROM book_groups WHERE id IN ({','.join('?' for _ in source_ids)})",
            source_ids,
        )
        recompute_group(conn, args.id)
        add_group_event(conn, args.id, "combined", "Combined groups", {"source_group_ids": source_ids})
        conn.commit()
        payload = get_book(conn, args.id, args)
    return {"ok": True, **payload}


def move_files(args):
    file_ids = [int(value) for value in args.file_ids.split(",") if value.strip()]
    if not file_ids:
        raise ValueError("No files selected")
    with connect(db_path(args)) as conn:
        init_db(conn)
        target = conn.execute("SELECT id FROM book_groups WHERE id = ?", (args.target_id,)).fetchone()
        if not target:
            raise ValueError("Target group was not found")
        conn.execute(
            f"UPDATE book_files SET group_id = ?, updated_at = ? WHERE group_id = ? AND id IN ({','.join('?' for _ in file_ids)})",
            [args.target_id, now(), args.id] + file_ids,
        )
        recompute_group(conn, args.id)
        recompute_group(conn, args.target_id)
        add_group_event(conn, args.id, "files_moved_out", "Moved files to another group", {"target_group_id": args.target_id, "file_ids": file_ids})
        add_group_event(conn, args.target_id, "files_moved_in", "Moved files from another group", {"source_group_id": args.id, "file_ids": file_ids})
        conn.commit()
        payload = get_book(conn, args.target_id, args)
    return {"ok": True, **payload}


def create_job(conn, job_type, book_id=None, status="running", payload=None):
    ts = now()
    conn.execute(
        """
        INSERT INTO jobs (type, book_id, status, logs, error, created_at, updated_at, payload_json)
        VALUES (?, ?, ?, '', '', ?, ?, ?)
        """,
        (job_type, book_id, status, ts, ts, json.dumps(payload or {})),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def complete_job(conn, job_id, status, logs="", error="", exit_code=None):
    conn.execute(
        "UPDATE jobs SET status = ?, logs = ?, error = ?, exit_code = ?, updated_at = ? WHERE id = ?",
        (status, logs, error, exit_code, now(), job_id),
    )


def job_to_dict(row):
    payload = row_to_dict(row)
    if not payload:
        return None
    try:
        payload["payload"] = json.loads(payload.get("payload_json") or "{}")
    except Exception:
        payload["payload"] = {}
    return payload


def active_full_run(conn):
    return conn.execute(
        """
        SELECT * FROM jobs
        WHERE type = 'full_run' AND status IN ('queued', 'running')
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()


def get_job(args):
    with connect(db_path(args)) as conn:
        init_db(conn)
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (args.id,)).fetchone()
    if not row:
        raise ValueError(f"Job {args.id} was not found")
    return {"ok": True, "job": job_to_dict(row)}


def latest_job(args):
    with connect(db_path(args)) as conn:
        init_db(conn)
        row = conn.execute(
            "SELECT * FROM jobs WHERE type = ? ORDER BY id DESC LIMIT 1",
            (args.type,),
        ).fetchone()
    return {"ok": True, "job": job_to_dict(row)}


def worker_command(args, config_file=None):
    command = [
        sys.executable,
        os.path.abspath(__file__),
        "--db",
        db_path(args),
    ]
    if getattr(args, "config_dir", None):
        command.extend(["--config-dir", config_root(args)])
    if config_file:
        command.extend(["--config", config_file])
    return command


def start_run(args):
    selected_config = full_run_config_path(args)
    with connect(db_path(args)) as conn:
        init_db(conn)
        running = active_full_run(conn)
        if running:
            return {
                "ok": False,
                "error": f"Booktree run {running['id']} is already {running['status']}",
                "job": job_to_dict(running),
            }
        job_id = create_job(
            conn,
            "full_run",
            status="queued",
            payload={"config_path": selected_config},
        )
        conn.commit()

    command = worker_command(args, selected_config) + ["run-full-job", "--job-id", str(job_id)]
    try:
        subprocess.Popen(
            command,
            cwd=Path(__file__).resolve().parent,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        with connect(db_path(args)) as conn:
            init_db(conn)
            complete_job(conn, job_id, "failed", "", str(exc), exit_code=1)
            conn.commit()
        raise

    with connect(db_path(args)) as conn:
        init_db(conn)
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return {"ok": True, "message": "Booktree run started", "job": job_to_dict(row)}


def run_full_job(args):
    with connect(db_path(args)) as conn:
        init_db(conn)
        row = conn.execute("SELECT * FROM jobs WHERE id = ? AND type = 'full_run'", (args.job_id,)).fetchone()
        if not row:
            raise ValueError(f"Full run job {args.job_id} was not found")
        payload = job_to_dict(row)["payload"]
        selected_config = safe_config_path(payload.get("config_path") or config_path(args), args, must_exist=True)
        conn.execute("UPDATE jobs SET status = 'running', updated_at = ? WHERE id = ?", (now(), args.job_id))
        conn.commit()

    command = [sys.executable, os.path.join(Path(__file__).resolve().parent, "booktree.py"), selected_config]
    completed = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parent,
        capture_output=True,
        text=True,
    )
    logs = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
    status = "complete" if completed.returncode == 0 else "failed"
    error = "" if completed.returncode == 0 else (completed.stderr.strip() or f"Booktree exited with code {completed.returncode}")

    try:
        import_args = argparse.Namespace(
            db=db_path(args),
            config=selected_config,
            config_dir=config_root(args),
            log_file=None,
            log_dir=None,
        )
        sync_result = import_logs(import_args, missing_ok=True)
        logs = f"{logs}\n\nSynced logs: scanned={sync_result['scanned']} imported={sync_result['imported']}".strip()
    except Exception as exc:
        error = error or str(exc)
        logs = f"{logs}\n\nWarning: failed to sync logs after run: {exc}".strip()
        if status == "complete":
            status = "failed"

    with connect(db_path(args)) as conn:
        init_db(conn)
        complete_job(conn, args.job_id, status, logs, error, exit_code=completed.returncode)
        conn.commit()
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (args.job_id,)).fetchone()
    return {"ok": status == "complete", "job": job_to_dict(row)}


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


def log_row_for_group_file(group, file_row, match, target_path=None):
    raw = json.loads(file_row["raw_log_json"] or "{}")
    raw.update(
        {
            "book": group["name"],
            "file": file_row["file"],
            "paths": target_path if target_path is not None else (file_row["paths"] or group["paths"]),
            "isMatched": "True",
            "isHardLinked": "False",
            "metadatasource": match["provider"],
            "id3-asin": group["asin"] or "",
            "id3-title": group["title"] or "",
            "id3-subtitle": group["subtitle"] or "",
            "id3-authors": group["authors"] or "",
            "id3-narrators": group["narrators"] or "",
            "id3-seriesparts": group["series_part"] or group["series"] or "",
            "id3-language": group["language"] or "english",
            "sourcePath": file_row["source_path"] or group["source_path"],
            "mediaPath": file_row["media_path"] or group["media_path"],
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


def get_group_for_processing(conn, group_id):
    book = row_to_dict(conn.execute("SELECT * FROM book_groups WHERE id = ?", (group_id,)).fetchone())
    if not book:
        raise ValueError(f"Book group {group_id} was not found")
    files = [
        row_to_dict(row)
        for row in conn.execute("SELECT * FROM book_files WHERE group_id = ? ORDER BY file", (group_id,)).fetchall()
    ]
    if not files:
        raise ValueError("Book group has no files to process")
    match = row_to_dict(
        conn.execute(
            "SELECT * FROM group_matches WHERE group_id = ? AND is_accepted = 1 ORDER BY id DESC LIMIT 1",
            (group_id,),
        ).fetchone()
    )
    if not match:
        raise ValueError("Accept a match before processing this book")
    return book, files, match


def remove_old_output(group, files):
    removed = []
    for target in output_cleanup_targets(group, files):
        if os.path.exists(target):
            os.remove(target)
            removed.append(target)
    return removed


def process_group(args, cleanup_old=False):
    with connect(db_path(args)) as conn:
        init_db(conn)
        book, files, match = get_group_for_processing(conn, args.id)
        cfg = DictConfig(config_data(config_path(args)))
        preview = destination_preview(book, files, match, cfg)
        pending_path = clean(preview["pending_output_path"])
        job_id = create_job(conn, "reprocess_book" if cleanup_old else "process_book", args.id)
        conn.commit()

    logs = io.StringIO()
    error = ""
    status = "complete"
    removed_targets = []
    try:
        import booktree

        cfg_data = config_data(config_path(args))
        cfg_data.setdefault("Config", {})["metadata"] = "log"
        if cleanup_old:
            removed_targets = remove_old_output(book, files)
        process_rows = [log_row_for_group_file(book, file_row, match, target_path=pending_path) for file_row in files]
        headers = list(process_rows[0].keys())
        try:
            import myx_utilities

            headers = list(myx_utilities.getLogHeaders().keys())
        except Exception:
            pass
        with tempfile.TemporaryDirectory(prefix="booktree-webui-") as tmp:
            input_csv = os.path.join(tmp, "input.csv")
            output_csv = os.path.join(tmp, "output.csv")
            with open(input_csv, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=headers)
                writer.writeheader()
                for row in process_rows:
                    writer.writerow({field: row.get(field, "") for field in headers})
            with contextlib.redirect_stdout(logs):
                booktree.buildTreeFromLog(input_csv, output_csv, DictConfig(cfg_data))
    except Exception as exc:
        error = str(exc)
        status = "failed"

    with connect(db_path(args)) as conn:
        init_db(conn)
        log_text = logs.getvalue()
        if removed_targets:
            log_text = f"Removed old output:\n" + "\n".join(removed_targets) + (f"\n\n{log_text}" if log_text else "")
        complete_job(conn, job_id, status, log_text, error)
        conn.execute(
            "UPDATE book_groups SET status = ?, is_hardlinked = ?, failure_reason = ?, output_path = ?, updated_at = ? WHERE id = ?",
            ("processed" if status == "complete" else "failed", 1 if status == "complete" else 0, error, pending_path if status == "complete" else clean(book.get("output_path")), now(), args.id),
        )
        conn.execute(
            "UPDATE book_files SET status = ?, is_hardlinked = ?, output_path = ?, updated_at = ? WHERE group_id = ?",
            ("processed" if status == "complete" else "failed", 1 if status == "complete" else 0, pending_path if status == "complete" else "", now(), args.id),
        )
        add_group_event(
            conn,
            args.id,
            "reprocessed" if cleanup_old and status == "complete" else ("processed" if status == "complete" else "process_failed"),
            error or ("Reprocessed from web UI" if cleanup_old else "Processed from web UI"),
            {"old_output_path": preview["current_output_path"], "new_output_path": pending_path, "removed_targets": removed_targets},
        )
        conn.commit()
        payload = get_book(conn, args.id, args)
    return {
        "ok": True,
        "process_status": status,
        "job_id": job_id,
        "logs": log_text,
        "error": error,
        **payload,
    }


def process_book(args):
    return process_group(args, cleanup_old=False)


def reprocess_book(args):
    return process_group(args, cleanup_old=True)


def output(payload):
    print(json.dumps(payload, default=str))


def main():
    parser = argparse.ArgumentParser(description="Booktree web UI worker")
    parser.add_argument("--db")
    parser.add_argument("--config")
    parser.add_argument("--config-dir")
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
    split_parser = sub.add_parser("split-files")
    split_parser.add_argument("--id", type=int, required=True)
    split_parser.add_argument("--file-ids", required=True)
    split_parser.add_argument("--name", default="")
    combine_parser = sub.add_parser("combine-groups")
    combine_parser.add_argument("--id", type=int, required=True)
    combine_parser.add_argument("--source-ids", required=True)
    move_parser = sub.add_parser("move-files")
    move_parser.add_argument("--id", type=int, required=True)
    move_parser.add_argument("--target-id", type=int, required=True)
    move_parser.add_argument("--file-ids", required=True)
    process_parser = sub.add_parser("process")
    process_parser.add_argument("--id", type=int, required=True)
    reprocess_parser = sub.add_parser("reprocess")
    reprocess_parser.add_argument("--id", type=int, required=True)
    get_config_parser = sub.add_parser("get-config")
    get_config_parser.add_argument("--path")
    save_config_parser = sub.add_parser("save-config")
    save_config_parser.add_argument("--path", required=True)
    save_config_parser.add_argument("--payload", required=True)
    save_as_parser = sub.add_parser("save-config-as")
    save_as_parser.add_argument("--name", required=True)
    save_as_parser.add_argument("--payload", required=True)
    save_as_parser.add_argument("--overwrite", action="store_true")
    active_config_parser = sub.add_parser("set-active-config")
    active_config_parser.add_argument("--path", required=True)
    sub.add_parser("list-configs")
    sub.add_parser("start-run")
    run_job_parser = sub.add_parser("run-full-job")
    run_job_parser.add_argument("--job-id", type=int, required=True)
    get_job_parser = sub.add_parser("get-job")
    get_job_parser.add_argument("--id", type=int, required=True)
    latest_job_parser = sub.add_parser("latest-job")
    latest_job_parser.add_argument("--type", default="full_run")

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
        elif args.command == "split-files":
            output(split_files(args))
        elif args.command == "combine-groups":
            output(combine_groups(args))
        elif args.command == "move-files":
            output(move_files(args))
        elif args.command == "process":
            output(process_book(args))
        elif args.command == "reprocess":
            output(reprocess_book(args))
        elif args.command == "list-configs":
            output(list_configs(args))
        elif args.command == "get-config":
            output(get_config(args))
        elif args.command == "save-config":
            output(save_config(args))
        elif args.command == "save-config-as":
            output(save_config_as(args))
        elif args.command == "set-active-config":
            output(set_active_config(args))
        elif args.command == "start-run":
            output(start_run(args))
        elif args.command == "run-full-job":
            output(run_full_job(args))
        elif args.command == "get-job":
            output(get_job(args))
        elif args.command == "latest-job":
            output(latest_job(args))
    except Exception as exc:
        output({"ok": False, "error": str(exc)})
        raise SystemExit(1)


if __name__ == "__main__":
    main()
