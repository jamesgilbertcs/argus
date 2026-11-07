"""Schema-derived presentation only; acceptance contracts and other providers stay fixed."""
import copy
import json
import unittest
from unittest.mock import patch

from scripts.reason_evidence import ReasoningFailure, reason_bundle
from scripts.reasoning_assessment import MODEL_OUTPUT_SCHEMA, validate_model_content
from scripts.reasoning_prompts import SYSTEM_PROMPT, build_prompt, model_contract_instructions
from scripts.reasoning_providers import MeterlessProvider, MockProvider, OpenAIConfig, OpenAIProvider
from scripts.reasoning_view import build_reasoning_view
from tests.test_meterless import MODEL, TOKEN, config
from tests.test_reasoning import model_content, valid_assessment
from tests.test_reasoning_contract import completed_bundle, serve


class ContractPresentationTests(unittest.TestCase):
    def setUp(self):
        self.bundle = completed_bundle()
        self.view = build_reasoning_view(self.bundle)
        self.prompt = build_prompt(self.view)
        self.content = model_content(valid_assessment(self.view))

    def reply(self, content):
        return {"id": "chat_presentation_fixture", "object": "chat.completion", "model": MODEL,
                "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": json.dumps(content)}}],
                "usage": {"completion_tokens": 111}}

    def checklist(self, schema=MODEL_OUTPUT_SCHEMA):
        return [line.removeprefix("- ").split(": ", 1) for line in model_contract_instructions(schema).splitlines()
                if line.startswith("- ")]

    def test_every_required_field_named_exactly_once_in_checklist(self):
        self.assertEqual([key for key, _ in self.checklist()], MODEL_OUTPUT_SCHEMA["required"])
        text = model_contract_instructions(MODEL_OUTPUT_SCHEMA)
        self.assertIn("Return exactly one JSON object", text)
        self.assertIn("No required field may be omitted or renamed", text)
        self.assertIn("Do not wrap", text)

    def test_hypothesis_explains_type_and_relationship_to_evidence(self):
        text = model_contract_instructions(MODEL_OUTPUT_SCHEMA)
        self.assertIn("assessed_hypothesis: a non-empty string", text)
        self.assertIn("specific security hypothesis", text)
        self.assertIn("supporting evidence and counterevidence", text)
        self.assertIn("not a declaration of compromise", text)

    def test_claims_explain_structure_selection_and_empty_array_policy(self):
        text = model_contract_instructions(MODEL_OUTPUT_SCHEMA)
        self.assertIn("factual_claims: an array", text)
        self.assertIn("selected from relationships.facts", text)
        self.assertIn("fact_id, kind, exact value", text)
        self.assertIn("complete evidence citations without changing types", text)
        self.assertIn("Do not put finding objects into this array", text)
        self.assertIn("field is mandatory even when [] is appropriate", text)
        self.assertIn("only when no supported checkable assertions", text)

    def test_interpretation_and_final_assessment_ownership_distinct(self):
        text = model_contract_instructions(MODEL_OUTPUT_SCHEMA)
        self.assertIn("model interpretation only", text)
        self.assertIn("ARGUS assembles the final assessment after validation", text)
        self.assertIn("Do not generate those ARGUS-owned fields", text)
        for key in ("alert_id", "schema_version", "collection_status", "evidence_complete"):
            self.assertNotIn(key, dict(self.checklist()))

    def test_nested_shapes_and_enums_come_from_schema(self):
        fields = dict(self.checklist())
        for key in ("key_findings", "counterevidence", "factual_claims", "alternative_explanations", "unknowns"):
            required = MODEL_OUTPUT_SCHEMA["properties"][key]["items"]["required"]
            self.assertEqual(fields[key], "array of objects with required keys [" + ", ".join(required) + "]")
        for key in ("classification", "confidence"):
            self.assertEqual(fields[key], "string; allowed values [" + ", ".join(MODEL_OUTPUT_SCHEMA["properties"][key]["enum"]) + "]")

    def test_schema_changes_are_not_hidden_by_a_second_field_inventory(self):
        schema = copy.deepcopy(MODEL_OUTPUT_SCHEMA)
        schema["properties"]["future_field"] = {"type": "array", "items": {"type": "integer"}}
        schema["required"].insert(0, "future_field")
        schema["properties"]["confidence"]["enum"] = ["TEST_ONLY"]
        fields = dict(self.checklist(schema))
        self.assertEqual(self.checklist(schema)[0], ["future_field", "array of integer"])
        self.assertEqual(fields["confidence"], "string; allowed values [TEST_ONLY]")
        self.assertNotIn("future_field", MODEL_OUTPUT_SCHEMA["properties"])

    def test_generation_is_deterministic_and_does_not_mutate_schema(self):
        before = copy.deepcopy(MODEL_OUTPUT_SCHEMA)
        self.assertEqual(model_contract_instructions(MODEL_OUTPUT_SCHEMA), model_contract_instructions(MODEL_OUTPUT_SCHEMA))
        self.assertEqual(MODEL_OUTPUT_SCHEMA, before)

    def test_checklist_has_no_fabricated_evidence_or_scenario_answer(self):
        text = model_contract_instructions(MODEL_OUTPUT_SCHEMA)
        for identifier in ("ALT0018", "AUTH000962", "AUTH000963", "N005", "DEV-U001-01", "F_SYNTH_SUCCESS"):
            self.assertNotIn(identifier, text)
        self.assertIn("actual records[].source table, id and variant_id", text)
        self.assertIn("never invent evidence or identifiers", text)
        self.assertIn("not IP similarity", text)
        self.assertIn("explicitly state uncertainty", text)

    def test_json_provider_sends_checklist_then_unchanged_full_schema_and_evidence(self):
        provider = MeterlessProvider(config(), output_mode="json_object", temperature=0)
        opener = serve(provider, self.reply(self.content))
        provider.generate(self.prompt, MODEL_OUTPUT_SCHEMA)
        body = json.loads(opener.open.call_args.args[0].data)
        system = body["messages"][0]["content"]
        self.assertEqual(system.count(model_contract_instructions(MODEL_OUTPUT_SCHEMA)), 1)
        self.assertTrue(system.startswith(SYSTEM_PROMPT))
        self.assertEqual(json.loads(system.rsplit("\n", 1)[1]), MODEL_OUTPUT_SCHEMA)
        self.assertEqual(body["messages"][1]["content"], self.prompt[1])
        self.assertEqual(body["response_format"], {"type": "json_object"})
        opener.open.assert_called_once()

    def test_mock_openai_and_strict_meterless_do_not_use_new_presentation(self):
        with patch("scripts.reasoning_providers.model_contract_instructions", side_effect=AssertionError("JSON mode only")):
            result = MockProvider().generate(self.prompt, MODEL_OUTPUT_SCHEMA)
            validate_model_content(json.loads(result.text), self.view)
            strict = MeterlessProvider(config())
            opener = serve(strict, self.reply(self.content))
            strict.generate(self.prompt, MODEL_OUTPUT_SCHEMA)
            body = json.loads(opener.open.call_args.args[0].data)
            self.assertNotIn("MODEL INTERPRETATION CONTRACT", body["messages"][0]["content"])
            self.assertEqual(body["response_format"]["json_schema"]["schema"], MODEL_OUTPUT_SCHEMA)
            openai = OpenAIProvider(OpenAIConfig(TOKEN, "configured-model"))
            opener = serve(openai, {"id": "response_fixture", "model": "configured-model", "status": "completed",
                "error": None, "incomplete_details": None, "output": [{"type": "message", "role": "assistant",
                "status": "completed", "content": [{"type": "output_text", "text": json.dumps(self.content)}]}]})
            openai.generate(self.prompt, MODEL_OUTPUT_SCHEMA)
            body = json.loads(opener.open.call_args.args[0].data)
            self.assertEqual(body["instructions"], SYSTEM_PROMPT)
            self.assertEqual(body["input"], self.prompt[1])
            self.assertEqual(body["text"]["format"]["schema"], MODEL_OUTPUT_SCHEMA)

    def test_missing_target_fields_still_fail_without_defaults_or_retry(self):
        for missing in (("assessed_hypothesis",), ("factual_claims",), ("assessed_hypothesis", "factual_claims")):
            value = copy.deepcopy(self.content)
            for field in missing: del value[field]
            provider = MeterlessProvider(config(), output_mode="json_object")
            opener = serve(provider, self.reply(value))
            with self.subTest(missing=missing), self.assertRaises(ReasoningFailure) as caught:
                reason_bundle(self.bundle, provider)
            report = caught.exception.report
            self.assertEqual(report["validation_stage"], "schema")
            self.assertFalse(report["validated_assessment_written"])
            paths = {e["path"] for e in report["details"]["errors"]}
            self.assertTrue({"assessment." + field for field in missing} <= paths)
            opener.open.assert_called_once()

    def test_citation_and_factual_rejections_are_unchanged(self):
        for stage in ("citations_and_gaps", "factual_consistency"):
            value = copy.deepcopy(self.content)
            if stage == "citations_and_gaps": value["supporting_evidence"][0]["id"] = "fabricated"
            else: value["factual_claims"][0]["value"] = "contradiction"
            provider = MeterlessProvider(config(), output_mode="json_object")
            opener = serve(provider, self.reply(value))
            with self.subTest(stage=stage), self.assertRaises(ReasoningFailure) as caught:
                reason_bundle(self.bundle, provider)
            self.assertEqual(caught.exception.report["validation_stage"], stage)
            self.assertFalse(caught.exception.report["validated_assessment_written"])
            opener.open.assert_called_once()


if __name__ == "__main__":
    unittest.main()
