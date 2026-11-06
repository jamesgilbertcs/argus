"""Strict Meterless requests and independent acceptance. Mock HTTP only."""

from __future__ import annotations

import copy
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from scripts.evidence_collector import serialise
from scripts.reason_evidence import ReasoningFailure, reason_bundle
from scripts.reasoning_assessment import MODEL_OUTPUT_SCHEMA, FACT_KINDS, strict_json, validate_model_content
from scripts.reasoning_assembly import VALIDATION_STAGES, validate_final_assessment
from scripts.reasoning_prompts import build_prompt
from scripts.reasoning_providers import MeterlessProvider, MockProvider, ProviderError, StructuredOutputError
from scripts.reasoning_view import ReasoningError, build_reasoning_view
from tests.test_meterless import MODEL, TOKEN, config
from tests.test_reasoning import model_content, valid_assessment
from tests.test_reasoning_contract import completed_bundle, serve


class StrictMeterlessTests(unittest.TestCase):
    def setUp(self):
        self.bundle = completed_bundle()
        self.view = build_reasoning_view(self.bundle)
        self.provider = MeterlessProvider(config())
        self.content = model_content(valid_assessment(self.view))

    def reply(self, *, content=None, structured=True):
        payload = {"id": "chat_fixture", "object": "chat.completion", "model": MODEL,
                   "choices": [{"index": 0, "finish_reason": "stop", "message": {
                       "role": "assistant", "content": json.dumps(self.content) if content is None else content}}],
                   "usage": {"prompt_tokens": 123, "completion_tokens": 456, "total_tokens": 579}}
        if structured:
            payload["meterless"] = {"structured_output": {"kind": "json_schema", "strict": True,
                "name": "argus_assessment", "schema_fingerprint": "0123456789abcdef",
                "capability": "unverified", "validation": "passed"}}
        return payload

    def generate(self):
        return self.provider.generate(build_prompt(self.view), MODEL_OUTPUT_SCHEMA)

    def test_exact_schema_request_without_prompt_duplication_or_metadata_fields(self):
        before = copy.deepcopy(MODEL_OUTPUT_SCHEMA)
        opener = serve(self.provider, self.reply())
        self.generate()
        body = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(body["response_format"], {"type": "json_schema", "json_schema": {
            "name": "argus_assessment", "strict": True, "schema": MODEL_OUTPUT_SCHEMA}})
        self.assertEqual(body["model"], MODEL)
        self.assertEqual(body["max_tokens"], config().max_output_tokens)
        self.assertFalse(body["stream"])
        self.assertNotIn("tools", body)
        self.assertNotIn("Match this JSON Schema exactly:", body["messages"][0]["content"])
        self.assertEqual(before, MODEL_OUTPUT_SCHEMA)
        for key in ("schema_version", "alert_id", "collection_status", "evidence_complete"):
            self.assertNotIn(key, body["response_format"]["json_schema"]["schema"]["properties"])

    def test_all_objects_have_exact_required_fields_and_no_extra_properties(self):
        def walk(schema):
            if schema.get("type") == "object":
                self.assertEqual(set(schema["required"]), set(schema["properties"]))
                self.assertIs(schema["additionalProperties"], False)
                for child in schema["properties"].values():
                    walk(child)
            if "items" in schema:
                walk(schema["items"])
            for child in schema.get("anyOf", []):
                walk(child)
        walk(MODEL_OUTPUT_SCHEMA)
        props = MODEL_OUTPUT_SCHEMA["properties"]
        self.assertEqual(props["classification"]["enum"], ["LIKELY_TRUE_POSITIVE", "LIKELY_FALSE_POSITIVE", "INCONCLUSIVE"])
        self.assertEqual(props["confidence"]["enum"], ["LOW", "MEDIUM", "HIGH"])
        self.assertEqual(props["key_findings"]["items"]["properties"]["kind"]["enum"], ["OBSERVED_FACT", "INTERPRETATION"])
        self.assertEqual(props["factual_claims"]["items"]["properties"]["kind"]["enum"], FACT_KINDS)
        self.assertEqual(props["factual_claims"]["items"]["properties"]["value"]["anyOf"],
                         [{"type": "string"}, {"type": "integer"}, {"type": "null"}])

    def test_schema_rejects_nested_finding_in_claims_missing_fields_and_extra_fields(self):
        for change in ("finding_in_claims", "missing_findings", "extra_root", "extra_nested", "enum", "wrong_value_type"):
            content = copy.deepcopy(self.content)
            if change == "finding_in_claims": content["factual_claims"].append(copy.deepcopy(content["key_findings"][0]))
            elif change == "missing_findings": del content["key_findings"]
            elif change == "extra_root": content["evidence_complete"] = True
            elif change == "extra_nested": content["key_findings"][0]["unexpected"] = True
            elif change == "enum": content["classification"] = "GUILTY"
            else: content["factual_claims"][0]["value"] = False
            serve(self.provider, self.reply(content=json.dumps(content)))
            with self.subTest(change=change), self.assertRaises(ReasoningFailure) as caught:
                reason_bundle(self.bundle, self.provider)
            self.assertEqual(caught.exception.report["validation_stage"], "schema")
            self.assertEqual(caught.exception.report["validation_stages"]["final_assembly"], "not_run")

    def test_runtime_passed_does_not_bypass_json_citation_or_fact_checks(self):
        for stage in ("json_parsing", "citations_and_gaps", "factual_consistency"):
            content = copy.deepcopy(self.content)
            if stage == "citations_and_gaps": content["supporting_evidence"][0]["variant_id"] = "fabricated"
            elif stage == "factual_consistency": content["factual_claims"][0]["value"] = "contradiction"
            raw = "not json" if stage == "json_parsing" else json.dumps(content)
            serve(self.provider, self.reply(content=raw))
            with self.subTest(stage=stage), self.assertRaises(ReasoningFailure) as caught:
                reason_bundle(self.bundle, self.provider)
            self.assertEqual(caught.exception.report["validation_stage"], stage)
            self.assertEqual(caught.exception.report["inference"]["meterless"]["structured_output"]["validation"], "passed")

    def test_success_keeps_all_stages_and_runtime_namespace(self):
        serve(self.provider, self.reply())
        view, result = reason_bundle(self.bundle, self.provider)
        self.assertEqual(result["validation_stages"], {key: "passed" for key in VALIDATION_STAGES})
        validate_final_assessment(result, view)
        self.assertEqual(result["inference"]["output_mode"], "strict_json_schema")
        self.assertEqual(result["inference"]["http_status"], 200)
        self.assertEqual(result["inference"]["completion_tokens"], 456)
        namespace = result["provider"]["metadata"]["meterless"]["structured_output"]
        self.assertEqual(namespace["kind"], "json_schema")
        self.assertTrue(namespace["strict"])
        self.assertEqual(namespace["schema_fingerprint"], "0123456789abcdef")
        self.assertEqual(namespace["capability"], "unverified")
        self.assertEqual(namespace, result["inference"]["meterless"]["structured_output"])

    def test_missing_runtime_metadata_is_unknown_without_changing_request_mode(self):
        opener = serve(self.provider, self.reply(structured=False))
        response = self.generate()
        self.assertEqual(response.metadata["meterless"]["structured_output"], {
            "availability": "unavailable", "requested_kind": "json_schema", "requested_strict": True})
        self.assertEqual(json.loads(opener.open.call_args.args[0].data)["response_format"]["type"], "json_schema")
        validate_model_content(strict_json(response.text), self.view)

    def test_rejected_or_malformed_runtime_metadata_never_downgrades(self):
        for key, bad in (("kind", "json_object"), ("strict", False), ("validation", "not_run"),
                         ("capability", "unsupported"), ("name", "wrong"), ("schema_fingerprint", "secret-like-text")):
            payload = self.reply()
            payload["meterless"]["structured_output"][key] = bad
            opener = serve(self.provider, payload)
            with self.subTest(key=key), self.assertRaises(StructuredOutputError) as caught:
                self.generate()
            self.assertNotIn("secret-like-text", str(caught.exception) + serialise(caught.exception.inference))
            opener.open.assert_called_once()

    def test_metadata_omits_untrusted_extra_fields_without_copying_names_or_values(self):
        payload = self.reply()
        payload["meterless"]["structured_output"]["sensitive-property"] = "sensitive-value"
        payload["meterless"]["receipt"] = {"sensitive": "do not copy"}
        serve(self.provider, payload)
        metadata = self.generate().metadata
        self.assertEqual(metadata["meterless"]["structured_output"]["omitted_field_count"], 1)
        for text in ("sensitive-property", "sensitive-value", "do not copy"):
            self.assertNotIn(text, serialise(metadata))

    def test_typed_http_format_errors_are_safe_and_not_retried(self):
        for code in MeterlessProvider.STRUCTURED_ERRORS:
            opener = serve(self.provider, self.reply())
            raw = json.dumps({"error": {"code": code, "message": "untrusted-error-text"}}).encode()
            opener.open.side_effect = HTTPError("http://localhost:8088/v1/chat/completions", 502, "untrusted-header", {}, io.BytesIO(raw))
            with self.subTest(code=code), self.assertRaises(ReasoningFailure) as caught:
                reason_bundle(self.bundle, self.provider)
            report = caught.exception.report
            self.assertEqual(report["validation_stage"], "provider_structured_output")
            self.assertEqual(report["details"]["check"], code)
            self.assertEqual(report["inference"]["http_status"], 502)
            self.assertIsNone(report["inference"]["actual_model"])
            self.assertTrue(all(value == "not_run" for value in report["validation_stages"].values()))
            self.assertNotIn("untrusted-error-text", serialise(report))
            self.assertNotIn("untrusted-header", serialise(report))
            opener.open.assert_called_once()

    def test_http_200_error_payload_is_rejected_without_fallback(self):
        opener = serve(self.provider, {"error": {"code": "structured_output_unsupported", "message": "secret-reflection"}})
        with self.assertRaises(ReasoningFailure) as caught:
            reason_bundle(self.bundle, self.provider)
        self.assertEqual(caught.exception.report["details"]["check"], "structured_output_unsupported")
        self.assertNotIn("secret-reflection", serialise(caught.exception.report))
        self.assertIsNone(caught.exception.report["inference"]["actual_model"])
        opener.open.assert_called_once()

    def test_http_runtime_termination_diagnostics_preserve_known_fields_only(self):
        opener = serve(self.provider, self.reply())
        raw = json.dumps({"error": {"code": "response_truncated"}, "meterless": {"validation": {
            "finish_reason": "length", "errors": [{"message": "private schema text"}]}}, "usage": {"completion_tokens": 6000}}).encode()
        opener.open.side_effect = HTTPError("http://localhost:8088/v1/chat/completions", 502, "", {}, io.BytesIO(raw))
        with self.assertRaises(ReasoningFailure) as caught:
            reason_bundle(self.bundle, self.provider)
        report = caught.exception.report
        self.assertEqual(report["inference"]["finish_reason"], "length")
        self.assertEqual(report["inference"]["completion_tokens"], 6000)
        self.assertNotIn("private schema text", serialise(report))

    def test_unhashable_error_code_and_reflected_credential_are_safe(self):
        for error in ({"code": ["unexpected"]}, {"code": "schema_validation_failed", "message": TOKEN}):
            opener = serve(self.provider, self.reply())
            opener.open.side_effect = HTTPError("http://localhost:8088/v1/chat/completions", 400, TOKEN, {}, io.BytesIO(json.dumps({"error": error}).encode()))
            with self.subTest(kind=type(error["code"])), self.assertRaises(ProviderError) as caught:
                self.generate()
            self.assertNotIn(TOKEN, str(caught.exception))
            opener.open.assert_called_once()

    def test_nonstopped_completion_keeps_strict_mode_and_runtime_diagnostics(self):
        payload = self.reply()
        payload["choices"][0]["finish_reason"] = "length"
        serve(self.provider, payload)
        with self.assertRaises(ReasoningFailure) as caught:
            reason_bundle(self.bundle, self.provider)
        report = caught.exception.report
        self.assertEqual(report["validation_stage"], "provider_termination")
        self.assertEqual(report["inference"]["output_mode"], "strict_json_schema")
        self.assertEqual(report["inference"]["meterless"]["structured_output"]["kind"], "json_schema")


