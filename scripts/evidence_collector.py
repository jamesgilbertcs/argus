"""Bounded Zetaris retrieval and a separate, pure evidence-bundle builder.

No local telemetry/evaluation reads, scenario logic, assessments or model calls.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

if __package__:
    from .evidence_queries import BATCH_SIZE, EVENT_TABLES, KEYS, SCHEMAS, TIME_COLUMNS, EvidenceQuery, identifier, instant, ip_literal, utc
    from .export_zetaris import decode_array
    from .zetaris_client import ProtocolError, QueryResult, ZetarisClient, ZetarisError
else:
    from evidence_queries import BATCH_SIZE, EVENT_TABLES, KEYS, SCHEMAS, TIME_COLUMNS, EvidenceQuery, identifier, instant, ip_literal, utc
    from export_zetaris import decode_array
    from zetaris_client import ProtocolError, QueryResult, ZetarisClient, ZetarisError


def json_value(value: Any) -> Any:
    """Lossless JSON adapter for native Decimal values; source strings stay strings."""
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def canonical(value: Any) -> str:
    return json.dumps(json_value(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def serialise(value: Any) -> str:
    return json.dumps(json_value(value), sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


@dataclass(frozen=True)
class CollectionOptions:
    before_hours: int = 24
    after_hours: int = 1
    row_limit: int = 200
    max_queries: int = 80
    max_references: int = 100
    max_subjects: int = 100

    def __post_init__(self) -> None:
        for name, low, high in [("before_hours", 1, 168), ("after_hours", 0, 24),
                                ("row_limit", 1, 1000), ("max_queries", 1, 200),
                                ("max_references", 1, 200), ("max_subjects", 1, 200)]:
            if type(getattr(self, name)) is not int or not low <= getattr(self, name) <= high:
                raise ValueError(f"Invalid collection option: {name}")


@dataclass
class Observation:
    query: EvidenceQuery
    limit: int
    status: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    roles: set[str] = field(default_factory=set)
    result: QueryResult | None = field(default=None, repr=False)
    error_type: str | None = None
    error_request_id: str | None = None
    http_status: int | None = None
    timestamp_context: list[dict[str, Any]] = field(default_factory=list)

    @property
    def query_id(self) -> str:
        return "Q" + hashlib.sha256(canonical({"query": self.query.scope(), "sql": self.query.sql(), "limit": self.limit}).encode()).hexdigest()[:20]


class ZetarisEvidenceSource:
    """Transport adapter. Only this component performs remote evidence reads."""

    def __init__(self, client: ZetarisClient) -> None:
        self.client = client

    def fetch(self, query: EvidenceQuery, limit: int) -> Observation:
        limit = min(limit, self.client.config.query_limit)
        observation = Observation(query, limit, "unavailable")
        try:
            result = self.client.execute_evidence_query(query, limit=limit)
            observation.result = result
            observation.rows = result.records()
            if result.headers is None or not set(SCHEMAS[query.table]) <= set(result.headers):
                raise ProtocolError("Missing evidence columns")
            for row in observation.rows:
                if all(key in row for key in TIME_COLUMNS):
                    context = {key: row.pop(key) for key in TIME_COLUMNS}
                    observation.timestamp_context.append({"record_fingerprint": hashlib.sha256(canonical(row).encode()).hexdigest(),
                                                          **context})
            observation.status = "potentially_truncated" if result.possibly_truncated else "complete"
        except ZetarisError as error:
            observation.status = "malformed" if isinstance(error, ProtocolError) else "unavailable"
            observation.error_type = type(error).__name__
            observation.error_request_id = error.request_id
            observation.http_status = error.status_code
        observation.rows.sort(key=canonical)
        observation.timestamp_context.sort(key=canonical)
        return observation


@dataclass
class CollectionSnapshot:
    alert_id: str
    options: CollectionOptions
    observations: list[Observation] = field(default_factory=list)
    references: list[dict[str, Any]] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    window: dict[str, str] | None = None


def evidence_instant(snapshot: CollectionSnapshot, table: str, row: dict) -> datetime:
    try:
        return instant(row.get("timestamp"))
    except (ValueError, TypeError):
        verified = _projected_instants(snapshot, table, row)
        if len(verified) == 1:
            return next(iter(verified))
        raise ValueError("Source timestamp timezone is unverified") from None


def _projected_instants(snapshot: CollectionSnapshot, table: str, row: dict) -> set[datetime]:
    fingerprint = hashlib.sha256(canonical(row).encode()).hexdigest()
    verified = set()
    for item in snapshot.observations:
        if item.query.table != table:
            continue
        for context in item.timestamp_context:
            if (context["record_fingerprint"] == fingerprint
                    and context["_argus_session_timezone"] in {"UTC", "Etc/UTC", "GMT", "Z", "+00:00"}
                    and context["_argus_timestamp_type"] in {"timestamp", "timestamp_ltz"}):
                value = context["_argus_sql_timestamp"]
                if isinstance(value, str):
                    try:
                        verified.add(datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc))
                    except ValueError:
                        pass
    return verified


class EvidenceCollector:
    """A finite query plan; construction of the public bundle is a separate function."""

    def __init__(self, source: ZetarisEvidenceSource, options: CollectionOptions | None = None) -> None:
        self.source = source
        self.options = options or CollectionOptions()

    def collect(self, alert_id: str) -> CollectionSnapshot:
        identifier(alert_id)
        run = _CollectionRun(self.source, CollectionSnapshot(alert_id, self.options))
        return run.collect()


class _CollectionRun:
    def __init__(self, source: ZetarisEvidenceSource, snapshot: CollectionSnapshot) -> None:
        self.source, self.snapshot = source, snapshot
        self.cache: dict[str, Observation] = {}

    def issue(self, code: str, **context: Any) -> None:
        self.snapshot.issues.append({"code": code, **context})

    def get(self, query: EvidenceQuery, role: str) -> Observation:
        query.sql()  # Validate closed templates even when the transport is mocked.
        key = canonical(query.scope())
        if key not in self.cache:
            if len(self.cache) >= self.snapshot.options.max_queries:
                observation = Observation(query, self.snapshot.options.row_limit, "budget_exhausted")
            else:
                observation = self.source.fetch(query, self.snapshot.options.row_limit)
            self.cache[key] = observation
            self.snapshot.observations.append(observation)
        self.cache[key].roles.add(role)
        return self.cache[key]

    def records(self, table: str) -> list[dict[str, Any]]:
        records = {canonical(row): row for observation in self.cache.values()
                   if observation.query.table == table and observation.status in {"complete", "potentially_truncated"}
                   for row in observation.rows}
        return [records[key] for key in sorted(records)]

    def values(self, field_name: str, tables: tuple[str, ...], *, ips: bool = False) -> list[str]:
        values = set()
        for table in tables:
            for row in self.records(table):
                value = row.get(field_name)
                if value in (None, ""):
                    continue
                try:
                    ip_literal(value) if ips else identifier(value)
                    values.add(value)
                except (ValueError, TypeError):
                    self.issue("invalid_source_field", table=table, source_id=row.get(KEYS[table]), field=field_name)
        return sorted(values)

    def bounded(self, values: list[str], purpose: str) -> list[str]:
        maximum = self.snapshot.options.max_subjects
        if len(values) > maximum:
            self.issue("subject_limit", purpose=purpose, omitted_values=values[maximum:])
        return values[:maximum]

    def batches(self, table: str, kind: str, values: list[str], role: str) -> None:
        for offset in range(0, len(values), BATCH_SIZE):
            self.get(EvidenceQuery(table, kind, values=tuple(values[offset:offset + BATCH_SIZE])), role)

    def sessions(self) -> None:
        successful = {row.get("session_id") for row in self.records("authentication_logs") if row.get("outcome") == "SUCCESS"}
        sessions = [value for value in self.values("session_id", ("resource_access_logs",)) if value not in successful]
        self.batches("authentication_logs", "sessions", self.bounded(sessions, "session_authentication"), "session")

    def collect(self) -> CollectionSnapshot:
        snapshot = self.snapshot
        self.get(EvidenceQuery("security_alerts", "ids", values=(snapshot.alert_id,)), "alert")
        alerts = [row for row in self.records("security_alerts") if row.get("alert_id") == snapshot.alert_id]
        if not alerts:
            return snapshot
        if len(alerts) != 1:
            self.issue("ambiguous_alert", source_id=snapshot.alert_id)
            return snapshot  # Do not choose one conflicting alert as the anchor.
        alert = alerts[0]
        try:
            anchor = evidence_instant(snapshot, "security_alerts", alert)
            snapshot.window = {"start": utc(anchor - timedelta(hours=snapshot.options.before_hours)),
                               "end": utc(anchor + timedelta(hours=snapshot.options.after_hours))}
        except (ValueError, TypeError, OverflowError):
            self.issue("history_window_unavailable", source_id=snapshot.alert_id, field="timestamp")
        try:
            references = decode_array(alert.get("related_event_ids"))
            if references is None:
                self.issue("missing_reference_list", source_id=snapshot.alert_id)
                references = []
        except (ValueError, TypeError, AttributeError):
            self.issue("malformed_reference_list", source_id=snapshot.alert_id)
            references = []
        unique = set()
        selected = set()
        for position, event_id in enumerate(references):
            reference = {"position": position, "event_id": event_id, "duplicate": event_id in unique}
            unique.add(event_id)
            try:
                identifier(event_id)
                if event_id in selected or len(selected) < snapshot.options.max_references:
                    selected.add(event_id)
                else:
                    reference["status"] = "not_collected_due_to_limit"
            except (ValueError, TypeError):
                reference["status"] = "invalid_identifier"
            snapshot.references.append(reference)
        if any(row.get("status") for row in snapshot.references):
            self.issue("unresolved_reference_input", source_id=snapshot.alert_id)
        # Resolve across both operational event tables without guessing an ID's type.
        for table in EVENT_TABLES:
            self.batches(table, "ids", sorted(selected), "referenced")
        self.sessions()
        users = self.bounded(self.values("user_id", ("security_alerts", *EVENT_TABLES)), "history_users")
        ips = self.bounded(self.values("source_ip", ("security_alerts", *EVENT_TABLES), ips=True), "history_ips")
        if snapshot.window and (users or ips):
            # Disjoint bounded batches: user scope, then source scope; merge evidence
            # by source ID/variant instead of duplicating records in the bundle.
            for table in EVENT_TABLES:
                for field_name, values in (("user_ids", users), ("source_ips", ips)):
                    for offset in range(0, len(values), BATCH_SIZE):
                        self.get(EvidenceQuery(table, "history", **{field_name: tuple(values[offset:offset + BATCH_SIZE])},
                                               **snapshot.window), "contextual")
        elif not users and not ips:
            self.issue("history_subjects_unavailable", source_id=snapshot.alert_id)
        self.sessions()  # Also resolve authentications behind contextual accesses.
        users = self.bounded(self.values("user_id", ("security_alerts", *EVENT_TABLES)), "user_metadata")
        resources = self.bounded(self.values("resource_id", ("resource_access_logs",)), "resource_metadata")
        ips = self.bounded(self.values("source_ip", ("security_alerts", *EVENT_TABLES), ips=True), "network_metadata")
        self.batches("users", "ids", users, "metadata")
        self.batches("resources", "ids", resources, "metadata")
        self.batches("approved_networks", "networks", ips, "network")
        return snapshot


def _resolution(snapshot: CollectionSnapshot, table: str, field_name: str, value: str) -> str:
    """A missing result is definitive only for a completed relevant lookup."""
    queries = [item for item in snapshot.observations if item.query.table == table
               and ((item.query.kind == "ids" and field_name == KEYS[table])
                    or (item.query.kind == "sessions" and field_name == "session_id")
                    or (item.query.kind == "networks" and field_name == "ip_address"))
               and value in item.query.values]
    if any(item.status == "complete" for item in queries):
        return "missing"
    if any(item.status == "potentially_truncated" for item in queries):
        return "potentially_truncated"
    return "unavailable"


def build_bundle(snapshot: CollectionSnapshot) -> dict[str, Any]:
    """Pure construction; no HTTP, clock, UUID generation, local files or labels."""
    records: dict[str, dict[str, dict]] = {table: {} for table in SCHEMAS}
    queries, issues = [], list(snapshot.issues)
    for item in snapshot.observations:
        summary = {"query_id": item.query_id, "scope": item.query.scope(), "limit": item.limit,
                   "sql": item.query.sql(),
                   "status": item.status, "returned_rows": len(item.rows),
                   "reported_total": item.result.total if item.result else None,
                   "headers": list(item.result.headers) if item.result and item.result.headers is not None else None,
                   "error_type": item.error_type}
        summary["timestamp_context"] = item.timestamp_context
        if item.result:
            extra = sorted(set(item.result.raw_response) - {"headers", "data", "total", "timeUsed"})
            if extra:
                summary["additional_response_metadata"] = extra
                issues.append({"code": "unreviewed_response_metadata", "query_id": item.query_id})
        queries.append(summary)
        if item.status != "complete":
            issues.append({"code": "query_" + item.status, "query_id": item.query_id, "table": item.query.table})
        if item.status not in {"complete", "potentially_truncated"}:
            if item.rows:
                summary["unusable_rows"] = json_value(item.rows)
            continue
        table = item.query.table
        seen = set()
        for row in item.rows:
            source_id = row.get(KEYS[table])
            if not isinstance(source_id, str) or not source_id:
                summary.setdefault("unusable_rows", []).append(json_value(row))
                issues.append({"code": "missing_source_identifier", "table": table, "query_id": item.query_id})
                continue
            optional = {"security_alerts": {"user_id"}, "authentication_logs": {"session_id", "failure_reason"}}
            for column in SCHEMAS[table]:
                if row.get(column) in (None, "") and column not in optional.get(table, set()):
                    issues.append({"code": "missing_source_field", "table": table, "source_id": source_id, "field": column})
            digest = hashlib.sha256(canonical(row).encode()).hexdigest()
            if digest in seen:
                issues.append({"code": "duplicate_source_row", "table": table, "source_id": source_id, "query_id": item.query_id})
            seen.add(digest)
            record = records[table].setdefault(digest, {
                "source": {"table": f"ARGUS_SECURITY.{table}_compat_test", "id_column": KEYS[table], "id": source_id},
                "variant_id": digest, "record": json_value(row), "roles": set(), "query_ids": set(),
            })
            record["roles"].update(item.roles)
            record["query_ids"].add(item.query_id)
            query = item.query
            field_name = {"ids": KEYS[table], "sessions": "session_id", "networks": "ip_address"}.get(query.kind)
            if field_name and row.get(field_name) not in query.values:
                issues.append({"code": "query_scope_mismatch", "table": table, "source_id": source_id, "query_id": item.query_id})
            if query.kind == "history":
                try:
                    matches = ((row.get("user_id") in query.user_ids or row.get("source_ip") in query.source_ips)
                               and instant(query.start) <= evidence_instant(snapshot, table, row) <= instant(query.end))
                    if not matches:
                        issues.append({"code": "query_scope_mismatch", "table": table, "source_id": source_id, "query_id": item.query_id})
                except (ValueError, TypeError):
                    issues.append({"code": "timestamp_unverified", "table": table, "source_id": source_id})
    output_records = {}
    for table, entries in records.items():
        output_records[table] = sorted(entries.values(), key=lambda row: (row["source"]["id"], row["variant_id"]))
        grouped: dict[str, list] = {}
        for row in output_records[table]:
            row["roles"], row["query_ids"] = sorted(row["roles"]), sorted(row["query_ids"])
            if "timestamp" in row["record"]:
                try:
                    value = evidence_instant(snapshot, table, row["record"])
                    row["timestamp_interpretation"] = {"utc": value.isoformat().replace("+00:00", "Z"),
                        "basis": "explicit_source_offset" if _has_offset(row["record"]["timestamp"]) else "verified_same_query_utc_projection"}
                    projections = _projected_instants(snapshot, table, row["record"])
                    if len(projections) > 1 or any(projected != value for projected in projections):
                        issues.append({"code": "conflicting_timestamp_projections", "table": table, "source_id": row["source"]["id"]})
                except (ValueError, TypeError):
                    issues.append({"code": "timestamp_unverified", "table": table, "source_id": row["source"]["id"]})
            grouped.setdefault(row["source"]["id"], []).append(row)
        for source_id, variants in grouped.items():
            if len(variants) > 1:
                issues.append({"code": "conflicting_source_records", "table": table, "source_id": source_id})

    def matching(table: str, field_name: str, value: Any) -> list[dict]:
        return [entry for entry in output_records[table] if entry["record"].get(field_name) == value]

    references = []
    for original in snapshot.references:
        reference = dict(original)
        matches = [entry for table in EVENT_TABLES for entry in matching(table, "event_id", original["event_id"])]
        reference["records"] = [{"table": entry["source"]["table"], "id": entry["source"]["id"], "variant_id": entry["variant_id"]}
                                for entry in matches]
        if "status" not in reference:
            states = [_resolution(snapshot, table, "event_id", original["event_id"]) for table in EVENT_TABLES]
            reference["status"] = ("ambiguous" if len(matches) > 1 else "resolved" if matches
                                   else "missing" if states == ["missing", "missing"]
                                   else "potentially_truncated" if "potentially_truncated" in states else "unavailable")
        if reference["status"] != "resolved":
            issues.append({"code": "reference_" + reference["status"], "event_id": reference["event_id"], "position": reference["position"]})
        references.append(reference)
    checks = []
    for table in ("security_alerts", *EVENT_TABLES):
        for entry in output_records[table]:
            row, source_id = entry["record"], entry["source"]["id"]
            user_id = row.get("user_id")
            if user_id not in (None, "") and not matching("users", "user_id", user_id):
                issues.append({"code": "user_context_" + _resolution(snapshot, "users", "user_id", user_id),
                               "table": table, "source_id": source_id, "user_id": user_id})
            if table == "resource_access_logs":
                resource_id, session_id = row.get("resource_id"), row.get("session_id")
                if not matching("resources", "resource_id", resource_id):
                    issues.append({"code": "resource_context_" + _resolution(snapshot, "resources", "resource_id", resource_id),
                                   "source_id": source_id, "resource_id": resource_id})
                sessions = [entry["record"] for entry in matching("authentication_logs", "session_id", session_id)
                            if session_id not in (None, "") and entry["record"].get("outcome") == "SUCCESS"]
                if len(sessions) != 1:
                    state = "ambiguous" if sessions else _resolution(snapshot, "authentication_logs", "session_id", session_id)
                    issues.append({"code": "session_" + state, "source_id": source_id, "session_id": session_id})
                for session in sessions:
                    for field_name in ("user_id", "source_ip"):
                        if row.get(field_name) != session.get(field_name):
                            issues.append({"code": "session_context_mismatch", "source_id": source_id, "session_id": session_id, "field": field_name})
                    try:
                        if evidence_instant(snapshot, table, row) <= evidence_instant(snapshot, "authentication_logs", session):
                            issues.append({"code": "access_not_after_authentication", "source_id": source_id, "session_id": session_id})
                    except (ValueError, TypeError):
                        issues.append({"code": "timestamp_unverified", "table": table, "source_id": source_id})
    alert_entries = matching("security_alerts", "alert_id", snapshot.alert_id)
    if len(alert_entries) == 1:
        alert_user = alert_entries[0]["record"].get("user_id")
        if alert_user not in (None, ""):
            for table in EVENT_TABLES:
                for entry in output_records[table]:
                    if "referenced" in entry["roles"] and entry["record"].get("user_id") != alert_user:
                        issues.append({"code": "alert_user_mismatch", "table": table, "source_id": entry["source"]["id"]})
    ips = sorted({entry["record"].get("source_ip") for table in ("security_alerts", *EVENT_TABLES)
                  for entry in output_records[table] if isinstance(entry["record"].get("source_ip"), str) and entry["record"]["source_ip"]})
    for ip in ips:
        matches = matching("approved_networks", "ip_address", ip)
        checks.append({"source_ip": ip, "status": "matched" if matches else
                       "no_exact_match" if _resolution(snapshot, "approved_networks", "ip_address", ip) == "missing" else
                       _resolution(snapshot, "approved_networks", "ip_address", ip),
                       "network_ids": sorted(entry["source"]["id"] for entry in matches)})
    issues = [json.loads(value) for value in sorted({canonical(row) for row in issues})]
    alert_query = snapshot.observations[0]
    status = ("alert_not_found" if not alert_entries and alert_query.status == "complete" else
              "alert_unavailable" if not alert_entries else "partial" if issues else "collected")
    return {"schema_version": "1.0", "alert_id": snapshot.alert_id, "collection_status": status,
            "scope": {"options": asdict(snapshot.options), "history_window": snapshot.window,
                      "history_subject_expansion": "one_pass_from_alert_references_and_their_sessions"},
            "records": output_records, "references": references, "network_checks": checks,
            "queries": sorted(queries, key=lambda row: row["query_id"]), "issues": issues}


def _has_offset(value: Any) -> bool:
    try:
        instant(value)
        return True
    except (ValueError, TypeError):
        return False


def build_trace(snapshot: CollectionSnapshot) -> dict[str, Any]:
    """Volatile wire provenance is explicit and separate from deterministic evidence."""
    return {"alert_id": snapshot.alert_id, "queries": [
        {"query_id": item.query_id, "status": item.status,
         "request_id": item.result.request_id if item.result else item.error_request_id,
         "wire_query_id": item.result.query_id if item.result else None,
         "response_sha256": hashlib.sha256(item.result.response_bytes).hexdigest() if item.result else None,
         "response_metadata": json_value({key: value for key, value in item.result.raw_response.items()
                                           if key != "data" or item.status == "malformed"}) if item.result else None,
         "http_status": item.http_status, "error_type": item.error_type} for item in snapshot.observations]}
