"""Contract 3.0 regressions. Inference/HTTP is mocked; no runtime or secrets."""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.evidence_collector import serialise
from scripts.evidence_relationships import derive_relationships
from scripts.reason_evidence import ReasoningFailure, failure_response_path, main, reason_bundle
from scripts.reasoning_assessment import (ASSESSMENT_SCHEMA, MODEL_OUTPUT_SCHEMA, FactualConsistencyError,
                                          strict_json, validate_assessment, validate_model_content,
                                          validate_model_structure)
from scripts.reasoning_assembly import assemble_assessment, collection_metadata
from scripts.reasoning_prompts import build_prompt
from scripts.reasoning_providers import (InferenceResponse, MeterlessConfig, MeterlessProvider, MockProvider,
                                         OpenAIConfig, OpenAIProvider)
from scripts.reasoning_view import ReasoningError, build_reasoning_view, digest
from tests.test_reasoning import model_content, valid_assessment
from tests.test_reasoning_hardening import generic_bundle


def completed_bundle() -> dict:
    bundle = generic_bundle()
    # Complete the alert's own exact-IP check as well as the event checks.
    bundle["network_checks"].append({"source_ip": "synthetic", "status": "no_exact_match", "network_ids": []})
    return bundle


def inference(content: dict, **metadata) -> InferenceResponse:
    return InferenceResponse(json.dumps(content), "mock", "contract-fixture", True, metadata)


def serve(provider, payload: dict) -> Mock:
    response = Mock(status=200)
    response.read.return_value = json.dumps(payload).encode()
    opener = Mock()
    opener.open.return_value.__enter__ = Mock(return_value=response)
    opener.open.return_value.__exit__ = Mock(return_value=False)
    provider._opener = opener
    return opener


class CompletenessTests(unittest.TestCase):
    def test_successful_reported_bounded_plan_can_be_complete_but_not_resolved(self):
        view = build_reasoning_view(completed_bundle())
        self.assertEqual({g["kind"] for g in view["gaps"]}, {"bounded_scope"})
        metadata = collection_metadata(view)
        self.assertTrue(metadata["collection_succeeded"])
        self.assertEqual(metadata["within_scope_completeness"], "COMPLETE")
        self.assertEqual(metadata["investigation_sufficiency"], "NOT_DETERMINED")
        self.assertEqual(metadata["limitations"], view["gaps"])

    def test_collected_does_not_override_missing_evidence(self):
        bundle = completed_bundle()
        bundle["issues"].append({"code": "missing_user", "source_id": "USER_MISSING"})
        metadata = collection_metadata(build_reasoning_view(bundle))
        self.assertTrue(metadata["collection_succeeded"])
        self.assertEqual(metadata["within_scope_completeness"], "INCOMPLETE")
        self.assertEqual(metadata["issues"], bundle["issues"])

    def test_no_query_receipts_means_unknown_not_true_or_false(self):
        bundle = completed_bundle()
        bundle["queries"] = []
        view, result = reason_bundle(bundle, MockProvider())
        self.assertIsNone(result["assessment"]["evidence_complete"])
        self.assertEqual(result["collection"]["within_scope_completeness"], "UNKNOWN")
        self.assertIn("query_receipts_unavailable", collection_metadata(view)["completeness_basis"])

    def test_missing_declared_scope_means_unknown(self):
        bundle = completed_bundle()
        bundle["scope"]["history_window"] = None
        result = reason_bundle(bundle, MockProvider())[1]
        self.assertIsNone(result["assessment"]["evidence_complete"])
        self.assertIn("declared_history_window_unavailable", result["collection"]["completeness_basis"])

    def test_truncated_unavailable_and_partial_are_incomplete(self):
        for status in ("potentially_truncated", "unavailable", "malformed", "budget_exhausted"):
            bundle = completed_bundle()
            bundle["collection_status"] = "partial"
            bundle["queries"][0]["status"] = status
            result = reason_bundle(bundle, MockProvider())[1]
            self.assertFalse(result["assessment"]["evidence_complete"])
            self.assertFalse(result["collection"]["collection_succeeded"])
            self.assertEqual(result["collection"]["query_completeness"][0]["status"], status)

    def test_unknown_alert_does_not_become_complete(self):
        bundle = completed_bundle()
        bundle["records"]["security_alerts"] = []
        bundle["collection_status"] = "alert_not_found"
        result = reason_bundle(bundle, MockProvider())[1]
        self.assertFalse(result["assessment"]["evidence_complete"])
        self.assertEqual(result["assessment"]["classification"], "INCONCLUSIVE")

    def test_incomplete_network_check_never_becomes_definitive_negative(self):
        bundle = completed_bundle()
        bundle["network_checks"][0]["status"] = "unavailable"
        result = reason_bundle(bundle, MockProvider())[1]
        self.assertFalse(result["assessment"]["evidence_complete"])
        fact = next(f for f in result["relationships"]["facts"] if f["kind"] == "NETWORK_MATCH_STATUS" and
                    f["evidence"][0]["id"] == "AUTH_A")
        self.assertEqual(fact["value"], "INCOMPLETE")


