"""Opt-in, read-only live REST verification. Not part of normal unittest discovery."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable

if __package__:
    from .zetaris_client import (COUNT_QUERIES, LARGE_TRANSFERS_SQL, PING_SQL, ROOT,
                                ProtocolError, ZetarisClient, ZetarisConfig, ZetarisError, exact_integer)
else:
    from zetaris_client import (COUNT_QUERIES, LARGE_TRANSFERS_SQL, PING_SQL, ROOT,
                               ProtocolError, ZetarisClient, ZetarisConfig, ZetarisError, exact_integer)


def check_live(client: ZetarisClient, report: Callable[[str], None] = print) -> None:
    for label, sql, column, expected in [
        ("SELECT 1", PING_SQL, "one", 1),
        ("authentication count", COUNT_QUERIES["authentication_logs"], "total", 1000),
    ]:
        result = client.execute_read_only(sql)
        rows = result.records()
        if len(rows) != 1 or set(rows[0]) != {column} or exact_integer(rows[0][column]) != expected:
            raise ProtocolError("Live scalar verification failed", request_id=result.request_id)
        report(f"PASS {label}: {column}={expected}, raw_type={type(rows[0][column]).__name__}, "
               f"checked_type=int, request_id={result.request_id}")
    result = client.execute_read_only(LARGE_TRANSFERS_SQL)
    rows = result.records()
    expected_rows = {"ACCESS001994": 2_400_000_000, "ACCESS002000": 950_000_000}
    seen = set()
    if len(rows) != 2:
        raise ProtocolError("Expected exactly two large transfers", request_id=result.request_id)
    for row in rows:
        if (set(row) != {"event_id", "bytes_transferred"} or type(row["event_id"]) is not str
                or row["event_id"] not in expected_rows or row["event_id"] in seen
                or exact_integer(row["bytes_transferred"]) != expected_rows[row["event_id"]]):
            raise ProtocolError("Large-transfer evidence verification failed", request_id=result.request_id)
        seen.add(row["event_id"])
    # Print only fixed verified values, not arbitrary server text or metadata.
    for event_id, expected in expected_rows.items():
        row = next(row for row in rows if row["event_id"] == event_id)
        report(f"PASS {event_id}: bytes_transferred={expected}, event_id_type=str, "
               f"raw_type={type(row['bytes_transferred']).__name__}, checked_type=int, request_id={result.request_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.local")
    args = parser.parse_args()
    try:
        check_live(ZetarisClient(ZetarisConfig.from_environment(args.env_file)))
    except ZetarisError as error:
        print(f"FAIL {type(error).__name__}: {error}")
        return 1
    print("Live direct REST verification passed; only the three approved SELECT statements were sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
