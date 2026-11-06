"""Relationship/claim regressions using generic fixtures and operational data only."""

from __future__ import annotations

import copy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.evidence_collector import serialise
from scripts.evidence_relationships import AUTH
from scripts.evidence_queries import SCHEMAS
from scripts.reason_evidence import ReasoningFailure, main, reason_bundle
from scripts.reasoning_assessment import FactualConsistencyError, validate_assessment
from scripts.reasoning_prompts import SYSTEM_PROMPT
from scripts.reasoning_providers import InferenceResponse, MockProvider
from scripts.reasoning_view import ReasoningError, build_reasoning_view, digest
from scripts.zetaris_client import ROOT
from tests.test_evidence_collection import MockZetaris
from tests.test_reasoning import citation, envelope, fixture, model_content, valid_assessment


def generic_bundle() -> dict:
    bundle = fixture()
    auths = []
    for source_id, ip, time, sid in (("AUTH_A", "192.0.2.51", "10:00:00", "SESSION_A"),
                                    ("AUTH_B", "203.0.113.15", "10:06:00", "SESSION_B")):
        row = envelope("authentication_logs", source_id, roles=["referenced"], user_id="USER_A", source_ip=ip,
                       device_id="REPORTED_DEVICE", timestamp=f"2026-09-10T{time}Z", session_id=sid,
                       reported_location="London%2C UK", outcome="SUCCESS")
        row["timestamp_interpretation"] = {"basis": "explicit_source_offset", "utc": row["record"]["timestamp"]}
        auths.append(row)
    bundle["records"]["authentication_logs"] = auths
    bundle["references"] = [{"position": i, "event_id": row["source"]["id"], "duplicate": False,
                              "status": "resolved", "records": [citation(row)]} for i, row in enumerate(auths)]
    bundle["records"]["approved_networks"] = [
        envelope("approved_networks", "NET_NEAR", ip_address="192.0.2.10", network_type="CORPORATE_VPN", status="ACTIVE"),
        envelope("approved_networks", "NET_B", ip_address="203.0.113.15", network_type="CORPORATE_VPN", status="ACTIVE")]
    bundle["network_checks"] = [{"source_ip": "192.0.2.51", "status": "no_exact_match", "network_ids": []},
                                {"source_ip": "203.0.113.15", "status": "matched", "network_ids": ["NET_B"]}]
    access = envelope("resource_access_logs", "ACCESS_A", user_id="USER_A", source_ip="192.0.2.51",
                      session_id="SESSION_A", resource_id="RESOURCE_A", timestamp="2026-09-10T10:02:00Z")
    access["timestamp_interpretation"] = {"basis": "explicit_source_offset", "utc": access["record"]["timestamp"]}
    bundle["records"]["resource_access_logs"] = [access]
    return bundle


def get_fact(view: dict, kind: str, source_id: str | None = None) -> dict:
    return next(f for f in view["relationships"]["facts"] if f["kind"] == kind and
                (source_id is None or any(s["id"] == source_id for s in f["evidence"])))