class ModelContractTests(unittest.TestCase):
    def setUp(self):
        self.bundle = completed_bundle()
        self.view = build_reasoning_view(self.bundle)
        self.content = model_content(valid_assessment(self.view))

    def test_collection_derived_fields_absent_and_rejected_if_returned(self):
        fields = {"alert_id", "schema_version", "collection_status", "evidence_complete", "relationships", "bundle_sha256"}
        self.assertFalse(fields & MODEL_OUTPUT_SCHEMA["properties"].keys())
        for field in fields:
            with self.subTest(field=field), self.assertRaises(ReasoningError):
                validate_model_structure(self.content | {field: True})

    def test_original_completeness_failure_is_no_longer_model_responsibility(self):
        historical = valid_assessment(self.view)
        historical["evidence_complete"] = True
        with self.assertRaises(ReasoningError):
            validate_assessment(historical, self.view)
        provider = Mock()
        provider.generate.return_value = inference(self.content)
        result = reason_bundle(self.bundle, provider)[1]
        self.assertTrue(result["assessment"]["evidence_complete"])
        self.assertNotIn("evidence_complete", provider.generate.call_args.args[1]["properties"])

    def test_selective_assertions_checked_without_repeating_unused_facts(self):
        self.content["factual_claims"] = self.content["factual_claims"][:1]
        validate_model_content(self.content, self.view)
        result = assemble_assessment(self.content, self.view, inference(self.content))
        self.assertEqual(len(result["assessment"]["factual_claims"]), 1)
        self.assertEqual(result["relationships"], derive_relationships(self.view))

    def test_all_supported_claim_contradictions_still_fail(self):
        for kind in {c["kind"] for c in self.content["factual_claims"]}:
            content = copy.deepcopy(self.content)
            next(c for c in content["factual_claims"] if c["kind"] == kind)["value"] = "contradiction"
            provider = Mock(generate=Mock(return_value=inference(content)))
            with self.subTest(kind=kind), self.assertRaises(ReasoningFailure) as caught:
                reason_bundle(self.bundle, provider)
            self.assertEqual(caught.exception.report["validation_stage"], "factual_consistency")
            self.assertEqual(caught.exception.report["validation_stages"]["final_assembly"], "not_run")
            provider.generate.assert_called_once()

    def test_wrong_variant_and_citation_are_not_relaxed(self):
        for field in ("id", "table", "variant_id"):
            content = copy.deepcopy(self.content)
            content["key_findings"][0]["evidence"][0][field] = "not_supplied"
            with self.subTest(field=field), self.assertRaises(ReasoningError):
                validate_model_content(content, self.view)

    def test_wrong_fact_evidence_rejected_even_for_selected_claim(self):
        self.content["factual_claims"][0]["evidence"] = []
        with self.assertRaisesRegex(FactualConsistencyError, "FACT_EVIDENCE_MISMATCH"):
            validate_model_content(self.content, self.view)

    def test_invalid_classification_confidence_and_missing_citations_fail(self):
        for key, value in (("classification", "GUILTY"), ("confidence", "CERTAIN")):
            with self.subTest(key=key), self.assertRaises(ReasoningError):
                validate_model_content(self.content | {key: value}, self.view)
        self.content["key_findings"][0]["evidence"] = []
        with self.assertRaises(ReasoningError):
            validate_model_content(self.content, self.view)

    def test_investigative_unknowns_need_not_copy_gaps_but_cannot_invent_ids(self):
        self.content["unknowns"] = [{"description": "Independent human confirmation is unavailable.", "gap_ids": []}]
        result = assemble_assessment(self.content, self.view, inference(self.content))
        self.assertEqual(result["collection"]["limitations"], self.view["gaps"])
        self.content["unknowns"][0]["gap_ids"] = ["invented"]
        with self.assertRaises(ReasoningError):
            validate_model_content(self.content, self.view)

    def test_historical_v1_and_v2_acceptance_rules_unchanged(self):
        legacy = valid_assessment(self.view)
        validate_assessment(legacy, self.view)
        del legacy["assessed_hypothesis"], legacy["factual_claims"]
        legacy["schema_version"] = "1.0"
        validate_assessment(legacy, self.view, allow_legacy=True)
        with self.assertRaises(ReasoningError):
            validate_model_content(legacy, self.view)


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.bundle = completed_bundle()
        self.view = build_reasoning_view(self.bundle)
        self.content = model_content(valid_assessment(self.view))

    def test_final_assembly_is_deterministic_and_preserves_model_content(self):
        response = inference(self.content)
        before = copy.deepcopy((self.content, self.view))
        result = assemble_assessment(self.content, self.view, response)
        self.assertEqual(serialise(result), serialise(assemble_assessment(self.content, self.view, response)))
        self.assertEqual((self.content, self.view), before)
        self.assertEqual({k: result["assessment"][k] for k in self.content}, self.content)
        self.assertEqual(result["schema_version"], "3.0")
        self.assertEqual(result["assessment"]["alert_id"], self.view["alert_id"])

    def test_source_provenance_hashes_and_relationships_preserved(self):
        view, result = reason_bundle(self.bundle, MockProvider())
        self.assertEqual(result["bundle_sha256"], digest(self.bundle))
        self.assertEqual(result["reasoning_view_sha256"], digest(view))
        self.assertEqual(result["provenance"]["sources"], [r["source"] for r in view["records"]])
        self.assertEqual(result["relationships"], view["relationships"])
        self.assertTrue(all(v == "passed" for v in result["validation_stages"].values()))

    def test_assembly_does_not_authorise_tampered_fact_catalog(self):
        self.content["factual_claims"][0]["value"] = "tampered"
        self.view["relationships"]["facts"][0]["value"] = "tampered"
        with self.assertRaises(FactualConsistencyError):
            assemble_assessment(self.content, self.view, inference(self.content))

    def test_assembly_failure_is_not_labelled_validated(self):
        provider = Mock(generate=Mock(return_value=inference(self.content)))
        with patch("scripts.reason_evidence.assemble_assessment", side_effect=ReasoningError("Assembly check failed")):
            with self.assertRaises(ReasoningFailure) as caught:
                reason_bundle(self.bundle, provider)
        self.assertEqual(caught.exception.report["validation_stage"], "final_assembly")
        self.assertFalse(caught.exception.report["validated_assessment_written"])

    def test_missing_metadata_is_explicitly_unknown(self):
        result = assemble_assessment(self.content, self.view, inference(self.content))
        for field in ("finish_reason", "completion_tokens", "requested_model", "output_mode"):
            self.assertIsNone(result["inference"][field])
            self.assertEqual(result["inference"][field + "_status"], "unavailable")
        self.assertEqual(result["inference"]["actual_model"], "contract-fixture")

    def test_available_provider_metadata_preserved(self):
        metadata = {"requested_model": "requested", "output_mode": "prompt_json", "finish_reason": "stop",
                    "usage": {"completion_tokens": 123, "prompt_tokens": 456}, "max_output_tokens": 6000}
        response = inference(self.content, **metadata)
        result = assemble_assessment(self.content, self.view, response)
        self.assertEqual(result["provider"]["metadata"], metadata)
        self.assertEqual(result["inference"]["completion_tokens"], 123)
        self.assertEqual(result["inference"]["requested_model"], "requested")

    def test_contradictory_variants_and_missing_context_still_visible(self):
        bundle = copy.deepcopy(self.bundle)
        row = copy.deepcopy(bundle["records"]["authentication_logs"][0])
        row["record"]["outcome"] = "FAILURE"
        row["variant_id"] = digest(row["record"])
        bundle["records"]["authentication_logs"].append(row)
        bundle["issues"].append({"code": "missing_resource", "source_id": "RESOURCE_MISSING"})
        view, result = reason_bundle(bundle, MockProvider())
        outcomes = {f["value"] for f in result["relationships"]["facts"] if f["kind"] == "AUTHENTICATION_OUTCOME"}
        self.assertEqual(outcomes, {"SUCCESS", "FAILURE"})
        self.assertEqual(result["collection"]["limitations"], view["gaps"])
        self.assertFalse(result["assessment"]["evidence_complete"])

    def test_bad_claim_diagnostics_do_not_include_model_text_or_secret(self):
        secret = "secret-reflected-by-model"
        self.content["factual_claims"][0]["value"] = secret
        provider = Mock(generate=Mock(return_value=inference(self.content)))
        with self.assertRaises(ReasoningFailure) as caught:
            reason_bundle(self.bundle, provider)
        self.assertNotIn(secret, serialise(caught.exception.report))
        self.assertEqual(caught.exception.report["details"]["check"], "FACT_VALUE_MISMATCH")


