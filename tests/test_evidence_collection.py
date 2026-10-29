"""Phase 3A tests use mocked Zetaris results, never local telemetry or labels."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.collect_evidence import output_path
from scripts.evidence_collector import (
    CollectionOptions, EvidenceCollector, ZetarisEvidenceSource, build_bundle, build_trace, serialise,
)
from scripts.evidence_queries import SCHEMAS, EvidenceQuery, TIME_COLUMNS, instant
from scripts.zetaris_client import (
    ConnectionError, ProtocolError, QueryResult, SQLPolicyError, ZetarisClient, ZetarisConfig,
)


def record(table: str, **values: object) -> dict:
    result = {key: "synthetic" for key in SCHEMAS[table]}
    result.update(values)
    return result


def fixture() -> dict[str, list[dict]]:
    tables = {table: [] for table in SCHEMAS}
    tables["security_alerts"] = [record("security_alerts", alert_id="ALERT_TEST", timestamp="2026-09-10T10:12:00Z",
        user_id="USR_A", source_ip="192.0.2.55", related_event_ids="|ACCESS_A|AUTH_A|", alert_type="UNUSUAL_DATA_EXPORT")]
    tables["authentication_logs"] = [record("authentication_logs", event_id="AUTH_A", timestamp="2026-09-10T10:00:00Z",
        user_id="USR_A", source_ip="192.0.2.55", session_id="SESSION_A", outcome="SUCCESS", failure_reason="")]
    tables["resource_access_logs"] = [record("resource_access_logs", event_id="ACCESS_A", timestamp="2026-09-10T10:08:00Z",
        user_id="USR_A", source_ip="192.0.2.55", session_id="SESSION_A", resource_id="RESOURCE_A", bytes_transferred="2400000000")]
    tables["users"] = [record("users", user_id="USR_A", department="Finance")]
    tables["resources"] = [record("resources", resource_id="RESOURCE_A", allowed_departments="|Finance|IT / Security|")]
    tables["approved_networks"] = [record("approved_networks", network_id="NET_A", ip_address="192.0.2.55",
        network_type="CORPORATE_VPN", status="ACTIVE", advertised_location="New York%2C US")]
    return tables


class MockZetaris:
    def __init__(self, tables: dict | None = None) -> None:
        self.tables = fixture() if tables is None else tables
        self.calls: list[EvidenceQuery] = []
        self.failures: dict[tuple[str, str], Exception] = {}
        self.truncated: set[tuple[str, str]] = set()
        self.empty_histories = False
        self.reverse = False
        self.clock_metadata: str | None = None
        self.client = Mock(spec=ZetarisClient)
        self.client.config = ZetarisConfig("http://localhost:8888/api/v1.0", "1", "unit-test-only-key")
        self.client.execute_evidence_query.side_effect = self.execute

    def execute(self, query: EvidenceQuery, *, limit: int) -> QueryResult:
        self.calls.append(query)
        query.sql()
        if (query.table, query.kind) in self.failures:
            raise self.failures[query.table, query.kind]
        rows = self.tables[query.table]
        key = SCHEMAS[query.table][0]
        if query.kind == "ids":
            rows = [row for row in rows if row[key] in query.values]
        elif query.kind == "sessions":
            rows = [row for row in rows if row["session_id"] in query.values and row["outcome"] == "SUCCESS"]
        elif query.kind == "networks":
            rows = [row for row in rows if row["ip_address"] in query.values]
        elif query.kind == "history":
            rows = [] if self.empty_histories else [row for row in rows
                if (row["user_id"] in query.user_ids or row["source_ip"] in query.source_ips)
                and instant(query.start) <= instant(row["timestamp"]) <= instant(query.end)]
        rows = copy.deepcopy(rows)
        if self.reverse:
            rows.reverse()
        total = len(rows) + (5 if (query.table, query.kind) in self.truncated else 0)
        rows = rows[:limit]
        headers = tuple(SCHEMAS[query.table])
        if self.clock_metadata and "timestamp" in headers:
            headers = (*headers, *TIME_COLUMNS)
            for row in rows:
                row.update(dict(zip(TIME_COLUMNS, [self.clock_metadata, "timestamp", "2026-09-10 10:12:00.000000"])))
        raw = {"headers": list(headers), "data": [[row[field] for field in headers] for row in rows],
               "total": total, "timeUsed": len(self.calls)}
        return QueryResult(headers, tuple(tuple(row[field] for field in headers) for row in rows),
                           total, len(self.calls), str(uuid.uuid4()), str(uuid.uuid4()), limit, raw,
                           json.dumps(raw, default=str).encode())

    def collect(self, alert_id: str = "ALERT_TEST", options: CollectionOptions | None = None):
        snapshot = EvidenceCollector(ZetarisEvidenceSource(self.client), options).collect(alert_id)
        return snapshot, build_bundle(snapshot)


class QueryTemplateTests(unittest.TestCase):
    def test_narrow_queries_use_only_authoritative_tables_and_fixed_columns(self) -> None:
        queries = [EvidenceQuery("security_alerts", "ids", values=("ALERT_TEST",)),
                   EvidenceQuery("authentication_logs", "sessions", values=("SESSION_A",)),
                   EvidenceQuery("approved_networks", "networks", values=("192.0.2.55",)),
                   EvidenceQuery("resource_access_logs", "history", user_ids=("USR_A",), source_ips=("192.0.2.55",),
                                 start="2026-09-09T10:12:00Z", end="2026-09-10T11:12:00Z")]
        for query in queries:
            sql = query.sql()
            self.assertIn(f"ARGUS_SECURITY.{query.table}_compat_test WHERE ", sql)
            self.assertIn(" ORDER BY ", sql)
            self.assertNotIn(";", sql)
        self.assertIn("outcome = 'SUCCESS'", queries[1].sql())
        self.assertIn(" OR ", queries[-1].sql())

    def test_rejects_injection_unbounded_queries_and_invalid_parameters(self) -> None:
        cases = [EvidenceQuery("evaluation", "ids", values=("A",)),
                 EvidenceQuery("security_alerts", "ids", values=("A' OR 1=1 --",)),
                 EvidenceQuery("users", "ids", values=()), EvidenceQuery("users", "ids", values=("B", "A")),
                 EvidenceQuery("users", "ids", values=("A",) * 21), EvidenceQuery("users", "sessions", values=("A",)),
                 EvidenceQuery("users", "history", user_ids=("U1",)),
                 EvidenceQuery("approved_networks", "networks", values=("localhost",)),
                 EvidenceQuery("approved_networks", "networks", values=("fe80::1%eth0' OR 1=1 --",)),
                 EvidenceQuery("approved_networks", "networks", values=("fe80::1%eth0",)),
                 EvidenceQuery("security_alerts", "ids", values=("A",), start="unexpected"),
                 EvidenceQuery("authentication_logs", "history", start="2026-09-10T00:00:00Z", end="2026-09-11T00:00:00Z"),
                 EvidenceQuery("authentication_logs", "history", user_ids=("U1",), start="2026-09-01T00:00:00Z", end="2026-09-30T00:00:00Z"),
                 EvidenceQuery("authentication_logs", "history", user_ids=("U1",), start="2026-09-10T00:00:00", end="2026-09-11T00:00:00Z")]
        for query in cases:
            with self.subTest(query=query), self.assertRaises((ValueError, TypeError)):
                query.sql()

    def test_client_preserves_old_allowlist_and_new_transport_limits(self) -> None:
        with patch("scripts.zetaris_client.build_opener") as builder:
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.status = 200
            response.headers = {"Content-Type": "application/json"}
            response.geturl.return_value = "http://localhost:8888/api/v1.0/sql-editor/sqls/run"
            response.read.return_value = b'{"headers":["alert_id"],"data":[],"total":0,"timeUsed":1}'
            builder.return_value.open.return_value = response
            client = ZetarisClient(ZetarisConfig("http://localhost:8888/api/v1.0", "1", "unit-test-only-key"))
            query = EvidenceQuery("security_alerts", "ids", values=("ALERT_TEST",))
            result = client.execute_evidence_query(query, limit=20)
            body = json.loads(builder.return_value.open.call_args.args[0].data)
            self.assertEqual(body["sql"], query.sql())
            self.assertEqual(body["limit"], 20)
            self.assertEqual(result.limit, 20)
            for invalid in [query.sql(), Mock(sql=Mock(return_value="DROP TABLE x"))]:
                with self.assertRaises(SQLPolicyError):
                    client.execute_evidence_query(invalid)
            with self.assertRaises(SQLPolicyError):
                client.execute_read_only(query.sql())


class CollectionTests(unittest.TestCase):
    def test_arbitrary_alert_multiple_references_original_identifiers_and_roles(self) -> None:
        source = MockZetaris()
        _, bundle = source.collect()
        self.assertEqual(bundle["collection_status"], "collected")
        self.assertEqual([row["event_id"] for row in bundle["references"]], ["ACCESS_A", "AUTH_A"])
        self.assertTrue(all(row["status"] == "resolved" for row in bundle["references"]))
        self.assertEqual(bundle["scope"]["history_window"], {"start": "2026-09-09T10:12:00Z", "end": "2026-09-10T11:12:00Z"})
        for table, entries in bundle["records"].items():
            for entry in entries:
                self.assertEqual(entry["source"]["id"], entry["record"][SCHEMAS[table][0]])
                self.assertEqual(entry["source"]["table"], f"ARGUS_SECURITY.{table}_compat_test")
                self.assertTrue(entry["query_ids"])
        access = bundle["records"]["resource_access_logs"][0]
        self.assertIn("referenced", access["roles"])
        self.assertIn("contextual", access["roles"])
        self.assertEqual(access["record"]["bytes_transferred"], "2400000000")
        self.assertTrue(all(query.kind != "history" or query.user_ids or query.source_ips for query in source.calls))

    def test_unknown_alert_is_not_an_empty_completed_collection(self) -> None:
        source = MockZetaris()
        _, bundle = source.collect("UNKNOWN_ALERT")
        self.assertEqual(bundle["collection_status"], "alert_not_found")
        self.assertEqual(len(source.calls), 1)
        self.assertEqual(bundle["references"], [])

    def test_invalid_alert_id_never_calls_zetaris(self) -> None:
        source = MockZetaris()
        with self.assertRaises(ValueError):
            source.collect("x'; DROP TABLE y")
        self.assertEqual(source.calls, [])

    def test_duplicate_references_keep_positions_but_not_duplicate_records(self) -> None:
        source = MockZetaris()
        source.tables["security_alerts"][0]["related_event_ids"] = "|ACCESS_A|ACCESS_A|AUTH_A|"
        _, bundle = source.collect()
        self.assertEqual([row["duplicate"] for row in bundle["references"]], [False, True, False])
        self.assertEqual([row["position"] for row in bundle["references"]], [0, 1, 2])
        self.assertEqual(len(bundle["records"]["resource_access_logs"]), 1)

    def test_missing_event_is_explicit_and_not_replaced_by_context(self) -> None:
        source = MockZetaris()
        source.tables["security_alerts"][0]["related_event_ids"] = "|ABSENT_EVENT|AUTH_A|"
        _, bundle = source.collect()
        self.assertEqual(bundle["references"][0]["status"], "missing")
        self.assertEqual(bundle["collection_status"], "partial")
        self.assertNotIn("referenced", bundle["records"]["resource_access_logs"][0]["roles"])

    def test_missing_user_and_resource_context(self) -> None:
        source = MockZetaris()
        source.tables["users"] = []
        source.tables["resources"] = []
        _, bundle = source.collect()
        codes = {issue["code"] for issue in bundle["issues"]}
        self.assertTrue({"user_context_missing", "resource_context_missing"} <= codes)

    def test_empty_context_is_distinct_from_unavailable_context(self) -> None:
        source = MockZetaris()
        source.empty_histories = True
        _, bundle = source.collect()
        histories = [query for query in bundle["queries"] if query["scope"]["kind"] == "history"]
        self.assertTrue(histories)
        self.assertTrue(all(query["status"] == "complete" and query["returned_rows"] == 0 for query in histories))
        self.assertEqual(bundle["collection_status"], "collected")

    def test_truncated_queries_preserve_rows_and_mark_missing_reference_uncertain(self) -> None:
        source = MockZetaris()
        source.tables["security_alerts"][0]["related_event_ids"] = "|ABSENT_EVENT|AUTH_A|"
        source.truncated.add(("authentication_logs", "ids"))
        _, bundle = source.collect()
        self.assertEqual(bundle["references"][0]["status"], "potentially_truncated")
        self.assertTrue(bundle["records"]["authentication_logs"])
        self.assertTrue(any(query["status"] == "potentially_truncated" for query in bundle["queries"]))

    def test_exact_row_cap_is_conservatively_marked(self) -> None:
        _, bundle = MockZetaris().collect(options=CollectionOptions(row_limit=1))
        self.assertEqual(bundle["collection_status"], "partial")
        self.assertTrue(any(query["status"] == "potentially_truncated" for query in bundle["queries"]))

    def test_connection_failure_and_safe_trace(self) -> None:
        source = MockZetaris()
        source.failures["resources", "ids"] = ConnectionError("DO_NOT_ECHO", request_id=str(uuid.uuid4()))
        snapshot, bundle = source.collect()
        self.assertTrue(any(issue["code"] == "resource_context_unavailable" for issue in bundle["issues"]))
        trace = build_trace(snapshot)
        self.assertNotIn("DO_NOT_ECHO", serialise(trace))
        self.assertTrue(any(query["request_id"] for query in trace["queries"] if query["status"] == "unavailable"))

    def test_malformed_zetaris_response_is_not_silently_empty(self) -> None:
        source = MockZetaris()
        source.failures["security_alerts", "ids"] = ProtocolError("DO_NOT_ECHO")
        _, bundle = source.collect()
        self.assertEqual(bundle["collection_status"], "alert_unavailable")
        self.assertEqual(bundle["queries"][0]["status"], "malformed")
        self.assertNotIn("DO_NOT_ECHO", serialise(bundle))

    def test_wrong_headers_are_reported_as_malformed_and_retained_in_trace(self) -> None:
        client = Mock(spec=ZetarisClient)
        client.config = ZetarisConfig("http://localhost:8888/api/v1.0", "1", "unit-test-only-key")
        client.execute_evidence_query.return_value = QueryResult(("wrong",), (("value",),), 1, 1,
            str(uuid.uuid4()), str(uuid.uuid4()), 200, {"headers": ["wrong"], "data": [["value"]], "total": 1, "timeUsed": 1}, b"{}")
        snapshot = EvidenceCollector(ZetarisEvidenceSource(client)).collect("ALERT_TEST")
        bundle = build_bundle(snapshot)
        self.assertEqual(bundle["queries"][0]["unusable_rows"], [{"wrong": "value"}])
        self.assertEqual(build_trace(snapshot)["queries"][0]["response_metadata"]["data"], [["value"]])

    def test_shared_ip_finds_additional_users_without_assuming_single_alert_user(self) -> None:
        source = MockZetaris()
        source.tables["security_alerts"][0]["user_id"] = ""
        second = copy.deepcopy(source.tables["authentication_logs"][0])
        second.update(event_id="AUTH_B", user_id="USR_B", session_id="SESSION_B")
        source.tables["authentication_logs"].append(second)
        source.tables["users"].append(record("users", user_id="USR_B"))
        _, bundle = source.collect()
        extra = next(row for row in bundle["records"]["authentication_logs"] if row["source"]["id"] == "AUTH_B")
        self.assertEqual(extra["roles"], ["contextual"])
        self.assertEqual({row["source"]["id"] for row in bundle["records"]["users"]}, {"USR_A", "USR_B"})

    def test_sessions_can_be_resolved_outside_history_window(self) -> None:
        source = MockZetaris()
        source.tables["security_alerts"][0]["related_event_ids"] = "|ACCESS_A|"
        source.tables["authentication_logs"][0]["timestamp"] = "2026-09-01T10:00:00Z"
        _, bundle = source.collect()
        auth = bundle["records"]["authentication_logs"][0]
        self.assertEqual(auth["roles"], ["session"])
        self.assertEqual(auth["record"]["timestamp"], "2026-09-01T10:00:00Z")

    def test_session_contradictions_are_retained(self) -> None:
        source = MockZetaris()
        source.tables["resource_access_logs"][0].update(source_ip="203.0.113.100", timestamp="2026-09-10T09:59:00Z")
        _, bundle = source.collect()
        codes = {issue["code"] for issue in bundle["issues"]}
        self.assertTrue({"session_context_mismatch", "access_not_after_authentication"} <= codes)
        self.assertEqual(bundle["records"]["resource_access_logs"][0]["record"]["source_ip"], "203.0.113.100")

    def test_conflicting_source_variants_are_not_overwritten(self) -> None:
        source = MockZetaris()
        second = copy.deepcopy(source.tables["resource_access_logs"][0])
        second["bytes_transferred"] = "1"
        source.tables["resource_access_logs"].append(second)
        _, bundle = source.collect()
        self.assertEqual(len(bundle["records"]["resource_access_logs"]), 2)
        self.assertEqual(bundle["references"][0]["status"], "ambiguous")
        self.assertTrue(any(issue["code"] == "conflicting_source_records" for issue in bundle["issues"]))

    def test_conflicting_alert_does_not_choose_arbitrary_anchor(self) -> None:
        source = MockZetaris()
        second = copy.deepcopy(source.tables["security_alerts"][0])
        second["timestamp"] = "2026-09-11T00:00:00Z"
        source.tables["security_alerts"].append(second)
        _, bundle = source.collect()
        self.assertEqual(len(source.calls), 1)
        self.assertEqual(bundle["collection_status"], "partial")
        self.assertIsNone(bundle["scope"]["history_window"])

    def test_unknown_network_is_not_a_missing_evidence_verdict(self) -> None:
        source = MockZetaris()
        source.tables["approved_networks"] = []
        _, bundle = source.collect()
        self.assertEqual(bundle["network_checks"][0]["status"], "no_exact_match")
        self.assertEqual(bundle["collection_status"], "collected")
        self.assertNotIn("assessment", bundle)

    def test_inactive_network_status_is_preserved(self) -> None:
        source = MockZetaris()
        source.tables["approved_networks"][0]["status"] = "INACTIVE"
        _, bundle = source.collect()
        self.assertEqual(bundle["network_checks"][0]["status"], "matched")
        self.assertEqual(bundle["records"]["approved_networks"][0]["record"]["status"], "INACTIVE")

    def test_timezone_is_not_assumed_for_naive_alert_timestamps(self) -> None:
        source = MockZetaris()
        source.tables["security_alerts"][0]["timestamp"] = "2026-09-10T10:12:00"
        _, bundle = source.collect()
        self.assertIsNone(bundle["scope"]["history_window"])
        self.assertTrue(any(issue["code"] == "history_window_unavailable" for issue in bundle["issues"]))
        self.assertFalse(any(query.kind == "history" for query in source.calls))

    def test_verified_sql_utc_projection_resolves_naive_api_timestamp_without_changing_it(self) -> None:
        source = MockZetaris()
        original = "2026-09-10 10:12:00.0"
        source.tables["security_alerts"][0]["timestamp"] = original
        source.clock_metadata = "GMT"
        _, bundle = source.collect()
        self.assertEqual(bundle["scope"]["history_window"]["start"], "2026-09-09T10:12:00Z")
        alert = bundle["records"]["security_alerts"][0]
        self.assertEqual(alert["record"]["timestamp"], original)
        self.assertEqual(alert["timestamp_interpretation"]["basis"], "verified_same_query_utc_projection")
        self.assertNotIn("_argus_session_timezone", alert["record"])
        self.assertTrue(any(query.kind == "history" for query in source.calls))

    def test_non_utc_sql_session_does_not_authorise_a_naive_timestamp_assumption(self) -> None:
        source = MockZetaris()
        source.tables["security_alerts"][0]["timestamp"] = "2026-09-10 10:12:00.0"
        source.clock_metadata = "Europe/London"
        _, bundle = source.collect()
        self.assertIsNone(bundle["scope"]["history_window"])
        self.assertTrue(any(issue["code"] == "history_window_unavailable" for issue in bundle["issues"]))

    def test_conflicting_timestamp_projection_is_explicit(self) -> None:
        source = MockZetaris()
        source.tables["security_alerts"][0]["timestamp"] = "2026-09-10T10:13:00Z"
        source.clock_metadata = "GMT"
        _, bundle = source.collect()
        self.assertTrue(any(issue["code"] == "conflicting_timestamp_projections" for issue in bundle["issues"]))
        self.assertEqual(bundle["collection_status"], "partial")

    def test_unknown_response_metadata_is_not_silently_treated_as_reviewed(self) -> None:
        snapshot, _ = MockZetaris().collect()
        snapshot.observations[0].result.raw_response["warnings"] = ["Review source completeness"]
        bundle = build_bundle(snapshot)
        self.assertTrue(any(issue["code"] == "unreviewed_response_metadata" for issue in bundle["issues"]))
        self.assertEqual(build_trace(snapshot)["queries"][0]["response_metadata"]["warnings"], ["Review source completeness"])

    def test_malformed_reference_encoding_remains_visible(self) -> None:
        source = MockZetaris()
        source.tables["security_alerts"][0]["related_event_ids"] = '["BROKEN"'
        _, bundle = source.collect()
        self.assertTrue(any(issue["code"] == "malformed_reference_list" for issue in bundle["issues"]))
        self.assertEqual(bundle["records"]["security_alerts"][0]["record"]["related_event_ids"], '["BROKEN"')

    def test_query_and_reference_budgets_are_visible(self) -> None:
        source = MockZetaris()
        _, bundle = source.collect(options=CollectionOptions(max_queries=1))
        self.assertEqual(len(source.calls), 1)
        self.assertTrue(any(query["status"] == "budget_exhausted" for query in bundle["queries"]))
        source = MockZetaris()
        _, bundle = source.collect(options=CollectionOptions(max_references=1))
        self.assertEqual(bundle["references"][1]["status"], "not_collected_due_to_limit")

    def test_bundle_is_deterministic_despite_wire_uuids_timings_and_row_order(self) -> None:
        source = MockZetaris()
        first, first_bundle = source.collect()
        source.reverse = True
        second, second_bundle = source.collect()
        self.assertEqual(serialise(first_bundle), serialise(second_bundle))
        self.assertNotEqual(serialise(build_trace(first)), serialise(build_trace(second)))
        self.assertEqual(serialise(first_bundle), serialise(build_bundle(first)))

    def test_native_large_integer_and_decimal_precision_preserved(self) -> None:
        source = MockZetaris()
        source.tables["resource_access_logs"][0]["bytes_transferred"] = 2400000000
        _, bundle = source.collect()
        self.assertIs(type(bundle["records"]["resource_access_logs"][0]["record"]["bytes_transferred"]), int)
        source.tables["resource_access_logs"][0]["bytes_transferred"] = Decimal("2400000000.000000000000001")
        _, bundle = source.collect()
        self.assertEqual(bundle["records"]["resource_access_logs"][0]["record"]["bytes_transferred"],
                         {"$decimal": "2400000000.000000000000001"})

    def test_workflow_does_not_read_local_csv_or_evaluation_files(self) -> None:
        with patch("builtins.open", side_effect=AssertionError("No local evidence reads")), \
             patch.object(Path, "open", side_effect=AssertionError("No local evidence reads")):
            _, bundle = MockZetaris().collect()
        self.assertEqual(bundle["collection_status"], "collected")

    def test_output_guard_protects_dataset_and_evaluation_paths(self) -> None:
        from scripts.zetaris_client import ROOT
        for path in [ROOT / "evaluation" / "any.json", ROOT / "data" / "any.json", ROOT / ".env.local"]:
            with self.assertRaises(ValueError):
                output_path(path)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.json"
            self.assertEqual(output_path(path), path.resolve())


if __name__ == "__main__":
    unittest.main()
