"""Diagnostic-only regressions; no runtime, credentials, cache or inference calls."""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.evidence_collector import serialise
from scripts.reason_evidence import ReasoningFailure, main, reason_bundle
from scripts.reasoning_assessment import ASSESSMENT_SCHEMA, validate_structure
from scripts.reasoning_diagnostics import MAX_SCHEMA_ERRORS, MAX_SYNTAX_SCAN_CHARS, schema_errors
from scripts.reasoning_providers import InferenceResponse, MockProvider
from scripts.reasoning_view import ReasoningError, build_reasoning_view
from tests.test_reasoning import fixture, valid_assessment


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.bundle = fixture()
        self.valid = valid_assessment(build_reasoning_view(self.bundle))

    def failure(self, value, metadata=None, *, raw=False):
        # These fixtures also exercise the archived v2 validator. For the new
        # inference pipeline, remove only its four ARGUS-owned metadata fields.
        if not raw:
            value = {k: v for k, v in value.items() if k not in {"schema_version", "alert_id", "collection_status", "evidence_complete"}}
        provider = Mock()
        provider.generate.return_value = InferenceResponse(value if raw else json.dumps(value), "mock", "test", True, metadata or {})
        with self.assertRaises(ReasoningFailure) as caught:
            reason_bundle(self.bundle, provider)
        provider.generate.assert_called_once()
        return caught.exception.report

    def test_original_first_failure_true_complete_is_precise_and_safe(self):
        self.valid["evidence_complete"] = True
        with self.assertRaises(ReasoningError):
            validate_structure(self.valid)
        self.assertEqual(schema_errors(self.valid)["errors"], [{"stage": "schema", "check": "ENUM", "path": "assessment.evidence_complete",
                                                        "expected": [False], "actual_type": "bool", "actual_value": True}])
        # New inference never asks the model to reproduce this deterministic flag.
        _, result = reason_bundle(self.bundle, MockProvider())
        self.assertFalse(result["assessment"]["evidence_complete"])
        self.assertTrue(all(s == "passed" for s in result["validation_stages"].values()))

    def test_original_second_failure_unclosed_unknowns_at_eof(self):
        raw = '{"unknowns":[{"description":"bounded","gap_ids":[]}'
        report = self.failure(raw, {"finish_reason": "stop", "usage": {"completion_tokens": 4391}}, raw=True)
        detail = report["details"]
        self.assertEqual(detail["parser_category"], "EXPECTED_COMMA")
        self.assertEqual((detail["line"], detail["column"]), (1, len(raw) + 1))
        self.assertFalse(detail["response_empty"])
        self.assertTrue(detail["appears_truncated"])
        self.assertEqual(detail["truncation_basis"], ["unclosed_container_at_end"])
        self.assertTrue(detail["truncation_is_heuristic"])
        self.assertEqual(report["inference"]["finish_reason"], "stop")
        self.assertEqual(report["inference"]["completion_tokens"], 4391)

    def test_multiple_schema_errors_include_nested_paths_and_types(self):
        self.valid["confidence"] = 0.9
        self.valid["key_findings"][0]["evidence"] = "secret-model-text"
        self.valid["evidence_complete"] = True
        errors = self.failure(self.valid)["details"]["errors"]
        by_path = {e["path"]: e for e in errors}
        self.assertEqual(by_path["assessment.confidence"]["check"], "TYPE")
        self.assertEqual(by_path["assessment.confidence"]["expected"], "string")
        self.assertEqual(by_path["assessment.confidence"]["actual_type"], "Decimal")
        self.assertEqual(by_path["assessment.key_findings[0].evidence"]["expected"], "array")
        self.assertEqual(by_path["assessment.key_findings[0].evidence"]["actual_type"], "str")
        self.assertNotIn("secret-model-text", serialise(errors))

    def test_missing_fields_have_precise_paths_and_validation_still_rejects(self):
        del self.valid["confidence"]
        del self.valid["key_findings"][0]["kind"]
        with self.assertRaises(ReasoningError): validate_structure(self.valid)
        errors = self.failure(self.valid)["details"]["errors"]
        missing = {e["path"]: e for e in errors if e["check"] == "MISSING_FIELD"}
        self.assertIn("assessment.confidence", missing)
        self.assertIn("assessment.key_findings[0].kind", missing)
        self.assertEqual(missing["assessment.confidence"]["actual_type"], "missing")
        self.assertEqual(missing["assessment.confidence"]["expected"]["enum"], ["LOW", "MEDIUM", "HIGH"])

    def test_incorrect_enum_omits_untrusted_value(self):
        self.valid["classification"] = "secret-auth-token-reflected-by-model"
        report = self.failure(self.valid)
        error = report["details"]["errors"][0]
        self.assertEqual(error["check"], "ENUM")
        self.assertEqual(error["expected"], ASSESSMENT_SCHEMA["properties"]["classification"]["enum"])
        self.assertEqual(error["actual_type"], "str")
        self.assertNotIn("actual_value", error)
        self.assertNotIn("secret-auth-token-reflected-by-model", serialise(report))

    def test_known_public_enum_value_can_be_shown_in_wrong_field(self):
        self.valid["confidence"] = "INCONCLUSIVE"
        error = self.failure(self.valid)["details"]["errors"][0]
        self.assertEqual(error["actual_value"], "INCONCLUSIVE")

    def test_unexpected_property_names_are_redacted(self):
        self.valid["secret-used-as-property-name"] = "sensitive-content"
        report = self.failure(self.valid)
        error = next(e for e in report["details"]["errors"] if e["check"] == "UNEXPECTED_FIELDS")
        self.assertEqual(error["path"], "assessment")
        self.assertEqual(error["unexpected_field_count"], 1)
        self.assertNotIn("secret-used-as-property-name", serialise(report))
        self.assertNotIn("sensitive-content", serialise(report))

    def test_error_list_is_deterministic_and_bounded(self):
        self.valid["key_findings"] = [{} for _ in range(100)]
        before = copy.deepcopy(self.valid)
        first = schema_errors(self.valid)
        self.assertEqual(first, schema_errors(self.valid))
        self.assertEqual(len(first["errors"]), MAX_SCHEMA_ERRORS)
        self.assertTrue(first["errors_truncated"])
        self.assertEqual(self.valid, before)

    def test_schema_and_valid_assessment_are_not_changed_by_diagnostics(self):
        original_schema, original_value = copy.deepcopy(ASSESSMENT_SCHEMA), copy.deepcopy(self.valid)
        self.assertEqual(schema_errors(self.valid)["errors"], [])
        validate_structure(self.valid)
        self.assertEqual(ASSESSMENT_SCHEMA, original_schema)
        self.assertEqual(self.valid, original_value)

    def test_empty_whitespace_and_nontext_responses(self):
        for raw, empty in (("", True), (" \r\n ", True), (None, None)):
            with self.subTest(value_type=type(raw)):
                report = self.failure(raw, raw=True)
                self.assertEqual(report["details"]["response_empty"], empty)
                self.assertIsNone(report["details"]["appears_truncated"])
                self.assertIsNone(report["inference"]["finish_reason"])
                self.assertIsNone(report["inference"]["completion_tokens"])

    def test_balanced_invalid_json_does_not_prove_truncation(self):
        report = self.failure('{"a": nope}', raw=True)
        self.assertEqual(report["details"]["parser_category"], "EXPECTED_VALUE")
        self.assertIsNone(report["details"]["appears_truncated"])

    def test_unterminated_string_indicates_possible_truncation(self):
        report = self.failure('{"a":"unfinished', raw=True)
        self.assertEqual(report["details"]["parser_category"], "UNTERMINATED_STRING")
        self.assertIn("unclosed_string_at_end", report["details"]["truncation_basis"])

    def test_reported_output_limit_is_distinct_from_syntax_heuristic(self):
        report = self.failure("not json", {"finish_reason": "length", "usage": {"completion_tokens": 12000}}, raw=True)
        self.assertTrue(report["details"]["appears_truncated"])
        self.assertFalse(report["details"]["truncation_is_heuristic"])
        self.assertIn("reported_output_limit", report["details"]["truncation_basis"])

    def test_quoted_delimiters_and_escapes_do_not_create_false_unclosed_containers(self):
        # Build the string with the normal JSON encoder to ensure valid escaping.
        raw = '{"a":' + json.dumps('braces { [ and escaped quote "') + ',"a":2}'
        report = self.failure(raw, raw=True)
        self.assertEqual(report["details"]["check"], "DUPLICATE_PROPERTIES")
        self.assertIsNone(report["details"]["appears_truncated"])

    def test_long_response_skips_bounded_syntax_scan(self):
        report = self.failure("{" + " " * MAX_SYNTAX_SCAN_CHARS, raw=True)
        self.assertTrue(report["details"]["syntax_scan_limited"])
        self.assertIsNone(report["details"]["appears_truncated"])

    def test_untrusted_termination_metadata_is_redacted(self):
        report = self.failure("bad", {"finish_reason": "secret-finish-text", "usage": {"completion_tokens": "secret-token-text"},
                                      "raw_response": "never copy this"}, raw=True)
        self.assertEqual(report["inference"]["finish_reason_status"], "redacted_invalid")
        self.assertEqual(report["inference"]["completion_tokens_status"], "redacted_invalid")
        for forbidden in ("secret-finish-text", "secret-token-text", "never copy this"):
            self.assertNotIn(forbidden, serialise(report))

    def test_openai_usage_alias_is_captured_without_provider_changes(self):
        report = self.failure("bad", {"usage": {"output_tokens": 42}}, raw=True)
        self.assertEqual(report["inference"]["completion_tokens"], 42)

    def test_failure_artifact_keeps_no_raw_response_or_secret(self):
        self.valid["confidence"] = "secret-model-value"
        reply = InferenceResponse(json.dumps(self.valid), "mock", "test", True, {"finish_reason": "stop", "usage": {"completion_tokens": 9}})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text(serialise(self.bundle), encoding="utf-8")
            with patch("scripts.reason_evidence.MockProvider.generate", return_value=reply) as generate, patch("sys.stdout", new=io.StringIO()) as console:
                self.assertEqual(main([str(source), "--provider", "mock", "--output-dir", str(root / "out")]), 1)
            generate.assert_called_once()
            files = list((root / "out").glob("*.json"))
            self.assertEqual(len(files), 1)
            text = files[0].read_text(encoding="utf-8")
            report = json.loads(text)
            self.assertNotIn("secret-model-value", text + console.getvalue())
            self.assertNotIn("response_text", report)
            self.assertEqual(report["inference"]["finish_reason"], "stop")
            self.assertEqual(report["inference"]["completion_tokens"], 9)
            self.assertFalse(report["validated_assessment_written"])


if __name__ == "__main__":
    unittest.main()
