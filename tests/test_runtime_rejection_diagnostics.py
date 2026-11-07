"""Diagnostic-only normalization; no inference or acceptance-rule changes."""
import copy
import io
import json
import unittest
from urllib.error import HTTPError

from scripts.evidence_collector import serialise
from scripts.reason_evidence import ReasoningFailure, reason_bundle
from scripts.reasoning_assessment import MODEL_OUTPUT_SCHEMA
from scripts.reasoning_diagnostics import MAX_RUNTIME_ERRORS, runtime_schema_diagnostics
from scripts.reasoning_providers import MeterlessProvider
from tests.test_meterless import TOKEN, config
from tests.test_reasoning_contract import completed_bundle, serve


class RuntimeDiagnosticTests(unittest.TestCase):
    def normalize(self, path, message):
        return runtime_schema_diagnostics({"errors": [{"path": path, "message": message}]})["errors"][0]

    def test_enum_path_constraint_and_expected_literals(self):
        error = self.normalize("$.confidence", "value is not one of the 3 allowed values")
        self.assertEqual(error["path"], "$.confidence")
        self.assertEqual(error["check"], "ENUM")
        self.assertEqual(error["keyword"], "enum")
        self.assertEqual(error["expected_from_argus_schema"]["enum"], ["LOW", "MEDIUM", "HIGH"])
        self.assertIsNone(error["actual_type"])
        self.assertEqual(error["actual_value_status"], "unavailable")

    def test_nested_array_type_path_and_actual_type(self):
        error = self.normalize("$.key_findings[2].evidence", "expected array, got string")
        self.assertEqual(error["path"], "$.key_findings[2].evidence")
        self.assertEqual(error["actual_type"], "string")
        self.assertEqual(error["expected_from_argus_schema"], {"type": "array"})
        self.assertEqual(error["keyword"], "type")

    def test_missing_and_union_constraints(self):
        missing = self.normalize("$.key_findings", "required property is missing")
        self.assertEqual(missing["check"], "MISSING_FIELD")
        error = self.normalize("$.factual_claims[0].value", "matches none of the anyOf alternatives")
        self.assertEqual(error["expected_from_argus_schema"]["types"], ["string", "integer", "null"])
        self.assertEqual(error["keyword"], "anyOf")

    def test_unknown_property_names_and_messages_are_not_reflected(self):
        error = self.normalize("$.key_findings[0].secret-credential", "property is not allowed by the schema")
        self.assertEqual(error["path"], "$.key_findings[0]")
        self.assertTrue(error["path_redacted"])
        self.assertEqual(error["expected_from_argus_schema"], {"additionalProperties": False})
        unknown = self.normalize("$.secret-credential", "private secret-credential text")
        self.assertEqual(unknown["check"], "RUNTIME_SCHEMA_ERROR")
        self.assertEqual(unknown["expected_from_argus_schema"], {})
        self.assertNotIn("secret-credential", serialise([error, unknown]))

    def test_invalid_long_or_large_index_paths_are_redacted(self):
        for path in (None, "not-jsonpath", "$" + "x" * 1024, "$.key_findings[1000].kind", "$.confidence.secret"):
            with self.subTest(path_type=type(path)):
                error = self.normalize(path, "required property is missing")
                self.assertTrue(error["path_redacted"])
                self.assertNotIn("secret", error["path"])

    def test_errors_are_bounded_deterministic_and_do_not_mutate_input(self):
        raw = {"errors": [{"path": "$.confidence", "message": "required property is missing"}] * 20}
        before = copy.deepcopy(raw)
        first = runtime_schema_diagnostics(raw)
        self.assertEqual(first, runtime_schema_diagnostics(raw))
        self.assertEqual(raw, before)
        self.assertEqual(len(first["errors"]), MAX_RUNTIME_ERRORS)
        self.assertTrue(first["errors_truncated"])
        self.assertEqual(first["reported_error_count"], 20)

    def test_unavailable_details_are_not_invented(self):
        for raw in (None, {}, {"errors": None}, {"errors": "secret"}):
            self.assertEqual(runtime_schema_diagnostics(raw), {"availability": "unavailable", "errors": []})
        available = runtime_schema_diagnostics({"errors": []})
        self.assertEqual(available["availability"], "available")
        self.assertEqual(available["errors"], [])

    def test_wrong_error_types_and_extra_values_do_not_leak(self):
        result = runtime_schema_diagnostics({"errors": [None, {"path": "$.confidence", "message": {"secret": TOKEN},
                                                          "actual": TOKEN, "expected": TOKEN}]})
        self.assertEqual(result["errors"][0]["check"], "MALFORMED_RUNTIME_ERROR")
        self.assertNotIn(TOKEN, serialise(result))

    def test_provider_retains_safe_details_through_failure_report(self):
        provider = MeterlessProvider(config())
        opener = serve(provider, {})
        raw = {"error": {"code": "schema_validation_failed", "message": "do not retain"},
               "meterless": {"validation": {"finish_reason": "stop", "errors": [
                   {"path": "$.factual_claims[0].kind", "message": "value is not one of the 6 allowed values"}]}},
               "usage": {"completion_tokens": 17}}
        opener.open.side_effect = HTTPError("http://localhost:8088/v1/chat/completions", 502, "", {}, io.BytesIO(json.dumps(raw).encode()))
        with self.assertRaises(ReasoningFailure) as caught:
            reason_bundle(completed_bundle(), provider)
        report = caught.exception.report
        diagnostics = report["inference"]["meterless"]["structured_output"]["runtime_validation"]
        self.assertEqual(diagnostics["errors"][0]["path"], "$.factual_claims[0].kind")
        self.assertEqual(diagnostics["errors"][0]["expected_from_argus_schema"]["enum"],
                         MODEL_OUTPUT_SCHEMA["properties"]["factual_claims"]["items"]["properties"]["kind"]["enum"])
        self.assertEqual(report["inference"]["finish_reason"], "stop")
        self.assertEqual(report["inference"]["completion_tokens"], 17)
        self.assertNotIn("do not retain", serialise(report))
        self.assertTrue(all(v == "not_run" for v in report["validation_stages"].values()))
        opener.open.assert_called_once()

    def test_unexpected_safe_root_property_is_retained_separately(self):
        error = self.normalize("$.assessment", "property is not allowed by the schema")
        self.assertEqual(error["path"], "$")
        self.assertTrue(error["path_redacted"])
        self.assertEqual(error["unexpected_property"], "assessment")
        self.assertEqual(error["unexpected_property_status"], "retained")
        self.assertEqual(error["expected_from_argus_schema"], {"additionalProperties": False})

    def test_unexpected_nested_property_retains_verified_parent(self):
        error = self.normalize("$.key_findings[2].extra_field2", "property is not allowed by the schema")
        self.assertEqual(error["path"], "$.key_findings[2]")
        self.assertEqual(error["unexpected_property"], "extra_field2")

    def test_unexpected_property_length_boundary(self):
        for size in (48, 49):
            error = self.normalize("$." + "z" * size, "property is not allowed by the schema")
            self.assertEqual("unexpected_property" in error, size == 48)

    def test_unsafe_or_credential_shaped_property_names_stay_redacted(self):
        for name in ("private-value", "a.b", "a[0]", "a\nvalue", "a\"value", "évidence",
                     "sk_private", "nvapi_private", "Bearer_private", "token_private", "secret_private",
                     "a" * 32, "", "42", "assessment." ):
            with self.subTest(name=name):
                error = self.normalize("$." + name, "property is not allowed by the schema")
                self.assertNotIn("unexpected_property", error)
                self.assertEqual(error["unexpected_property_status"], "redacted")

    def test_names_not_retained_for_unknown_messages_or_invalid_parents(self):
        for path, message in (("$.assessment", "untrusted message"),
                              ("$.confidence.extra", "property is not allowed by the schema"),
                              ("$.untrusted_parent.extra", "property is not allowed by the schema"),
                              ("$.key_findings[1000].extra", "property is not allowed by the schema")):
            with self.subTest(path=path):
                error = self.normalize(path, message)
                self.assertNotIn("unexpected_property", error)

    def test_http_rejection_retains_safe_property_without_its_value_or_raw_message(self):
        provider = MeterlessProvider(config())
        opener = serve(provider, {})
        raw = {"error": {"code": "schema_validation_failed", "message": "untrusted-message"},
               "meterless": {"validation": {"finish_reason": "stop", "errors": [
                   {"path": "$.assessment", "message": "property is not allowed by the schema", "actual": "private-value"}]}}}
        opener.open.side_effect = HTTPError("http://localhost:8088/v1/chat/completions", 502, "", {}, io.BytesIO(json.dumps(raw).encode()))
        with self.assertRaises(ReasoningFailure) as caught:
            reason_bundle(completed_bundle(), provider)
        report = caught.exception.report
        error = report["inference"]["meterless"]["structured_output"]["runtime_validation"]["errors"][0]
        self.assertEqual(error["unexpected_property"], "assessment")
        self.assertNotIn("private-value", serialise(report))
        self.assertNotIn("untrusted-message", serialise(report))
        self.assertTrue(all(v == "not_run" for v in report["validation_stages"].values()))
        opener.open.assert_called_once()

    def test_reflected_configured_credential_in_property_is_never_retained(self):
        # A short identifier can itself be a configured credential. The
        # existing transport guard must reject it before normalization.
        from dataclasses import replace
        provider = MeterlessProvider(replace(config(), runtime_token="PrivateCredential"))
        opener = serve(provider, {})
        raw = {"error": {"code": "schema_validation_failed"}, "meterless": {"validation": {"errors": [
            {"path": "$.PrivateCredential", "message": "property is not allowed by the schema"}]}}}
        opener.open.side_effect = HTTPError("http://localhost:8088/v1/chat/completions", 502, "", {}, io.BytesIO(json.dumps(raw).encode()))
        from scripts.reasoning_providers import ProviderError
        with self.assertRaises(ProviderError) as caught:
            reason_bundle(completed_bundle(), provider)
        self.assertNotIn("PrivateCredential", str(caught.exception))
        opener.open.assert_called_once()


if __name__ == "__main__":
    unittest.main()
