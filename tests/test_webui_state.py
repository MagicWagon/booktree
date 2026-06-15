import csv
import io
import json
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

    def group_rows(self, db):
        with booktree_worker.connect(db) as conn:
            booktree_worker.init_db(conn)
            return [dict(row) for row in conn.execute("SELECT * FROM book_groups ORDER BY id").fetchall()]

    def file_rows(self, db):
        with booktree_worker.connect(db) as conn:
            booktree_worker.init_db(conn)
            return [dict(row) for row in conn.execute("SELECT * FROM book_files ORDER BY id").fetchall()]

    def write_log(self, path, rows):
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(base_row().keys()))
            writer.writeheader()
            writer.writerows(rows)

    def config_args(self, tmp):
        return Namespace(db=self.db_file(tmp), config="", config_dir=tmp)

    def write_config(self, tmp, name="config.json", **config_overrides):
        path = os.path.join(tmp, name)
        payload = {
            "Config": {
                "metadata": "mam-audible",
                "log_path": "/logs",
                "paths": [],
                **config_overrides,
            },
            "unknown_top": {"keep": True},
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return path, payload

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
                base_row(book="Needs Metadata", file="/data/source/needs.m4b")
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

    def test_mp3_tracks_in_same_folder_group_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self.db_file(tmp)
            booktree_worker.sync_books(
                [
                    FakeBook(base_row(file="/data/source/Book/01 Track.mp3", book="Book", **{"id3-title": "Chapter 1"})),
                    FakeBook(base_row(file="/data/source/Book/02 Track.mp3", book="Book", **{"id3-title": "Chapter 2"})),
                ],
                FakeConfig(),
                db_file=db,
            )

            groups = self.group_rows(db)
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0]["file_count"], 2)
            self.assertEqual(groups[0]["detection_reason"], "track_like_files")

    def test_distinct_asins_in_same_folder_need_split_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self.db_file(tmp)
            booktree_worker.sync_books(
                [
                    FakeBook(base_row(file="/data/source/Mixed/book-a.mp3", book="Mixed", **{"id3-title": "Book A", "id3-asin": "A"})),
                    FakeBook(base_row(file="/data/source/Mixed/book-b.mp3", book="Mixed", **{"id3-title": "Book B", "id3-asin": "B"})),
                ],
                FakeConfig(),
                db_file=db,
            )

            group = self.group_rows(db)[0]
            self.assertEqual(group["status"], "needs_split_review")
            self.assertEqual(group["detection_reason"], "multiple_asins")

    def test_manual_split_moves_files_to_new_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self.db_file(tmp)
            booktree_worker.sync_books(
                [
                    FakeBook(base_row(file="/data/source/Mixed/book-a.mp3", book="Mixed", **{"id3-title": "Book A"})),
                    FakeBook(base_row(file="/data/source/Mixed/book-b.mp3", book="Mixed", **{"id3-title": "Book B"})),
                ],
                FakeConfig(),
                db_file=db,
            )
            group_id = self.group_rows(db)[0]["id"]
            file_id = self.file_rows(db)[0]["id"]
            args = Namespace(db=db, config="", id=group_id, file_ids=str(file_id), name="Book A")

            result = booktree_worker.split_files(args)

            self.assertTrue(result["ok"])
            groups = self.group_rows(db)
            self.assertEqual(len(groups), 2)
            self.assertEqual(sorted(group["file_count"] for group in groups), [1, 1])

    def test_manual_combine_moves_source_group_files_to_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self.db_file(tmp)
            booktree_worker.sync_books(
                [
                    FakeBook(base_row(book="One", file="/data/source/one.mp3")),
                    FakeBook(base_row(book="Two", file="/data/source/two.mp3")),
                ],
                FakeConfig(),
                db_file=db,
            )
            groups = self.group_rows(db)
            args = Namespace(db=db, config="", id=groups[0]["id"], source_ids=str(groups[1]["id"]))

            result = booktree_worker.combine_groups(args)

            self.assertTrue(result["ok"])
            groups = self.group_rows(db)
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0]["file_count"], 2)

    def test_group_metadata_edit_is_used_for_process_rows(self):
        group = {"name": "Edited", "asin": "ASIN", "title": "Edited Title", "subtitle": "", "authors": "Author", "narrators": "", "series": "", "series_part": "", "language": "english", "source_path": "/data/source", "media_path": "/data/media", "paths": ""}
        file_row = {"raw_log_json": "{}", "file": "/data/source/Book/01.mp3", "paths": "", "source_path": "/data/source", "media_path": "/data/media"}
        match = {"provider": "adb", "asin": "ASIN", "title": "Edited Title", "subtitle": "", "authors": "Author", "narrators": "", "series": "", "language": "english"}

        row = booktree_worker.log_row_for_group_file(group, file_row, match)

        self.assertEqual(row["id3-asin"], "ASIN")
        self.assertEqual(row["id3-title"], "Edited Title")
        self.assertEqual(row["id3-authors"], "Author")

    def test_safe_config_path_accepts_config_root_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _payload = self.write_config(tmp, "example.json")
            args = self.config_args(tmp)

            self.assertEqual(booktree_worker.safe_config_path("example.json", args, must_exist=True), os.path.realpath(path))

    def test_safe_config_path_rejects_traversal_and_non_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.config_args(tmp)

            with self.assertRaises(ValueError):
                booktree_worker.safe_config_path("../secret.json", args)
            with self.assertRaises(ValueError):
                booktree_worker.safe_config_path("config.cfg", args)

    def test_list_configs_returns_json_files_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write_config(tmp, "config.json")
            self.write_config(tmp, "testing.json")
            with open(os.path.join(tmp, "notes.txt"), "w", encoding="utf-8") as handle:
                handle.write("ignore")

            result = booktree_worker.list_configs(self.config_args(tmp))

            self.assertEqual([item["name"] for item in result["configs"]], ["config.json", "testing.json"])

    def test_save_config_preserves_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, payload = self.write_config(tmp)
            payload["Config"]["metadata"] = "mam"
            args = Namespace(db=self.db_file(tmp), config="", config_dir=tmp, path=path, payload=json.dumps(payload))

            result = booktree_worker.save_config(args)

            self.assertTrue(result["ok"])
            with open(path, encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertEqual(saved["Config"]["metadata"], "mam")
            self.assertEqual(saved["unknown_top"], {"keep": True})

    def test_save_config_as_creates_file_and_sets_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            _path, payload = self.write_config(tmp)
            args = Namespace(db=self.db_file(tmp), config="", config_dir=tmp, name="testing.json", payload=json.dumps(payload), overwrite=False)

            result = booktree_worker.save_config_as(args)

            self.assertTrue(result["ok"])
            self.assertTrue(os.path.exists(os.path.join(tmp, "testing.json")))
            self.assertEqual(booktree_worker.active_config_path(self.config_args(tmp)), os.path.realpath(os.path.join(tmp, "testing.json")))

    def test_active_config_path_is_used_without_explicit_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            default_path, _default = self.write_config(tmp, "config.json", metadata="mam")
            active_path, _active = self.write_config(tmp, "active.json", metadata="audible")
            args = self.config_args(tmp)
            booktree_worker.set_active_config_path(args, active_path)

            self.assertEqual(booktree_worker.config_path(args), os.path.realpath(active_path))
            self.assertNotEqual(booktree_worker.config_path(args), os.path.realpath(default_path))


if __name__ == "__main__":
    unittest.main()
