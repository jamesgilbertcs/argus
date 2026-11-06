"""Mocked Nightshift HTTP only. No runtime/token files or live inference in tests."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler

from scripts.check_meterless import main as check_main
from scripts.evidence_collector import serialise
from scripts.reason_evidence import main, reason_bundle
from scripts.reasoning_assessment import ASSESSMENT_SCHEMA
from scripts.reasoning_prompts import SYSTEM_PROMPT, build_prompt
from scripts.reasoning_providers import MeterlessConfig, MeterlessProvider, ProviderError, _NoRedirects
from scripts.reasoning_view import ReasoningError, build_reasoning_view
from tests.test_reasoning import fixture, valid_assessment


TOKEN = "unit-test-runtime-secret"
MODEL = "nvidia/nemotron-3.5-bolt-e4b"
BASE = "http://127.0.0.1:8088/v1"


def config(**changes) -> MeterlessConfig:
    return MeterlessConfig(**({"base_url": BASE, "runtime_token": TOKEN, "model": MODEL} | changes))


class MeterlessConfigurationTests(unittest.TestCase):
    def test_file_configuration_environment_precedence_without_openai_or_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.local"
            original = f"METERLESS_BASE_URL={BASE}\nMETERLESS_RUNTIME_TOKEN={TOKEN}\nMETERLESS_MODEL={MODEL}\n"
            path.write_text(original, encoding="utf-8")
            before = dict(os.environ)
            result = MeterlessConfig.from_environment(path, environ={"METERLESS_MODEL": "nvidia/test-model"})
            self.assertEqual(result.model, "nvidia/test-model")
            self.assertEqual(result.runtime_token, TOKEN)
            self.assertEqual(result.timeout_seconds, 180)
            self.assertEqual(result.max_output_tokens, 6000)
            self.assertNotIn(TOKEN, repr(result))
            self.assertEqual(dict(os.environ), before)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_environment_only_and_explicit_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            result = MeterlessConfig.from_environment(Path(directory) / "missing", environ={
                "METERLESS_BASE_URL": BASE, "METERLESS_RUNTIME_TOKEN": TOKEN, "METERLESS_MODEL": MODEL,
                "ARGUS_INFERENCE_TIMEOUT_SECONDS": "240", "ARGUS_MAX_OUTPUT_TOKENS": "8000"})
        self.assertEqual(result.timeout_seconds, 240)
        self.assertEqual(result.max_output_tokens, 8000)

    def test_each_missing_setting_fails_clearly_without_secrets(self):
        settings = {"METERLESS_BASE_URL": BASE, "METERLESS_RUNTIME_TOKEN": TOKEN, "METERLESS_MODEL": MODEL}
        with tempfile.TemporaryDirectory() as directory:
            for name in settings:
                with self.subTest(name=name), self.assertRaises(ProviderError) as caught:
                    MeterlessConfig.from_environment(Path(directory) / "missing", environ={k: v for k, v in settings.items() if k != name})
                self.assertIn(name, str(caught.exception))
                self.assertNotIn(TOKEN, str(caught.exception))

    def test_origin_or_v1_normalised_without_double_v1(self):
        for base in ("http://127.0.0.1:8088", BASE, BASE + "/"):
            self.assertEqual(config(base_url=base).base_url, BASE)
        self.assertEqual(config(base_url="http://[::1]:8088/v1").base_url, "http://[::1]:8088/v1")
        self.assertEqual(config(base_url="https://localhost:8088/v1").base_url, "https://localhost:8088/v1")

    def test_unsafe_urls_credentials_and_paths_are_refused(self):
        for base in (f"http://user:{TOKEN}@localhost:8088/v1", f"{BASE}?token={TOKEN}", BASE + "#secret",
                     "http://192.0.2.1:8088/v1", "file:///v1", BASE + "/v1", BASE + "/chat/completions",
                     "http://localhost:70000/v1", "http://localhost:0/v1", "http://local\nhost:8088/v1"):
            with self.subTest(base_kind=base.split(":")[0]), self.assertRaises(ProviderError) as caught:
                config(base_url=base)
            self.assertNotIn(TOKEN, str(caught.exception))

    def test_bad_tokens_models_and_numeric_limits_are_refused(self):
        for changes in ({"runtime_token": "rt-local"}, {"runtime_token": "<placeholder>"}, {"runtime_token": "bad\nkey"},
                        {"model": ""}, {"model": "has whitespace"}, {"model": TOKEN}, {"timeout_seconds": 0},
                        {"timeout_seconds": float("nan")}, {"timeout_seconds": 601}, {"max_output_tokens": True},
                        {"max_output_tokens": 100}, {"max_output_tokens": 32001}):
            with self.subTest(fields=list(changes)), self.assertRaises(ProviderError): config(**changes)

    def test_bad_env_syntax_and_numbers_do_not_echo_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.local"
            path.write_text(f"invalid syntax {TOKEN}", encoding="utf-8")
            with self.assertRaises(ProviderError) as caught: MeterlessConfig.from_environment(path, environ={})
            self.assertNotIn(TOKEN, str(caught.exception))
            path.unlink()
            with self.assertRaises(ProviderError) as caught:
                MeterlessConfig.from_environment(path, environ={"ARGUS_MAX_OUTPUT_TOKENS": TOKEN})
            self.assertNotIn(TOKEN, str(caught.exception))


class MeterlessProviderTests(unittest.TestCase):
    def setUp(self):
        self.bundle = fixture()
        self.view = build_reasoning_view(self.bundle)
        self.provider = MeterlessProvider(config())
        self.opener = Mock()
        self.provider._opener = self.opener

    def response(self, **changes):
        assessment = valid_assessment(self.view)
        assessment["executive_summary"] = "Unit-test response, not a live model assessment."
        return {"id": "chat_test", "object": "chat.completion", "model": MODEL, "created": 123,
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(assessment)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 200}, **changes}

    def serve(self, payload, *, status=200):
        self.wire_response = Mock()
        self.wire_response.status = status
        self.wire_response.read.return_value = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.opener.open.return_value.__enter__ = Mock(return_value=self.wire_response)
        self.opener.open.return_value.__exit__ = Mock(return_value=False)

    def generate(self):
        return self.provider.generate(build_prompt(self.view), ASSESSMENT_SCHEMA)

    def test_authenticated_endpoint_model_messages_schema_and_limits(self):
        self.serve(self.response())
        reply = self.generate()
        request = self.opener.open.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, BASE + "/chat/completions")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertEqual(request.get_header("Meterless-app"), "argus")
        self.assertEqual(request.get_header("Meterless-scope"), "user")
        self.assertEqual(body["model"], MODEL)
        self.assertEqual(body["max_tokens"], 6000)
        self.assertFalse(body["stream"])
        self.assertTrue(body["messages"][0]["content"].startswith(SYSTEM_PROMPT))
        self.assertEqual(json.loads(body["messages"][0]["content"].split("Match this JSON Schema exactly:\n")[1]), ASSESSMENT_SCHEMA)
        self.assertEqual(body["messages"][1], {"role": "user", "content": build_prompt(self.view)[1]})
        self.assertNotIn("response_format", body)
        self.assertNotIn("tools", body)
        self.assertEqual(self.opener.open.call_args.kwargs["timeout"], 180)
        self.assertEqual(reply.provider, "meterless")
        self.assertEqual(reply.model, MODEL)
        self.assertFalse(reply.mock_generated)
        self.assertEqual(reply.metadata["output_mode"], "prompt_json")

    def test_existing_assessment_pipeline_validates_and_keeps_source_identifiers(self):
        self.serve(self.response())
        view, result = reason_bundle(self.bundle, self.provider)
        self.assertEqual(result["assessment"]["alert_id"], self.bundle["alert_id"])
        self.assertIn(result["assessment"]["supporting_evidence"][0], [r["source"] for r in view["records"]])
        self.assertEqual(result["provider"]["name"], "meterless")

    def test_fabricated_citations_rejected_after_inference(self):
        payload = self.response()
        value = json.loads(payload["choices"][0]["message"]["content"])
        value["supporting_evidence"][0]["id"] = "FABRICATED"
        payload["choices"][0]["message"]["content"] = json.dumps(value)
        self.serve(payload)
        with self.assertRaises(ReasoningError): reason_bundle(self.bundle, self.provider)

    def test_malformed_model_json_rejected_by_existing_parser(self):
        for content in ("not JSON", "```json\n{}\n```", "{}"):
            payload = self.response()
            payload["choices"][0]["message"]["content"] = content
            self.serve(payload)
            with self.subTest(content=content), self.assertRaises(ReasoningError): reason_bundle(self.bundle, self.provider)

    def test_malformed_wire_json_utf8_and_duplicates(self):
        for payload in (b"not JSON", b"\xff", b'{"choices":[],"choices":[]}'):
            self.serve(payload)
            with self.assertRaises(ProviderError): self.generate()

    def test_invalid_response_shapes(self):
        for payload in (None, [], {}, self.response(choices=None), self.response(choices=[]),
                        self.response(choices=[None]), self.response(object="other"), self.response(id=None),
                        self.response(error={"message": "error"}), self.response(choices=self.response()["choices"] * 2)):
            self.serve(payload)
            with self.assertRaises(ProviderError): self.generate()

    def test_changed_model_is_rejected_without_fallback(self):
        self.serve(self.response(model="nvidia/another-model"))
        with self.assertRaises(ProviderError): self.generate()
        self.opener.open.assert_called_once()

    def test_missing_output_and_invalid_messages(self):
        for message in (None, {}, {"role": "user", "content": "text"}, {"role": "assistant", "content": None},
                        {"role": "assistant", "content": " "}, {"role": "assistant", "content": []}):
            payload = self.response()
            payload["choices"][0]["message"] = message
            self.serve(payload)
            with self.assertRaises(ProviderError): self.generate()

    def test_unsuccessful_termination_and_tool_requests_are_refused(self):
        for reason in ("length", "content_filter", "tool_calls", None, "unexpected"):
            payload = self.response()
            payload["choices"][0]["finish_reason"] = reason
            self.serve(payload)
            with self.subTest(reason=reason), self.assertRaises(ProviderError): self.generate()
        for extra in ({"tool_calls": [{"function": "run_sql"}]}, {"function_call": {"name": "command"}}, {"refusal": "no"}):
            payload = self.response()
            payload["choices"][0]["message"].update(extra)
            self.serve(payload)
            with self.assertRaises(ProviderError): self.generate()

    def test_http_errors_are_safe_without_retry_or_downgrade(self):
        for code in (401, 403, 429, 500, 302):
            self.opener.open.reset_mock()
            self.opener.open.side_effect = HTTPError(BASE, code, TOKEN, {}, io.BytesIO(TOKEN.encode()))
            with self.subTest(code=code), self.assertRaises(ProviderError) as caught: self.generate()
            self.assertNotIn(TOKEN, str(caught.exception))
            self.assertTrue(caught.exception.__suppress_context__)
            self.opener.open.assert_called_once()

    def test_unexpected_http_status(self):
        self.serve(self.response(), status=202)
        with self.assertRaises(ProviderError): self.generate()

    def test_connection_and_timeout_failures_are_safe(self):
        for error in (URLError(TOKEN), TimeoutError(TOKEN), OSError(TOKEN)):
            self.opener.open.side_effect = error
            with self.assertRaises(ProviderError) as caught: self.generate()
            self.assertNotIn(TOKEN, str(caught.exception))

    def test_timeout_during_read_is_handled(self):
        self.serve(self.response())
        self.wire_response.read.side_effect = TimeoutError(TOKEN)
        with self.assertRaises(ProviderError) as caught: self.generate()
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_response_size_is_bounded_without_truncation(self):
        self.serve(b"x" * (self.provider.MAX_RESPONSE_BYTES + 1))
        with self.assertRaises(ProviderError): self.generate()
        self.wire_response.read.assert_called_once_with(self.provider.MAX_RESPONSE_BYTES + 1)

    def test_configured_credentials_in_input_or_response_are_not_exposed(self):
        with self.assertRaises(ProviderError) as caught:
            self.provider.generate((SYSTEM_PROMPT, TOKEN), ASSESSMENT_SCHEMA)
        self.opener.open.assert_not_called()
        self.assertNotIn(TOKEN, str(caught.exception))
        self.serve(self.response(id=TOKEN))
        with self.assertRaises(ProviderError) as caught: self.generate()
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_no_proxies_or_redirects(self):
        with patch("scripts.reasoning_providers.build_opener") as build:
            MeterlessProvider(config())
        handlers = build.call_args.args
        self.assertTrue(any(isinstance(h, ProxyHandler) and h.proxies == {} for h in handlers))
        redirect = next(h for h in handlers if isinstance(h, _NoRedirects))
        self.assertIsNone(redirect.redirect_request(None, None, 302, None, None, "https://example.invalid"))

    def test_authenticated_model_discovery_preserves_identifiers(self):
        self.serve({"object": "list", "data": [{"id": "meterless/future-mode"}, {"id": MODEL}]})
        self.assertEqual(self.provider.available_models(), ("meterless/future-mode", MODEL))
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, BASE + "/models")
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertIsNone(request.data)

    def test_invalid_model_discovery_shape(self):
        for payload in ({}, {"object": "list", "data": [None]}, {"object": "list", "data": [{"id": None}]}):
            self.serve(payload)
            with self.assertRaises(ProviderError): self.provider.available_models()


class MeterlessCLITests(unittest.TestCase):
    def test_meterless_selection_no_openai_or_zetaris_required_and_source_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bundle.json"
            original = serialise(fixture())
            source.write_text(original, encoding="utf-8")
            provider = MeterlessProvider(config())
            response = MeterlessProviderTests()
            response.view = build_reasoning_view(fixture())
            payload = response.response()
            reply = Mock()
            reply.status = 200
            reply.read.return_value = json.dumps(payload).encode()
            provider._opener = Mock()
            provider._opener.open.return_value.__enter__ = Mock(return_value=reply)
            provider._opener.open.return_value.__exit__ = Mock(return_value=False)
            with patch("scripts.reason_evidence.MeterlessConfig.from_environment", return_value=config()), \
                 patch("scripts.reason_evidence.MeterlessProvider", return_value=provider), \
                 patch("scripts.reason_evidence.OpenAIConfig.from_environment", side_effect=AssertionError("Not OpenAI")), \
                 patch("scripts.reason_evidence.MockProvider", side_effect=AssertionError("No fallback")), \
                 patch("sys.stdout", new=io.StringIO()) as output:
                self.assertEqual(main([str(source), "--provider", "meterless", "--output-dir", str(root / "out")]), 0)
            result = json.loads((root / "out" / "ALERT_SAMPLE.meterless.assessment.json").read_text(encoding="utf-8"))
            self.assertEqual(result["provider"]["name"], "meterless")
            self.assertFalse(result["provider"]["mock_generated"])
            self.assertIn(MODEL, output.getvalue())
            self.assertIn("prompt_json", output.getvalue())
            self.assertNotIn(TOKEN, output.getvalue())
            self.assertNotIn(TOKEN, serialise(result))
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            self.assertTrue((root / "out" / "ALERT_SAMPLE.meterless.view.json").exists())

    def test_meterless_config_error_no_mock_or_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bundle.json"
            source.write_text(serialise(fixture()), encoding="utf-8")
            with patch("scripts.reason_evidence.MeterlessConfig.from_environment", side_effect=ProviderError("Missing METERLESS_RUNTIME_TOKEN")), \
                 patch("scripts.reason_evidence.MockProvider") as mock, patch("sys.stdout", new=io.StringIO()) as output:
                self.assertEqual(main([str(source), "--provider", "meterless", "--output-dir", str(root / "out")]), 1)
            mock.assert_not_called()
            self.assertIn("METERLESS_RUNTIME_TOKEN", output.getvalue())
            self.assertFalse((root / "out").exists())

    def test_check_command_discovery_and_explicit_small_inference(self):
        with patch("scripts.check_meterless.MeterlessConfig.from_environment", return_value=config()), \
             patch("scripts.check_meterless.MeterlessProvider") as adapter, patch("sys.stdout", new=io.StringIO()):
            provider = adapter.return_value
            provider.available_models.return_value = (MODEL,)
            provider.generate.return_value = Mock(text='{"ok":true}')
            self.assertEqual(check_main([]), 0)
            provider.generate.assert_not_called()
            self.assertEqual(check_main(["--infer"]), 0)
            provider.generate.assert_called_once()
            self.assertEqual(adapter.call_args.args[0].max_output_tokens, 256)

    def test_check_command_refuses_unadvertised_model(self):
        with patch("scripts.check_meterless.MeterlessConfig.from_environment", return_value=config()), \
             patch("scripts.check_meterless.MeterlessProvider") as adapter, patch("sys.stdout", new=io.StringIO()):
            adapter.return_value.available_models.return_value = ("different-model",)
            self.assertEqual(check_main(["--infer"]), 1)
            adapter.return_value.generate.assert_not_called()

    def test_check_command_requires_exact_boolean_result(self):
        with patch("scripts.check_meterless.MeterlessConfig.from_environment", return_value=config()), \
             patch("scripts.check_meterless.MeterlessProvider") as adapter, patch("sys.stdout", new=io.StringIO()):
            adapter.return_value.available_models.return_value = (MODEL,)
            for text in ('{"ok":1}', '{"ok":false}', '{}', '{"ok":true,"extra":1}'):
                adapter.return_value.generate.return_value = Mock(text=text)
                with self.subTest(text=text): self.assertEqual(check_main(["--infer"]), 1)


if __name__ == "__main__":
    unittest.main()
