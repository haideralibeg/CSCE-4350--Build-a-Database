###
# Name: Haider Beg - 11589880
# Course: CSCE 4350 - Build a Database
###


import copy
import json
import math
import os
import shlex
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

Value = str | dict[str, str] | list[str] # a value can be a string, a hash (dict), or a list
State = tuple[dict[str, Value], dict[str, float]] # the state is a tuple of the data and expiration dictionaries


class Database:
    # This class implements a simple key/value database with a line-based command interface.

    # The database supports string, hash, and list values, as well as expiration times for keys.
    def __init__(
        self, path: str | Path = "data.db", clock: Callable[[], float] = time.time
    ) -> None:
        self.path = Path(path)
        self.clock = clock
        self.data: dict[str, Value] = {}
        self.expires: dict[str, float] = {}
        self.transaction: State | None = None
        self._load()

    # this function loads the database from a file, either as a JSON snapshot or as a log of SET/DEL commands
    def _load(self) -> None:
        """Load a snapshot, or import the earlier key/value and SET/DEL logs."""
        if not self.path.exists():
            return
        content = self.path.read_text(encoding="utf-8")
        if not content.strip():
            return
        if content.lstrip().startswith("{"):
            state = json.loads(content)
            self.data = state["data"]
            self.expires = state["expires"]
            return
        for number, line in enumerate(content.splitlines(), 1):
            parts = shlex.split(line)
            if not parts:
                continue
            if len(parts) == 3 and parts[0] == "SET":
                self.data[parts[1]] = parts[2]
            elif len(parts) == 2 and parts[0] in ("DEL", "DELETE"):
                self.data.pop(parts[1], None)
            elif len(parts) == 2:
                self.data[parts[0]] = parts[1]
            else:
                raise ValueError(f"invalid database record on line {number}")

    # this function saves the database to a file atomically, by writing to a temporary file and then replacing the original file
    def _save(self) -> None:
        """Replace the saved state atomically after flushing it to disk."""
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as file:
                temporary = Path(file.name)
                json.dump({"data": self.data, "expires": self.expires}, file)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    # this function is called whenever the database changes, and it saves the state unless there is an active transaction
    def _changed(self) -> None:
        """Persist changes unless they belong to an uncommitted transaction."""
        if self.transaction is None:
            self._save()

    # this function removes expired keys from the database, checks the expiration time of a key
    def _expire(self, key: str) -> None:
        """Remove expired values lazily; saved deadlines survive restarts."""
        if key in self.expires and self.expires[key] <= self.clock():
            self.data.pop(key, None)
            self.expires.pop(key, None)

    # this function retrieves a value from the database, checking its type and expiration
    def _get(self, key: str, kind: type) -> Value | None:
        """Return a live value, rejecting operations on a different type."""
        self._expire(key)
        value = self.data.get(key)
        if value is not None and not isinstance(value, kind):
            raise ValueError("wrong value type for this command")
        return value

    # this fuction checks a condition and raises a ValueError if false, used for argument checks
    @staticmethod
    def _require(condition: bool) -> None:
        if not condition:
            raise ValueError("wrong number of arguments")

    # this function executes a command line and returns the output lines as a list of strings 
    def execute(self, line: str) -> list[str]:
        """Execute one command and return its output lines, without printing."""
        parts = shlex.split(line)
        if not parts:
            return []
        command, *args = parts
        command = command.upper()

        # Handle transaction commands first, since they don't change the database immediately.
        if command in ("BEGIN", "COMMIT", "ABORT"):
            self._require(not args)
            if command == "BEGIN":
                if self.transaction is not None:
                    raise ValueError("transaction already active")
                # Copy nested hashes and lists too, so ABORT can restore them.
                self.transaction = copy.deepcopy((self.data, self.expires))
            else:
                if self.transaction is None:
                    raise ValueError("no active transaction")
                if command == "ABORT":
                    self.data, self.expires = self.transaction
                else:
                    # Publish all transaction changes in a single file replacement.
                    self._save()
                self.transaction = None
            return []

        # flush the database, removing all keys and expiration times
        if command == "FLUSHDB":
            self._require(not args)
            self.data.clear()
            self.expires.clear()
            self._changed()
            return []
        
        # set or get a key/value pair, or multiple pairs at once, with optional expiration times
        if command in ("SET", "MSET"):
            valid = len(args) == 2 if command == "SET" else bool(args) and len(args) % 2 == 0
            self._require(valid)
            for key, value in zip(args[::2], args[1::2]):
                self.data[key] = value
                self.expires.pop(key, None)
            self._changed()
            return []

        # get or delete a key/value pair, or multiple pairs at once, with optional expiration times
        if command in ("GET", "MGET"):
            self._require(len(args) == 1 if command == "GET" else bool(args))
            values = [self._get(key, str) for key in args]
            # MGET needs a placeholder to preserve the position of every key.
            missing = "" if command == "GET" else "nil"
            return [missing if value is None else value for value in values]

        if command in ("DEL", "EXISTS"):
            self._require(bool(args))
            count = 0
            for key in args:
                self._expire(key)
                if key in self.data:
                    count += 1
                    if command == "DEL":
                        del self.data[key]
                        self.expires.pop(key, None)
            if command == "DEL" and count:
                self._changed()
            return [str(count)]

        # sets an expiration time for a key, returns 1 if successful, 0 if the key does not exist
        if command == "EXPIRE":
            self._require(len(args) == 2)
            key, seconds = args[0], int(args[1])
            self._expire(key)
            if key not in self.data:
                return ["0"]
            self.expires[key] = self.clock() + seconds
            self._expire(key)
            self._changed()
            return ["1"]

        # returns the remaining time to live of a key that has an expiration time, in seconds
        if command == "TTL":
            self._require(len(args) == 1)
            key = args[0]
            self._expire(key)
            if key not in self.data:
                return ["-2"]
            if key not in self.expires:
                return ["-1"]
            return [str(max(0, math.ceil(self.expires[key] - self.clock())))]


        # returns a sorted list of keys in the database that are within the specified range
        if command == "RANGE":
            self._require(len(args) == 2)
            lower, upper = args
            for key in list(self.data):
                self._expire(key)
            # Quoted empty bounds mean there is no lower or upper limit.
            keys = sorted(
                key for key in self.data
                if (not lower or key >= lower) and (not upper or key <= upper)
            )
            return keys + ["END"]

        # increment or decrement a numeric value stored at a key, returning the new value
        if command in ("INCR", "DECR"):
            self._require(len(args) == 1)
            key = args[0]
            value = self._get(key, str)
            number = int(value) if value is not None else 0
            number += 1 if command == "INCR" else -1
            self.data[key] = str(number)
            self._changed()
            return [str(number)]

        # handle hash commands, which store field/value pairs under a key
        if command == "HSET":
            self._require(len(args) >= 3 and len(args) % 2 == 1)
            key = args[0]
            fields = self._get(key, dict)
            if fields is None:
                fields = {}
                self.data[key] = fields
            count = 0
            for field, value in zip(args[1::2], args[2::2]):
                count += field not in fields
                fields[field] = value
            self._changed()
            return [str(count)]

        if command in ("HGET", "HGETALL"):
            self._require(len(args) == (2 if command == "HGET" else 1))
            fields = self._get(args[0], dict) or {}
            if command == "HGET":
                return [fields.get(args[1], "nil")]
            return [item for field in sorted(fields) for item in (field, fields[field])]

        # handle list commands, which store ordered sequences of values under a key
        if command in ("LPUSH", "RPUSH"):
            self._require(len(args) >= 2)
            key = args[0]
            items = self._get(key, list)
            if items is None:
                items = []
                self.data[key] = items
            if command == "LPUSH":
                items[:0] = reversed(args[1:])
            else:
                items.extend(args[1:])
            self._changed()
            return [str(len(items))]

        if command in ("LPOP", "RPOP"):
            self._require(len(args) == 1)
            key = args[0]
            items = self._get(key, list)
            if not items:
                return ["nil"]
            value = items.pop(0 if command == "LPOP" else -1)
            if not items:
                del self.data[key]
                self.expires.pop(key, None)
            self._changed()
            return [value]

        if command == "LRANGE":
            self._require(len(args) == 3)
            start, stop = int(args[1]), int(args[2])
            items = self._get(args[0], list) or []
            # Negative indexes count from the end; the stop index is inclusive.
            if start < 0:
                start += len(items)
            if stop < 0:
                stop += len(items)
            return items[max(0, start) : stop + 1] if stop >= 0 else []

        raise ValueError(f"unknown command: {command}")


# main function that reads commands from standard input and executes them on the database, handling errors gracefully
def main() -> None:
    """Read commands until EXIT or EOF; report invalid input without crashing."""
    try:
        database = Database()
        for line in sys.stdin:
            if line.strip().upper() == "EXIT":
                break
            try:
                output = database.execute(line)
            except ValueError as error:
                output = [f"ERR {error}"]
            for result in output:
                print(result, flush=True)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Database error: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main() # this calls the main function when the script is run directly