class RelationshipTests(unittest.TestCase):
    def test_exact_network_links_without_prefix_approval(self):
        view = build_reasoning_view(generic_bundle())
        negative = get_fact(view, "NETWORK_MATCH_STATUS", "AUTH_A")
        positive = get_fact(view, "NETWORK_MATCH_STATUS", "AUTH_B")
        self.assertEqual(negative["value"], "NO_EXACT_MATCH")
        self.assertFalse(any(s["id"] == "NET_NEAR" for s in negative["evidence"]))
        self.assertEqual(positive["value"], "MATCHED")
        self.assertIn("NET_B", [s["id"] for s in positive["evidence"]])

    def test_incomplete_unavailable_and_missing_checks_are_not_negatives(self):
        for status in ("unavailable", "potentially_truncated", "malformed", None):
            bundle = generic_bundle()
            if status is None: bundle["network_checks"].pop(0)
            else: bundle["network_checks"][0]["status"] = status
            view = build_reasoning_view(bundle)
            self.assertEqual(get_fact(view, "NETWORK_MATCH_STATUS", "AUTH_A")["value"], "NOT_CHECKED" if status is None else "INCOMPLETE")
            self.assertTrue(view["gaps"])

    def test_inconsistent_collector_link_stays_visible(self):
        bundle = generic_bundle()
        bundle["network_checks"][0].update(status="matched", network_ids=["NET_NEAR"])
        view = build_reasoning_view(bundle)
        self.assertEqual(get_fact(view, "NETWORK_MATCH_STATUS", "AUTH_A")["value"], "CONFLICTING")
        self.assertEqual(view["network_checks"], bundle["network_checks"])
        self.assertIn("network_relationship_conflicting", {i["code"] for i in view["relationships"]["issues"]})

    def test_shared_device_and_exact_elapsed_time(self):
        view = build_reasoning_view(generic_bundle())
        self.assertEqual(get_fact(view, "SHARED_REPORTED_DEVICE_ID")["value"], "REPORTED_DEVICE")
        self.assertEqual(get_fact(view, "AUTHENTICATION_DELTA_MICROSECONDS")["value"], 360_000_000)
        pair = view["relationships"]["authentication_pairs"][0]
        self.assertEqual(pair["ordering"], "BEFORE")
        self.assertEqual(pair["device_status"], "SHARED")

    def test_unknown_time_does_not_become_zero_or_assumed_utc(self):
        bundle = generic_bundle()
        del bundle["records"]["authentication_logs"][1]["timestamp_interpretation"]
        view = build_reasoning_view(bundle)
        self.assertIsNone(get_fact(view, "AUTHENTICATION_UTC", "AUTH_B")["value"])
        self.assertIsNone(get_fact(view, "AUTHENTICATION_DELTA_MICROSECONDS")["value"])

    def test_conflicting_timestamp_evidence_vetoes_utc(self):
        bundle = generic_bundle()
        bundle["issues"] = [{"code": "conflicting_timestamp_projections", "source_id": "AUTH_B"}]
        self.assertIsNone(get_fact(build_reasoning_view(bundle), "AUTHENTICATION_DELTA_MICROSECONDS")["value"])

    def test_microsecond_order_and_negative_delta_are_exact(self):
        bundle = generic_bundle()
        for row, time in zip(bundle["records"]["authentication_logs"], ("10:00:00.000001", "10:00:00")):
            row["record"]["timestamp"] = f"2026-09-10T{time}Z"
            row["timestamp_interpretation"]["utc"] = row["record"]["timestamp"]
            row["variant_id"] = digest(row["record"])
        bundle["references"] = [{"status": "resolved", "records": [citation(r)]} for r in bundle["records"]["authentication_logs"]]
        view = build_reasoning_view(bundle)
        self.assertEqual(get_fact(view, "AUTHENTICATION_DELTA_MICROSECONDS")["value"], -1)
        self.assertEqual(view["relationships"]["authentication_timeline"][0]["source"]["id"], "AUTH_B")

    def test_missing_and_different_devices_are_distinct(self):
        for value, status in ((None, "UNAVAILABLE"), ("OTHER", "DIFFERENT")):
            bundle = generic_bundle()
            row = bundle["records"]["authentication_logs"][1]
            row["record"]["device_id"] = value
            row["variant_id"] = digest(row["record"])
            bundle["references"][1]["records"] = [citation(row)]
            view = build_reasoning_view(bundle)
            self.assertIsNone(get_fact(view, "SHARED_REPORTED_DEVICE_ID")["value"])
            self.assertEqual(view["relationships"]["authentication_pairs"][0]["device_status"], status)

    def test_session_resource_associations_and_missing_context(self):
        bundle = generic_bundle()
        view = build_reasoning_view(bundle)
        link = view["relationships"]["session_access_links"][0]
        self.assertEqual(link["status"], "MATCHED")
        self.assertTrue(link["comparisons"][0]["access_after_authentication"])
        self.assertTrue(link["comparisons"][0]["user_matches"])
        self.assertTrue(link["comparisons"][0]["source_ip_matches"])
        bundle["records"]["resources"] = []
        view = build_reasoning_view(bundle)
        self.assertEqual(view["relationships"]["session_access_links"][0]["resource_context_status"], "NOT_IN_BUNDLE")

    def test_conflicting_variants_not_dropped_or_conflated(self):
        bundle = generic_bundle()
        other = copy.deepcopy(bundle["records"]["authentication_logs"][0])
        other["record"]["outcome"] = "FAILURE"
        other["variant_id"] = digest(other["record"])
        bundle["records"]["authentication_logs"].append(other)
        view = build_reasoning_view(bundle)
        outcomes = [f["value"] for f in view["relationships"]["facts"] if f["kind"] == "AUTHENTICATION_OUTCOME" and f["evidence"][0]["id"] == "AUTH_A"]
        self.assertEqual(set(outcomes), {"SUCCESS", "FAILURE"})
        self.assertIn("conflicting_variants", {g["kind"] for g in view["gaps"]})

    def test_relationships_are_deterministic_without_mutating_input(self):
        bundle = generic_bundle()
        before = copy.deepcopy(bundle)
        self.assertEqual(serialise(build_reasoning_view(bundle)), serialise(build_reasoning_view(bundle)))
        self.assertEqual(bundle, before)