class ProviderContractTests(unittest.TestCase):
    def setUp(self):
        self.bundle = completed_bundle()
        self.view = build_reasoning_view(self.bundle)
        self.content = model_content(valid_assessment(self.view))

    def test_mock_explicitly_supports_new_and_historical_contracts(self):
        prompt = build_prompt(self.view)
        provider = MockProvider()
        historical = strict_json(provider.generate(prompt, ASSESSMENT_SCHEMA).text)
        validate_assessment(historical, self.view)
        new = provider.generate(prompt, MODEL_OUTPUT_SCHEMA)
        validate_model_content(strict_json(new.text), self.view)
        self.assertTrue(new.mock_generated)
        self.assertNotIn("collection_status", strict_json(new.text))

    def test_openai_uses_new_schema_and_preserves_actual_requested_model(self):
        provider = OpenAIProvider(OpenAIConfig("fixture-api-secret", "configured-model"))
        opener = serve(provider, {"id": "response_fixture", "model": "configured-model-snapshot", "status": "completed",
                                  "error": None, "incomplete_details": None, "usage": {"output_tokens": 100},
                                  "output": [{"type": "message", "role": "assistant", "status": "completed",
                                              "content": [{"type": "output_text", "text": json.dumps(self.content)}]}]})
        result = reason_bundle(self.bundle, provider)[1]
        body = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(body["text"]["format"]["schema"], MODEL_OUTPUT_SCHEMA)
        self.assertEqual(result["inference"]["requested_model"], "configured-model")
        self.assertEqual(result["inference"]["actual_model"], "configured-model-snapshot")
        self.assertEqual(result["inference"]["completion_tokens"], 100)
        self.assertIsNone(result["inference"]["finish_reason"])

    def test_meterless_uses_new_schema_in_explicit_strict_output_mode(self):
        provider = MeterlessProvider(MeterlessConfig("http://localhost:8088/v1", "fixture-runtime-secret", "configured-model"))
        opener = serve(provider, {"id": "chat_fixture", "object": "chat.completion", "model": "configured-model",
                                  "choices": [{"index": 0, "finish_reason": "stop", "message": {
                                      "role": "assistant", "content": json.dumps(self.content)}}], "usage": {"completion_tokens": 100}})
        result = reason_bundle(self.bundle, provider)[1]
        body = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(body["response_format"], {"type": "json_schema", "json_schema": {
            "name": "argus_assessment", "strict": True, "schema": MODEL_OUTPUT_SCHEMA}})
        self.assertEqual(result["inference"]["output_mode"], "strict_json_schema")
        self.assertEqual(result["inference"]["finish_reason"], "stop")

    def test_unsuccessful_or_missing_termination_keeps_safe_diagnostics(self):
        for finish in ("length", None, "secret-invalid-finish-value"):
            provider = MeterlessProvider(MeterlessConfig("http://localhost:8088/v1", "fixture-runtime-secret", "configured-model"))
            opener = serve(provider, {"id": "chat_fixture", "object": "chat.completion", "model": "configured-model",
                                      "choices": [{"index": 0, "finish_reason": finish, "message": {
                                          "role": "assistant", "content": "unvalidated secret text"}}], "usage": {"completion_tokens": 4391}})
            with self.subTest(finish=finish), self.assertRaises(ReasoningFailure) as caught:
                reason_bundle(self.bundle, provider)
            report = caught.exception.report
            self.assertEqual(report["validation_stage"], "provider_termination")
            self.assertEqual(report["inference"]["completion_tokens"], 4391)
            self.assertTrue(all(v == "not_run" for v in report["validation_stages"].values()))
            self.assertNotIn("unvalidated secret text", serialise(report))
            self.assertNotIn("secret-invalid-finish-value", serialise(report))
            opener.open.assert_called_once()


