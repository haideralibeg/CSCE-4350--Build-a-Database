"""Behavior and restart tests, always using a temporary database."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from main import Database

SCRIPT = Path(__file__).resolve().parents[1] / "main.py"


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "data.db"
        self.now = 1000.0
        self.db = Database(self.path, clock=lambda: self.now)

    def restart(self) -> None:
        self.db = Database(self.path, clock=lambda: self.now)

    def test_set_overwrite_and_restart(self) -> None:
        self.db.execute("SET car M")
        self.db.execute("SET car Mustang")
        self.assertTrue(self.path.is_file())
        self.restart()
        self.assertEqual(self.db.execute("GET car"), ["Mustang"])

    def test_missing_keys(self) -> None:
        self.assertEqual(self.db.execute("GET missing"), [""])
        self.assertEqual(self.db.execute("EXISTS missing"), ["0"])
        self.assertEqual(self.db.execute("DEL missing"), ["0"])
        self.assertEqual(self.db.execute("EXPIRE missing 1"), ["0"])

    def test_delete_is_persistent(self) -> None:
        self.db.execute("SET car Mustang")
        self.assertEqual(self.db.execute("DEL car"), ["1"])
        self.assertEqual(self.db.execute("DEL car"), ["0"])
        self.restart()
        self.assertEqual(self.db.execute("EXISTS car"), ["0"])

    def test_bulk_commands(self) -> None:
        self.db.execute("MSET a first b second a updated")
        self.restart()
        self.assertEqual(self.db.execute("MGET a missing b"), ["updated", "nil", "second"])
        with self.assertRaises(ValueError):
            self.db.execute("MSET a broken b")
        self.assertEqual(self.db.execute("GET a"), ["updated"])

    def test_expiration_survives_restart(self) -> None:
        self.db.execute("SET car Mustang")
        self.assertEqual(self.db.execute("TTL car"), ["-1"])
        self.assertEqual(self.db.execute("EXPIRE car 2"), ["1"])
        self.assertEqual(self.db.execute("TTL car"), ["2"])
        self.now += 1
        self.restart()
        self.assertEqual(self.db.execute("TTL car"), ["1"])
        self.now += 1
        self.assertEqual(self.db.execute("TTL car"), ["-2"])
        self.assertEqual(self.db.execute("GET car"), [""])
        self.restart()
        self.assertEqual(self.db.execute("EXISTS car"), ["0"])

    def test_overwrite_clears_expiration(self) -> None:
        self.db.execute("SET car M")
        self.db.execute("EXPIRE car 1")
        self.db.execute("SET car Mustang")
        self.now += 2
        self.assertEqual(self.db.execute("GET car"), ["Mustang"])
        self.assertEqual(self.db.execute("TTL car"), ["-1"])
        self.assertEqual(self.db.execute("EXPIRE car 0"), ["1"])
        self.assertEqual(self.db.execute("EXISTS car"), ["0"])

    def test_ranges_are_sorted_inclusive_and_unbounded(self) -> None:
        self.db.execute("MSET d 4 b 2 a 1 c 3")
        self.assertEqual(self.db.execute("RANGE b d"), ["b", "c", "d", "END"])
        self.assertEqual(self.db.execute('RANGE "" c'), ["a", "b", "c", "END"])
        self.assertEqual(self.db.execute('RANGE d ""'), ["d", "END"])
        self.db.execute("EXPIRE c 1")
        self.now += 1
        self.assertEqual(self.db.execute("RANGE b d"), ["b", "d", "END"])
        self.assertEqual(self.db.execute("RANGE z a"), ["END"])

    def test_abort_restores_values_and_deadlines(self) -> None:
        self.db.execute("SET car old")
        self.db.execute("EXPIRE car 10")
        self.db.execute("BEGIN")
        self.db.execute("SET car new")
        self.db.execute("SET temporary yes")
        self.assertEqual(self.db.execute("GET car"), ["new"])
        self.db.execute("ABORT")
        self.assertEqual(self.db.execute("GET car"), ["old"])
        self.assertEqual(self.db.execute("TTL car"), ["10"])
        self.restart()
        self.assertEqual(self.db.execute("GET temporary"), [""])

    def test_commit_is_persistent_and_uncommitted_writes_are_not(self) -> None:
        self.db.execute("BEGIN")
        self.db.execute("SET car new")
        self.assertEqual(Database(self.path).execute("GET car"), [""])
        self.db.execute("COMMIT")
        self.restart()
        self.assertEqual(self.db.execute("GET car"), ["new"])

    def test_transaction_errors(self) -> None:
        for command in ("ABORT", "COMMIT"):
            with self.assertRaises(ValueError):
                self.db.execute(command)
        self.db.execute("BEGIN")
        with self.assertRaises(ValueError):
            self.db.execute("BEGIN")
        self.db.execute("ABORT")

    def test_hash_commands_and_persistence(self) -> None:
        self.assertEqual(self.db.execute("HSET car model Mustang color red"), ["2"])
        self.assertEqual(self.db.execute("HSET car color blue"), ["0"])
        self.restart()
        self.assertEqual(self.db.execute("HGET car model"), ["Mustang"])
        self.assertEqual(self.db.execute("HGET car missing"), ["nil"])
        self.assertEqual(self.db.execute("HGETALL car"), ["color", "blue", "model", "Mustang"])

    def test_list_order_ranges_and_persistence(self) -> None:
        self.assertEqual(self.db.execute("LPUSH queue item1"), ["1"])
        self.assertEqual(self.db.execute("LPUSH queue item2"), ["2"])
        self.assertEqual(self.db.execute("RPUSH queue item3"), ["3"])
        self.restart()
        self.assertEqual(self.db.execute("LRANGE queue 0 -1"), ["item2", "item1", "item3"])
        self.assertEqual(self.db.execute("LRANGE queue -2 -1"), ["item1", "item3"])
        self.assertEqual(self.db.execute("LRANGE queue 0 -10"), [])
        self.assertEqual(self.db.execute("LPOP queue"), ["item2"])
        self.assertEqual(self.db.execute("RPOP queue"), ["item3"])
        self.assertEqual(self.db.execute("LPOP queue"), ["item1"])
        self.assertEqual(self.db.execute("LPOP queue"), ["nil"])
        self.assertEqual(self.db.execute("EXISTS queue"), ["0"])

    def test_abort_restores_mutable_collections(self) -> None:
        self.db.execute("HSET car color red")
        self.db.execute("RPUSH queue first")
        self.db.execute("BEGIN")
        self.db.execute("HSET car color blue")
        self.db.execute("LPOP queue")
        self.db.execute("ABORT")
        self.assertEqual(self.db.execute("HGET car color"), ["red"])
        self.assertEqual(self.db.execute("LRANGE queue 0 -1"), ["first"])

    def test_counters_and_invalid_values(self) -> None:
        self.assertEqual(self.db.execute("INCR count"), ["1"])
        self.assertEqual(self.db.execute("INCR count"), ["2"])
        self.assertEqual(self.db.execute("DECR count"), ["1"])
        self.db.execute("SET text hello")
        with self.assertRaises(ValueError):
            self.db.execute("INCR text")
        self.assertEqual(self.db.execute("GET text"), ["hello"])
        self.restart()
        self.assertEqual(self.db.execute("GET count"), ["1"])

    def test_wrong_types_do_not_destroy_values(self) -> None:
        self.db.execute("SET car Mustang")
        for command in ("HSET car color red", "LPUSH car item", "LPOP car"):
            with self.assertRaises(ValueError):
                self.db.execute(command)
        self.assertEqual(self.db.execute("GET car"), ["Mustang"])

    def test_flush_and_rollback(self) -> None:
        self.db.execute("SET car Mustang")
        self.db.execute("BEGIN")
        self.db.execute("FLUSHDB")
        self.assertEqual(self.db.execute("GET car"), [""])
        self.db.execute("ABORT")
        self.assertEqual(self.db.execute("GET car"), ["Mustang"])
        self.db.execute("FLUSHDB")
        self.restart()
        self.assertEqual(self.db.execute('RANGE "" ""'), ["END"])

    def test_legacy_file_import_and_empty_file(self) -> None:
        self.path.write_text("", encoding="utf-8")
        self.restart()
        self.path.write_text("car M\nSET car Mustang\nSET gone x\nDEL gone\n\n", encoding="utf-8")
        self.restart()
        self.assertEqual(self.db.execute("GET car"), ["Mustang"])
        self.assertEqual(self.db.execute("EXISTS gone"), ["0"])
        self.db.execute("SET new value")
        self.restart()
        self.assertEqual(self.db.execute("MGET car new"), ["Mustang", "value"])

    def test_failed_replace_preserves_saved_database(self) -> None:
        self.db.execute("SET car old")
        with patch("main.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.db.execute("SET car new")
        self.restart()
        self.assertEqual(self.db.execute("GET car"), ["old"])
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_command_line_recovers_from_invalid_input(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT)], cwd=self.directory.name,
            input='SET\nBOGUS\nSET "broken\nset car "Ford Mustang"\nGET car\nEXIT\n',
            text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertTrue(all(line.startswith("ERR ") for line in lines[:3]))
        self.assertEqual(lines[3:], ["Ford Mustang"])

    def test_exit_does_not_commit_transaction(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT)], cwd=self.directory.name,
            input="SET car old\nBEGIN\nSET car new\nEXIT\n",
            text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.restart()
        self.assertEqual(self.db.execute("GET car"), ["old"])


if __name__ == "__main__":
    unittest.main()
