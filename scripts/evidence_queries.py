"""Closed, typed SELECT templates for evidence retrieval; never accepts SQL text."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

if __package__:
    from .generate_data import SCHEMAS
else:
    from generate_data import SCHEMAS


KEYS = {table: schema[0] for table, schema in SCHEMAS.items()}
EVENT_TABLES = ("authentication_logs", "resource_access_logs")
TIME_TABLES = ("security_alerts", *EVENT_TABLES)
TIME_COLUMNS = ("_argus_session_timezone", "_argus_timestamp_type", "_argus_sql_timestamp")
BATCH_SIZE = 20


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("Invalid source identifier")
    return value


def ip_literal(value: str) -> str:
    # ipaddress alone permits IPv6 zone IDs containing arbitrary text. Zone IDs
    # are inappropriate for exact egress matching and must never enter SQL.
    if not isinstance(value, str) or not re.fullmatch(r"[0-9A-Fa-f:.]+", value):
        raise ValueError("Invalid exact source IP")
    ipaddress.ip_address(value)
    return value


def instant(value: str) -> datetime:
    """Require an explicit timezone; do not infer UTC for naive API timestamps."""
    if not isinstance(value, str):
        raise ValueError("Timestamp must be text with an explicit timezone")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timestamp has no timezone")
    return result.astimezone(timezone.utc)


def utc(value: datetime) -> str:
    precision = "microseconds" if value.microsecond else "seconds"
    return value.astimezone(timezone.utc).isoformat(timespec=precision).replace("+00:00", "Z")


def _values(values: tuple[str, ...], *, ips: bool = False) -> str:
    if not isinstance(values, tuple) or not 1 <= len(values) <= BATCH_SIZE or tuple(sorted(set(values))) != values:
        raise ValueError("Query values must be a sorted, unique, bounded tuple")
    for value in values:
        if ips:
            ip_literal(value)  # Validate; retain the original spelling.
        else:
            identifier(value)
    return ", ".join(f"'{value}'" for value in values)


@dataclass(frozen=True)
class EvidenceQuery:
    table: str
    kind: str
    values: tuple[str, ...] = ()
    user_ids: tuple[str, ...] = ()
    source_ips: tuple[str, ...] = ()
    start: str | None = None
    end: str | None = None

    def scope(self) -> dict:
        return asdict(self)

    def sql(self) -> str:
        if self.table not in SCHEMAS:
            raise ValueError("Unknown evidence table")
        if self.kind != "history" and (self.user_ids or self.source_ips or self.start is not None or self.end is not None):
            raise ValueError("Unexpected query parameters")
        if self.kind == "ids":
            predicate = f"{KEYS[self.table]} IN ({_values(self.values)})"
        elif self.kind == "sessions" and self.table == "authentication_logs":
            predicate = f"session_id IN ({_values(self.values)}) AND outcome = 'SUCCESS'"
        elif self.kind == "networks" and self.table == "approved_networks":
            predicate = f"ip_address IN ({_values(self.values, ips=True)})"
        elif self.kind == "history" and self.table in EVENT_TABLES:
            if self.values or not self.start or not self.end:
                raise ValueError("History requires a window and subjects")
            start, end = instant(self.start), instant(self.end)
            if utc(start) != self.start or utc(end) != self.end or not 0 < (end - start).total_seconds() <= 192 * 3600:
                raise ValueError("Invalid canonical UTC history window")
            clauses = []
            if self.user_ids:
                clauses.append(f"user_id IN ({_values(self.user_ids)})")
            if self.source_ips:
                clauses.append(f"source_ip IN ({_values(self.source_ips, ips=True)})")
            if not clauses:
                raise ValueError("History requires users or source IPs")
            predicate = f"timestamp >= '{self.start}' AND timestamp <= '{self.end}' AND (" + " OR ".join(clauses) + ")"
        else:
            raise ValueError("Unknown evidence query template")
        order = f"timestamp, {KEYS[self.table]}" if self.table in EVENT_TABLES else KEYS[self.table]
        projection = "*"
        if self.table in TIME_TABLES:
            projection += (", current_timezone() AS _argus_session_timezone, typeof(timestamp) AS _argus_timestamp_type, "
                           "date_format(timestamp, 'yyyy-MM-dd HH:mm:ss.SSSSSS') AS _argus_sql_timestamp")
        return f"SELECT {projection} FROM ARGUS_SECURITY.{self.table}_compat_test WHERE {predicate} ORDER BY {order}"
