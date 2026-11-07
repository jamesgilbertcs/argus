"""Explicit JSON-mode transport with unchanged independent acceptance gates."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from scripts.evidence_collector import serialise
from scripts.reason_evidence import ReasoningFailure, main, reason_bundle
from scripts.reasoning_assessment import MODEL_OUTPUT_SCHEMA
from scripts.reasoning_assembly import VALIDATION_STAGES, validate_final_assessment
from scripts.reasoning_prompts import build_prompt
from scripts.reasoning_providers import MeterlessProvider, ProviderError
from scripts.reasoning_view import build_reasoning_view
from tests.test_meterless import MODEL, TOKEN, config
from tests.test_reasoning import model_content, valid_assessment
from tests.test_reasoning_contract import completed_bundle, serve


class MeterlessJSONTests(unittest.TestCase):
    def setUp(self):
        self.bundle = completed_bundle()
        self.view = build_reasoning_view(self.bundle)
        self.content = model_content(valid_assessment(self.view))
        self.provider = MeterlessProvider(config(), output_mode="json_object", temperature=0)

    def reply(self, content=None, *, kind="json_object", strict=False):
        return {"id": "chat_json_fixture", "object": "chat.completion", "model": MODEL,
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant",
                             "content": json.dumps(self.content) if content is None else content}}],
                "usage": {"prompt_tokens": 111, "completion_tokens": 222, "total_tokens": 333},
                "meterless": {"structured_output": {"kind": kind, "strict": strict, "name": None,
                              "schema_fingerprint": "0123456789abcdef", "capability": "unverified", "validation": "passed"}}}

    def generate(self):
        return self.provider.generate(build_prompt(self.view), MODEL_OUTPUT_SCHEMA)

    def failure(self, text, stage):
        opener = serve(self.provider, self.reply(text))
        with self.assertRaises(ReasoningFailure) as caught:
            reason_bundle(self.bundle, self.provider)
        self.assertEqual(caught.exception.report["validation_stage"], stage)
        self.assertFalse(caught.exception.report["validated_assessment_written"])
        self.assertEqual(caught.exception.report["inference"]["output_mode"], "json_object")
        opener.open.assert_called_once()
        return caught.exception.report

    def test_json_request_includes_exact_contract_in_system_only(self):
        opener = serve(self.provider, self.reply())
        before = copy.deepcopy(MODEL_OUTPUT_SCHEMA)
        self.generate()
        request = opener.open.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        prompt = build_prompt(self.view)
        self.assertTrue(body["messages"][0]["content"].startswith(prompt[0]))
        self.assertEqual(body["messages"][1], {"role": "user", "content": prompt[1]})
        self.assertEqual(json.loads(body["messages"][0]["content"].rsplit("\n", 1)[1]), MODEL_OUTPUT_SCHEMA)
        self.assertEqual(before, MODEL_OUTPUT_SCHEMA)
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["model"], MODEL)
        self.assertEqual(body["max_tokens"], config().max_output_tokens)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertNotIn("tools", body)
        self.assertFalse(body["stream"])

    def test_default_strict_request_equals_explicit_strict_request(self):
        requests = []
        for provider in (MeterlessProvider(config()), MeterlessProvider(config(), output_mode="json_schema")):
            opener = serve(provider, self.reply(kind="json_schema", strict=True))
            provider.generate(build_prompt(self.view), MODEL_OUTPUT_SCHEMA)
            requests.append(opener.open.call_args.args[0].data)
        self.assertEqual(*requests)
        body = json.loads(requests[0])
        self.assertEqual(body["response_format"], {"type": "json_schema", "json_schema": {
            "name": "argus_assessment", "strict": True, "schema": MODEL_OUTPUT_SCHEMA}})
        self.assertNotIn("temperature", body)
        self.assertNotIn(json.dumps(MODEL_OUTPUT_SCHEMA, separators=(",", ":")), body["messages"][0]["content"])

    def test_invalid_mode_and_temperature_fail_before_transport(self):
        for mode in ("prompt_json", "text", "auto", TOKEN):
            with self.subTest(mode=mode), self.assertRaises(ProviderError) as caught:
                MeterlessProvider(config(), output_mode=mode)
            self.assertNotIn(TOKEN, str(caught.exception))
        for value in (True, "0", float("nan"), float("inf"), -0.1, 2.1):
            with self.subTest(value=value), self.assertRaises(ProviderError):
                MeterlessProvider(config(), temperature=value)

    def test_valid_output_passes_every_gate_and_preserves_mode(self):
        original = copy.deepcopy(self.bundle)
        opener = serve(self.provider, self.reply())
        view, result = reason_bundle(self.bundle, self.provider)
        validate_final_assessment(result, view)
        self.assertEqual(result["validation_stages"], {key: "passed" for key in VALIDATION_STAGES})
        self.assertEqual(result["inference"]["output_mode"], "json_object")
        self.assertFalse(result["provider"]["mock_generated"])
        self.assertEqual(result["provider"]["metadata"]["requested_temperature"], 0)
        metadata = result["inference"]["meterless"]["structured_output"]
        self.assertEqual(metadata["kind"], "json_object")
        self.assertIs(metadata["requested_strict"], False)
        self.assertEqual(self.bundle, original)
        opener.open.assert_called_once()

    def test_invalid_json_not_repaired_or_retried(self):
        for text in ("not json", "```json\n{}\n```", '{"confidence":"LOW","confidence":"HIGH"}'):
            with self.subTest(text=text): self.failure(text, "json_parsing")

    def test_missing_fields_extra_fields_and_bad_enums_rejected(self):
        for change in ("missing", "extra", "enum"):
            value = copy.deepcopy(self.content)
            if change == "missing": del value["key_findings"]
            elif change == "extra": value["assessment_classification"] = "INCONCLUSIVE"
            else: value["confidence"] = "private-enum-value"
            with self.subTest(change=change):
                report = self.failure(json.dumps(value), "schema")
                self.assertNotIn(TOKEN, serialise(report))
                self.assertNotIn("private-enum-value", serialise(report))

    def test_fabricated_citation_rejected(self):
        value = copy.deepcopy(self.content)
        value["supporting_evidence"][0]["variant_id"] = "fabricated"
        self.failure(json.dumps(value), "citations_and_gaps")

    def test_contradictory_fact_rejected(self):
        value = copy.deepcopy(self.content)
        value["factual_claims"][0]["value"] = "contradiction"
        self.failure(json.dumps(value), "factual_consistency")

    def test_omitted_model_gap_discussion_does_not_erase_collection_gaps(self):
        self.bundle["issues"].append({"code": "history_unavailable", "source_id": "AUTH_A"})
        self.view = build_reasoning_view(self.bundle)
        self.content = model_content(valid_assessment(self.view))
        self.content["unknowns"] = []  # permitted by interpretation_v1
        serve(self.provider, self.reply())
        view, result = reason_bundle(self.bundle, self.provider)
        self.assertEqual(result["collection"]["limitations"], view["gaps"])
        self.assertEqual(result["collection"]["within_scope_completeness"], "INCOMPLETE")
        self.assertIs(result["assessment"]["evidence_complete"], False)
        validate_final_assessment(result, view)

    def test_fabricated_gap_rejected(self):
        value = copy.deepcopy(self.content)
        value["unknowns"] = [{"gap_ids": ["invented"], "description": "Unverified gap."}]
        self.failure(json.dumps(value), "citations_and_gaps")

    def test_runtime_schema_or_strict_metadata_cannot_mislabel_json_mode(self):
        for kind, strict in (("json_schema", True), ("json_object", True)):
            opener = serve(self.provider, self.reply(kind=kind, strict=strict))
            with self.subTest(kind=kind), self.assertRaises(ReasoningFailure) as caught:
                reason_bundle(self.bundle, self.provider)
            self.assertEqual(caught.exception.report["validation_stage"], "provider_structured_output")
            opener.open.assert_called_once()

    def test_missing_runtime_metadata_stays_unavailable(self):
        payload = self.reply(); del payload["meterless"]
        serve(self.provider, payload)
        response = self.generate()
        self.assertEqual(response.metadata["meterless"]["structured_output"], {
            "availability": "unavailable", "requested_kind": "json_object", "requested_strict": False})

    def test_unsupported_mode_http_error_is_safe_and_never_falls_back(self):
        opener = serve(self.provider, {})
        raw = json.dumps({"error": {"code": "structured_output_unsupported", "message": "private-error"}}).encode()
        opener.open.side_effect = HTTPError("http://localhost:8088/v1/chat/completions", 400, "private-header", {}, io.BytesIO(raw))
        with self.assertRaises(ReasoningFailure) as caught:
            reason_bundle(self.bundle, self.provider)
        report = caught.exception.report
        self.assertEqual(report["details"]["check"], "structured_output_unsupported")
        self.assertEqual(report["inference"]["output_mode"], "json_object")
        self.assertIs(report["inference"]["meterless"]["structured_output"]["requested_strict"], False)
        self.assertNotIn("private-", serialise(report))
        opener.open.assert_called_once()

    def test_unsuccessful_termination_keeps_json_mode_and_token_count(self):
        payload = self.reply(); payload["choices"][0]["finish_reason"] = "length"
        serve(self.provider, payload)
        with self.assertRaises(ReasoningFailure) as caught:
            reason_bundle(self.bundle, self.provider)
        self.assertEqual(caught.exception.report["validation_stage"], "provider_termination")
        self.assertEqual(caught.exception.report["inference"]["output_mode"], "json_object")
        self.assertEqual(caught.exception.report["inference"]["completion_tokens"], 222)

    def test_transport_limits_and_errors_remain_safe(self):
        for error in (TimeoutError(), URLError(TOKEN)):
            opener = serve(self.provider, {})
            opener.open.side_effect = error
            with self.subTest(error=type(error)), self.assertRaises(ProviderError) as caught: self.generate()
            self.assertNotIn(TOKEN, str(caught.exception))
            opener.open.assert_called_once()
        opener = serve(self.provider, {})
        opener.open.return_value.__enter__.return_value.read.return_value = b"x" * (self.provider.MAX_RESPONSE_BYTES + 1)
        with self.assertRaisesRegex(ProviderError, "byte limit"): self.generate()

    def test_reflected_credential_not_exposed(self):
        serve(self.provider, self.reply(content=TOKEN))
        with self.assertRaises(ProviderError) as caught: self.generate()
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_cli_selects_json_mode_and_writes_only_validated_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "bundle.json"
            original = serialise(self.bundle); source.write_text(original)
            serve(self.provider, self.reply())
            with patch("scripts.reason_evidence.MeterlessConfig.from_environment", return_value=config()), \
                 patch("scripts.reason_evidence.MeterlessProvider", return_value=self.provider) as adapter, \
                 patch("scripts.reason_evidence.MockProvider", side_effect=AssertionError("No fallback")), \
                 patch("sys.stdout", new=io.StringIO()) as output:
                self.assertEqual(main([str(source), "--provider", "meterless", "--meterless-output-mode", "json_object",
                                       "--meterless-temperature", "0", "--output-dir", str(root / "out")]), 0)
            self.assertEqual(adapter.call_args.kwargs, {"output_mode": "json_object", "temperature": 0.0})
            self.assertIn("Output mode: json_object", output.getvalue())
            self.assertNotIn(TOKEN, output.getvalue())
            self.assertEqual(source.read_text(), original)
            result = json.loads(next((root / "out").glob("*.assessment.json")).read_text())
            self.assertEqual(result["validation_stages"], {key: "passed" for key in VALIDATION_STAGES})

    def test_cli_failure_does_not_save_raw_response_or_validated_assessment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "bundle.json"
            source.write_text(serialise(self.bundle))
            serve(self.provider, self.reply("untrusted narrative"))
            with patch("scripts.reason_evidence.MeterlessConfig.from_environment", return_value=config()), \
                 patch("scripts.reason_evidence.MeterlessProvider", return_value=self.provider), \
                 patch("sys.stdout", new=io.StringIO()) as output:
                self.assertEqual(main([str(source), "--provider", "meterless", "--meterless-output-mode", "json_object",
                                       "--output-dir", str(root / "out")]), 1)
            paths = list((root / "out").iterdir())
            self.assertEqual(len(paths), 1)
            self.assertIn(".failure.", paths[0].name)
            self.assertNotIn("untrusted narrative", paths[0].read_text() + output.getvalue())

    def test_cli_rejects_meterless_options_with_other_providers(self):
        with patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(["unused.json", "--provider", "mock", "--meterless-output-mode", "json_object"])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
