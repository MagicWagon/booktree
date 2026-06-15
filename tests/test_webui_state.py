import csv
import io
import os
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from unittest import mock

import booktree_worker


class FakeConfig:
    def get(self, path=None, default=None):
        values = {
            "Config/metadata": "mam-audible",
            "Config/log_path": "",
        }
        return values.get(path, default)


class FakeFile:
    pass


class FakeBook:
    def __init__(self, row):
        self.files = [FakeFile()]
        self.row = row

    def getLogRecord(self, _book_file, _cfg):
        return dict(self.row)


def base_row(**overrides):
    row = {
        "book": "Sample Book",
        "file": "/data/source/Sample Book/book.m4b",
        "paths": "/data/media/Sample Author/Sample Book",
        "isMatched": "False",
        "isHardLinked": "False",
        "mamCount": "0",
        "audibleMatchCount": "0",
        "metadatasource": "id3",
        "id3-asin": "",
        "id3-title": "Sample Book",
        "id3-subtitle": "",
        "id3-publisher": "",
        "id3-length": "",
        "id3-duration": "",
        "id3-series": "",
        "id3-authors": "Sample Author",
        "id3-narrators": "",
        "id3-seriesparts": "",
        "id3-language": "english",
        "mam-matchRate": "",
        "mam-asin": "",
        "mam-title": "",
        "mam-subtitle": "",
        "mam-publisher": "",
        "mam-length": "",
        "mam-duration": "",
        "mam-series": "",
        "mam-authors": "",
        "mam-narrators": "",
        "mam-seriesparts": "",
        "mam-language": "",
        "adb-matchRate": "",
        "adb-asin": "",
        "adb-title": "",
        "adb-subtitle": "",
        "adb-publisher": "",
        "adb-length": "",
        "adb-duration": "",
        "adb-series": "",
        "adb-authors": "",
        "adb-narrators": "",
        "adb-seriesparts": "",
        "adb-language": "",
        "sourcePath": "/data/source",
        "mediaPath": "/data/media",
    }
    row.update(overrides)
    return row


class WebUiStateTests(unittest.TestCase):
    def db_file(self, tmp):
        return os.path.join(tmp, "booktree.db")

    def rows(self, db):
        with booktree_worker.connect(db) as conn:
            booktree_worker.init_db(conn)
            return [dict(row) for row in conn.execute("SELECT * FROM books ORDER BY id").fetchall()]

    def write_log(self, path, rows):
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(base_row().keys()))
            writer.writeheader()
            writer.writerows(rows)

    def test_sync_processed_book_from_cli_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self.db_file(tmp)
            book = FakeBook(base_row(isMatched="True", isHardLinked="True", metadatasource="audible"))

            synced = booktree_worker.sync_books([book], FakeConfig(), db_file=db)

            self.assertEqual(synced, 1)
            rows = self.rows(db)
            self.assertEqual(rows[0]["status"], "processed")
            self.assertEqual(rows[0]["is_matched"], 1)
            self.assertEqual(rows[0]["is_hardlinked"], 1)

    def test_sync_unmatched_book_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self.db_file(tmp)
            no_match = FakeBook(base_row(book="No Match", file="/data/source/no-match.m4b"))
            needs_metadata = FakeBook(
                base_row(book="Needs Metadata", file="/data/source/needs.m4b", id3_title="", id3_authors="")
            )
            needs_metadata.row["id3-title"] = ""
            needs_metadata.row["id3-authors"] = ""

            synced = booktree_worker.sync_books([no_match, needs_metadata], FakeConfig(), db_file=db)

            self.assertEqual(synced, 2)
            statuses = {row["name"]: row["status"] for row in self.rows(db)}
            self.assertEqual(statuses["No Match"], "no_match")
            self.assertEqual(statuses["Needs Metadata"], "needs_metadata")

    def test_sync_preserves_ignored_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self.db_file(tmp)
            book = FakeBook(base_row(isMatched="False"))
            booktree_worker.sync_books([book], FakeConfig(), db_file=db)
            with booktree_worker.connect(db) as conn:
                conn.execute("UPDATE books SET status = 'ignored'")
                conn.commit()

            booktree_worker.sync_books([FakeBook(base_row(isMatched="True", isHardLinked="True"))], FakeConfig(), db_file=db)

            rows = self.rows(db)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "ignored")

    def test_imports_multiple_logs_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self.db_file(tmp)
            log1 = os.path.join(tmp, "booktree_log_1.csv")
            log2 = os.path.join(tmp, "booktree_log_2.csv")
            self.write_log(log1, [base_row(book="One", file="/data/source/one.m4b")])
            self.write_log(log2, [base_row(book="Two", file="/data/source/two.m4b")])
            args = Namespace(db=db, config="", log_file=None, log_dir=tmp)

            first = booktree_worker.import_logs(args)
            second = booktree_worker.import_logs(args)

            self.assertEqual(first["imported"], 2)
            self.assertEqual(second["imported"], 0)
            self.assertEqual(len(self.rows(db)), 2)

    def test_safe_sync_failure_warns_without_raising(self):
        with mock.patch("booktree_worker.sync_books", side_effect=RuntimeError("db locked")):
            output = io.StringIO()
            with redirect_stdout(output):
                booktree_worker.sync_books_safely([FakeBook(base_row())], FakeConfig(), db_file="/bad/db")

        self.assertIn("Warning: failed to update Booktree web UI state", output.getvalue())


if __name__ == "__main__":
    unittest.main()