class FinalArtifactValidationTests(unittest.TestCase):
    def setUp(self):
        self.bundle = completed_bundle()
        self.view, self.result = reason_bundle(self.bundle, MockProvider())

    def test_valid_saved_artifact_and_model_content_are_unchanged(self):
        before = copy.deepcopy(self.result)
        validate_final_assessment(self.result, self.view)
        self.assertEqual(self.result, before)
        self.assertEqual(self.result["schema_version"], "3.0")

    def test_invalid_final_schema_and_extra_properties_are_rejected(self):
        for field, value in (("confidence", "CERTAIN"), ("evidence_complete", "true"), ("unexpected", True)):
            result = copy.deepcopy(self.result)
            result["assessment"][field] = value
            with self.subTest(field=field), self.assertRaises(ReasoningError):
                validate_final_assessment(result, self.view)

    def test_deterministic_metadata_cannot_be_changed_after_assembly(self):
        for change in ("hash", "alert", "sources", "relationships", "collection", "complete", "provider", "stages", "inference", "extra_root"):
            result = copy.deepcopy(self.result)
            if change == "hash": result["bundle_sha256"] = "wrong"
            elif change == "alert": result["assessment"]["alert_id"] = "OTHER"
            elif change == "sources": result["provenance"]["sources"].pop()
            elif change == "relationships": result["relationships"]["facts"].pop()
            elif change == "collection": result["collection"]["collection_succeeded"] = 1
            elif change == "complete": result["assessment"]["evidence_complete"] = None
            elif change == "provider": result["provider"]["mock_generated"] = False
            elif change == "stages": result["validation_stages"]["final_assembly"] = "not_run"
            elif change == "inference": result["inference"]["completion_tokens"] = 100
            else: result["unexpected"] = True
            with self.subTest(change=change), self.assertRaises(ReasoningError):
                validate_final_assessment(result, self.view)

    def test_bad_assembled_artifact_fails_before_final_stage_is_passed(self):
        bad = copy.deepcopy(self.result)
        bad.pop("validation_stages")
        bad["assessment"]["alert_id"] = "WRONG"
        with patch("scripts.reason_evidence.assemble_assessment", return_value=bad):
            with self.assertRaises(ReasoningFailure) as caught:
                reason_bundle(self.bundle, MockProvider())
        self.assertEqual(caught.exception.report["validation_stage"], "final_assembly")
        self.assertFalse(caught.exception.report["validated_assessment_written"])
        self.assertEqual(caught.exception.report["validation_stages"]["factual_consistency"], "passed")


if __name__ == "__main__":
    unittest.main()