class FailureRetentionTests(unittest.TestCase):
    def test_raw_failure_retention_is_explicit_and_never_printed(self):
        for retain in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "bundle.json"
                original = serialise(completed_bundle())
                source.write_text(original, encoding="utf-8")
                raw = '{"untrusted":"model diagnostic text"'
                args = [str(source), "--provider", "mock", "--output-dir", str(root / "out")]
                if retain:
                    args.append("--retain-failed-response")
                with patch("scripts.reason_evidence.MockProvider.generate", return_value=InferenceResponse(raw, "mock", "fixture", True)) as generate, \
                     patch("sys.stdout", new=io.StringIO()) as output:
                    self.assertEqual(main(args), 1)
                generate.assert_called_once()
                reports = list((root / "out").glob("*.json"))
                self.assertEqual(len(reports), 1)
                self.assertFalse(json.loads(reports[0].read_text())["validated_assessment_written"])
                responses = list((root / "out").glob("*.response.txt"))
                self.assertEqual(len(responses), int(retain))
                if retain:
                    self.assertEqual(responses[0].read_bytes(), raw.encode())
                self.assertNotIn("model diagnostic text", output.getvalue())
                self.assertEqual(source.read_text(encoding="utf-8"), original)

    def test_configured_credential_prevents_raw_failure_persistence(self):
        secret = "fixture-runtime-secret"
        config = MeterlessConfig("http://localhost:8088/v1", secret, "configured-model")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bundle.json"
            source.write_text(serialise(completed_bundle()), encoding="utf-8")
            provider = Mock(generate=Mock(return_value=InferenceResponse("malformed " + secret, "meterless", "configured-model", False)))
            with patch("scripts.reason_evidence.MeterlessConfig.from_environment", return_value=config), \
                 patch("scripts.reason_evidence.MeterlessProvider", return_value=provider), \
                 patch("sys.stdout", new=io.StringIO()) as output:
                self.assertEqual(main([str(source), "--provider", "meterless", "--output-dir", str(root / "out"), "--retain-failed-response"]), 1)
            self.assertFalse((root / "out").exists())
            self.assertNotIn(secret, output.getvalue())

    def test_raw_failure_path_cannot_follow_links_or_overwrite_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "failure.json"
            with self.assertRaises(ReasoningError):
                failure_response_path(path, path.with_suffix(".response.txt"))
            with patch.object(Path, "is_symlink", return_value=True), self.assertRaises(ReasoningError):
                failure_response_path(path, Path(directory) / "bundle.json")


if __name__ == "__main__":
    unittest.main()