class ClaimTests(unittest.TestCase):
    def setUp(self):
        self.view = build_reasoning_view(generic_bundle())
        self.assessment = valid_assessment(self.view)

    def test_valid_required_claims_pass(self):
        validate_assessment(self.assessment, self.view)
        self.assertEqual({c["fact_id"] for c in self.assessment["factual_claims"]}, set(self.view["relationships"]["required_fact_ids"]))

    def test_contradictory_values_for_every_supported_kind_fail(self):
        for kind in {f["kind"] for f in self.view["relationships"]["facts"]}:
            value = copy.deepcopy(self.assessment)
            claim = next(c for c in value["factual_claims"] if c["kind"] == kind)
            claim["value"] = "wrong value"
            with self.subTest(kind=kind), self.assertRaisesRegex(FactualConsistencyError, "FACT_VALUE_MISMATCH"):
                validate_assessment(value, self.view)

    def test_unrelated_network_citation_rejected(self):
        claim = next(c for c in self.assessment["factual_claims"] if c["kind"] == "NETWORK_MATCH_STATUS" and c["value"] == "NO_EXACT_MATCH")
        wrong = next(r["source"] for r in self.view["records"] if r["source"]["id"] == "NET_NEAR")
        claim["evidence"].append(wrong)
        with self.assertRaisesRegex(FactualConsistencyError, "FACT_EVIDENCE_MISMATCH"):
            validate_assessment(self.assessment, self.view)

    def test_missing_duplicate_unknown_and_wrong_kind_claims_fail(self):
        for mutation, check in (("missing", "REQUIRED_FACT_CLAIM_MISSING"), ("duplicate", "DUPLICATE_FACT_CLAIM"),
                                ("unknown", "UNKNOWN_FACT"), ("kind", "FACT_KIND_MISMATCH")):
            value = copy.deepcopy(self.assessment)
            if mutation == "missing": value["factual_claims"].pop()
            elif mutation == "duplicate": value["factual_claims"].append(copy.deepcopy(value["factual_claims"][0]))
            elif mutation == "unknown": value["factual_claims"][0]["fact_id"] = "untrusted-secret-like-text"
            else: value["factual_claims"][0]["kind"] = "SHARED_REPORTED_DEVICE_ID" if value["factual_claims"][0]["kind"] != "SHARED_REPORTED_DEVICE_ID" else "NETWORK_MATCH_STATUS"
            with self.subTest(mutation=mutation), self.assertRaisesRegex(FactualConsistencyError, check):
                validate_assessment(value, self.view)

    def test_integer_precision_and_boolean_coercion_not_allowed(self):
        claim = next(c for c in self.assessment["factual_claims"] if c["kind"] == "AUTHENTICATION_DELTA_MICROSECONDS")
        claim["value"] = True
        with self.assertRaises(ReasoningError): validate_assessment(self.assessment, self.view)

    def test_edited_view_fact_catalog_does_not_authorise_wrong_claim(self):
        claim = self.assessment["factual_claims"][0]
        claim["value"] = "tampered"
        fact = next(f for f in self.view["relationships"]["facts"] if f["fact_id"] == claim["fact_id"])
        fact["value"] = "tampered"
        with self.assertRaises(FactualConsistencyError): validate_assessment(self.assessment, self.view)

    def test_legacy_validation_is_explicit_and_new_pipeline_rejects_it(self):
        value = copy.deepcopy(self.assessment)
        value["schema_version"] = "1.0"
        del value["assessed_hypothesis"], value["factual_claims"]
        validate_assessment(value, self.view, allow_legacy=True)
        with self.assertRaises(ReasoningError): validate_assessment(value, self.view)

    def test_diagnostics_do_not_echo_untrusted_claim_values(self):
        self.assessment["factual_claims"][0]["value"] = "secret-reflected-by-model"
        with self.assertRaises(FactualConsistencyError) as caught: validate_assessment(self.assessment, self.view)
        self.assertNotIn("secret-reflected-by-model", str(caught.exception))
        self.assertNotIn("secret-reflected-by-model", serialise(caught.exception.details))

    def test_failed_assessment_creates_only_safe_failure_report(self):
        self.assessment["factual_claims"][0]["value"] = "secret-reflected-by-model"
        reply = InferenceResponse(json.dumps(model_content(self.assessment)), "mock", "unit-test", True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bundle.json"
            original = serialise(generic_bundle())
            source.write_text(original, encoding="utf-8")
            with patch("scripts.reason_evidence.MockProvider.generate", return_value=reply) as generate, patch("sys.stdout", new=io.StringIO()) as output:
                self.assertEqual(main([str(source), "--provider", "mock", "--output-dir", str(root / "out")]), 1)
            generate.assert_called_once()
            files = list((root / "out").glob("*.json"))
            self.assertEqual(len(files), 1)
            report = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertFalse(report["validated_assessment_written"])
            self.assertEqual(report["validation_stages"]["factual_consistency"], "failed")
            self.assertEqual(report["validation_stages"]["schema"], "passed")
            self.assertNotIn("secret-reflected-by-model", serialise(report) + output.getvalue())
            self.assertEqual(source.read_text(encoding="utf-8"), original)

    def test_parse_schema_and_citation_failures_report_distinct_stages(self):
        for stage, payload in (("json_parsing", "not json"), ("schema", "{}"), ("citations_and_gaps", "citation")):
            value = copy.deepcopy(self.assessment)
            if payload == "citation":
                value["supporting_evidence"][0]["id"] = "MISSING"
                payload = json.dumps(model_content(value))
            provider = Mock()
            provider.generate.return_value = InferenceResponse(payload, "mock", "test", True)
            with self.subTest(stage=stage), self.assertRaises(ReasoningFailure) as caught:
                reason_bundle(generic_bundle(), provider)
            self.assertEqual(caught.exception.report["validation_stages"][stage], "failed")

    def test_schema_failure_has_safe_field_path_without_value(self):
        value = copy.deepcopy(self.assessment)
        value["factual_claims"][0]["kind"] = "secret-like-invalid-enum"
        provider = Mock()
        provider.generate.return_value = InferenceResponse(json.dumps(model_content(value)), "mock", "test", True)
        with self.assertRaises(ReasoningFailure) as caught: reason_bundle(generic_bundle(), provider)
        report = caught.exception.report
        self.assertEqual(report["details"]["path"], "assessment.factual_claims[0].kind")
        self.assertEqual(report["details"]["check"], "ENUM")
        self.assertNotIn("secret-like-invalid-enum", serialise(report))

    def test_malformed_unicode_still_produces_safe_failure_report(self):
        provider = Mock()
        provider.generate.return_value = InferenceResponse("\ud800", "mock", "test", True)
        with self.assertRaises(ReasoningFailure) as caught: reason_bundle(generic_bundle(), provider)
        self.assertEqual(caught.exception.report["validation_stages"]["json_parsing"], "failed")
        self.assertEqual(len(caught.exception.report["response_sha256"]), 64)

    def test_json_failures_have_safe_codes_and_syntax_positions(self):
        for payload, check in (('{"secret-key":1,"secret-key":2}', "DUPLICATE_PROPERTIES"),
                               ('{"secret-key":NaN}', "NON_JSON_CONSTANT"), ('{"secret-key":}', "SYNTAX")):
            provider = Mock()
            provider.generate.return_value = InferenceResponse(payload, "mock", "test", True)
            with self.subTest(check=check), self.assertRaises(ReasoningFailure) as caught:
                reason_bundle(generic_bundle(), provider)
            report = caught.exception.report
            self.assertEqual(report["details"]["check"], check)
            self.assertNotIn("secret-key", serialise(report))
            if check == "SYNTAX": self.assertIsInstance(report["details"]["column"], int)

    def test_prompt_mentions_hypothesis_and_relationship_limits(self):
        for phrase in ("assessed_hypothesis", "NO_EXACT_MATCH", "reported-device", "not independent confirmation", "selected provisional classification"):
            self.assertIn(phrase, SYSTEM_PROMPT)


class OperationalRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Only test fixtures read compatibility CSVs; production reasoning does
        # not. Exercise the existing collector with mocked Zetaris transport.
        tables = {}
        for table in SCHEMAS:
            with (ROOT / "data" / "zetaris" / f"{table}.csv").open(encoding="utf-8", newline="") as handle:
                tables[table] = list(csv.DictReader(handle))
        _, bundle = MockZetaris(tables).collect("ALT0018")
        cls.view = build_reasoning_view(bundle)

    def test_alt0018_exact_network_regression(self):
        negative = get_fact(self.view, "NETWORK_MATCH_STATUS", "AUTH000962")
        self.assertEqual(negative["details"]["source_ip"], "198.51.100.51")
        self.assertEqual(negative["value"], "NO_EXACT_MATCH")
        self.assertNotIn("N003", [s["id"] for s in negative["evidence"]])
        positive = get_fact(self.view, "NETWORK_MATCH_STATUS", "AUTH000963")
        self.assertEqual(positive["details"]["source_ip"], "203.0.113.10")
        self.assertEqual(positive["value"], "MATCHED")
        self.assertIn("N005", [s["id"] for s in positive["evidence"]])

    def test_alt0018_shared_device_and_six_minutes(self):
        fact = next(f for f in self.view["relationships"]["facts"] if f["kind"] == "SHARED_REPORTED_DEVICE_ID" and
                    {s["id"] for s in f["evidence"]} == {"AUTH000962", "AUTH000963"})
        self.assertEqual(fact["value"], "DEV-U001-01")
        delta = next(f for f in self.view["relationships"]["facts"] if f["kind"] == "AUTHENTICATION_DELTA_MICROSECONDS" and f["evidence"] == fact["evidence"])
        self.assertEqual(delta["value"], 360_000_000)
        self.assertTrue(delta["required"])

    def test_alt0018_incorrect_network_claim_is_rejected(self):
        value = valid_assessment(self.view)
        fact = get_fact(self.view, "NETWORK_MATCH_STATUS", "AUTH000962")
        claim = next(c for c in value["factual_claims"] if c["fact_id"] == fact["fact_id"])
        claim["value"] = "MATCHED"
        with self.assertRaises(FactualConsistencyError): validate_assessment(value, self.view)


if __name__ == "__main__":
    unittest.main()
