"""Explicit inference adapters. No provider has tool capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

if __package__:
    from .reasoning_assessment import strict_json
    from .reasoning_prompts import model_contract_instructions
    from .reasoning_view import ReasoningError
    from .zetaris_client import ROOT, ZetarisError, _read_settings
else:
    from reasoning_assessment import strict_json
    from reasoning_prompts import model_contract_instructions
    from reasoning_view import ReasoningError
    from zetaris_client import ROOT, ZetarisError, _read_settings


class ProviderError(ReasoningError):
    pass


class InferenceTerminationError(ProviderError):
    """Rejected completion, retaining only bounded, allowlisted diagnostics."""

    def __init__(self, metadata: dict, *, model: str, max_output_tokens: int, response_text: str | None = None) -> None:
        if __package__:
            from .reasoning_diagnostics import inference_metadata
        else:
            from reasoning_diagnostics import inference_metadata
        self.inference = inference_metadata(metadata, model)
        self.model = model
        self.response_text = response_text
        self.inference.update({"requested_model": model, "actual_model": model,
                               "max_output_tokens": max_output_tokens})
        super().__init__("Meterless output did not finish successfully; expected one stopped completion")


class StructuredOutputError(ProviderError):
    """Typed format failure; no upstream message, raw body or headers retained."""

    def __init__(self, code: str, *, model: str, metadata: dict, http_status: int, actual_model: str = "") -> None:
        if __package__:
            from .reasoning_diagnostics import inference_metadata
        else:
            from reasoning_diagnostics import inference_metadata
        self.model = model
        self.response_text = None
        self.details = {"check": code, "http_status": http_status}
        self.inference = inference_metadata(metadata, actual_model)
        label = "strict structured" if metadata.get("output_mode") == "strict_json_schema" else "JSON"
        super().__init__("Meterless " + label + " output failed: " + code)


@dataclass(frozen=True)
class InferenceResponse:
    text: str = field(repr=False)
    provider: str
    model: str
    mock_generated: bool
    metadata: dict[str, Any] = field(default_factory=dict)


class ReasoningProvider(Protocol):
    def generate(self, prompt: tuple[str, str], schema: dict) -> InferenceResponse: ...


class MockProvider:
    """Deterministic plumbing fixture, with no security classification logic."""

    def generate(self, prompt: tuple[str, str], schema: dict) -> InferenceResponse:
        view = strict_json(prompt[1])["untrusted_evidence"]
        alerts = [r for r in view["records"] if r["source"]["table"] == "ARGUS_SECURITY.security_alerts_compat_test"]
        evidence = [r["source"] for r in (alerts[:1] or view["records"][:1])]
        assessment = {
            "schema_version": "2.0", "alert_id": view["alert_id"],
            "assessed_hypothesis": "The alert describes activity warranting a security investigation; this mock does not assess it.",
            "factual_claims": [{key: fact[key] for key in ("fact_id", "kind", "value", "evidence")}
                               for fact in view["relationships"]["facts"] if fact["required"]],
            "classification": "INCONCLUSIVE", "confidence": "LOW",
            "executive_summary": "MOCK-GENERATED: pipeline demonstration only. No model interpretation or incident conclusion was produced.",
            "key_findings": ([{"kind": "OBSERVED_FACT", "statement": "A source record was supplied for review.",
                              "evidence": evidence}] if evidence else []),
            "supporting_evidence": evidence, "counterevidence": [],
            "alternative_explanations": [{"explanation": "Hypothetically, legitimate activity or misuse could explain an alert; this mock does not evaluate either.",
                                          "evidence": [], "verification_needed": "Human review of the supplied evidence is required."}],
            "unknowns": [{"gap_ids": [g["gap_id"]], "description": "MOCK acknowledgement: " + g["kind"] + "; review its details in the reasoning view."}
                         for g in view["gaps"]],
            "recommended_next_steps": ["Have a human analyst review the evidence and its limitations."],
            "collection_status": view["collection_status"], "evidence_complete": False,
        }
        # Honor the supplied contract, including historical test/archive callers.
        # New inference does not echo collection metadata or a mandatory fact list.
        if "collection_status" not in schema.get("properties", {}):
            for key in ("schema_version", "alert_id", "collection_status", "evidence_complete"):
                del assessment[key]
            assessment["factual_claims"] = []
            assessment["unknowns"] = [{"gap_ids": [], "description": "MOCK: investigative sufficiency has not been assessed; consult ARGUS collection limitations."}]
        return InferenceResponse(json.dumps(assessment, ensure_ascii=False), "mock", "deterministic-mock-v1", True)


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float = 60
    max_output_tokens: int = 6000

    def __post_init__(self) -> None:
        if (not isinstance(self.api_key, str) or not self.api_key or self.api_key.startswith("<")
                or any(not 33 <= ord(c) <= 126 for c in self.api_key)):
            raise ProviderError("A valid OPENAI_API_KEY is required for the openai provider")
        if not isinstance(self.model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", self.model):
            raise ProviderError("ARGUS_OPENAI_MODEL must name a Structured Outputs compatible model")
        if self.api_key in self.model:
            raise ProviderError("Invalid inference configuration")
        if (type(self.timeout_seconds) not in (int, float) or not math.isfinite(self.timeout_seconds)
                or not 1 <= self.timeout_seconds <= 600):
            raise ProviderError("Inference timeout must be between 1 and 600 seconds")
        if type(self.max_output_tokens) is not int or not 256 <= self.max_output_tokens <= 32_000:
            raise ProviderError("Inference output token limit must be between 256 and 32000")

    @classmethod
    def from_environment(cls, env_file: Path = ROOT / ".env.local", *, environ: Mapping[str, str] | None = None) -> OpenAIConfig:
        try:
            settings = _read_settings(env_file)
            settings.update(dict(os.environ if environ is None else environ))
            return cls(settings.get("OPENAI_API_KEY", ""), settings.get("ARGUS_OPENAI_MODEL", ""),
                       float(settings.get("ARGUS_INFERENCE_TIMEOUT_SECONDS", "60")),
                       int(settings.get("ARGUS_MAX_OUTPUT_TOKENS", "6000")))
        except (ZetarisError, ValueError, TypeError) as error:
            if isinstance(error, ProviderError):
                raise
            raise ProviderError("Cannot load inference configuration") from None


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OpenAIProvider:
    """Verified Responses REST protocol; explicit invocation sends evidence to OpenAI.

    Fixed HTTPS origin, no redirects, environment proxies, retries, sessions,
    stored responses, tools, or arbitrary URL configuration. No SDK dependency.
    """

    ENDPOINT = "https://api.openai.com/v1/responses"
    MAX_RESPONSE_BYTES = 2_097_152

    def __init__(self, config: OpenAIConfig) -> None:
        self.config = config
        self._opener = build_opener(ProxyHandler({}), _NoRedirects())

    def generate(self, prompt: tuple[str, str], schema: dict) -> InferenceResponse:
        body = json.dumps({"model": self.config.model, "instructions": prompt[0], "input": prompt[1],
                           "store": False, "max_output_tokens": self.config.max_output_tokens,
                           "text": {"format": {"type": "json_schema", "name": "argus_assessment",
                                               "strict": True, "schema": schema}}}, ensure_ascii=False)
        if self.config.api_key in body:
            raise ProviderError("Inference payload refused because it contains a configured credential")
        request = Request(self.ENDPOINT, body.encode("utf-8"), method="POST",
                          headers={"Authorization": "Bearer " + self.config.api_key, "Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=self.config.timeout_seconds) as response:
                if response.status != 200:
                    raise ProviderError("Inference service returned an unexpected HTTP status")
                raw = response.read(self.MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            error.close()
            if error.code in (401, 403):
                raise ProviderError("Inference authentication or authorisation failed") from None
            raise ProviderError("Inference HTTP request failed") from None
        except (TimeoutError, URLError, OSError, HTTPException):
            raise ProviderError("Inference connection failed or timed out") from None
        if len(raw) > self.MAX_RESPONSE_BYTES:
            raise ProviderError("Inference response exceeds the byte limit")
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            raise ProviderError("Inference response is not UTF-8") from None
        if self.config.api_key in text:
            raise ProviderError("Inference response refused because it contains a configured credential")
        try:
            payload = strict_json(text)
        except ReasoningError:
            raise ProviderError("Inference service returned malformed JSON") from None
        return self._parse_response(payload)

    def _parse_response(self, payload: Any) -> InferenceResponse:
        if (type(payload) is not dict or payload.get("status") != "completed" or payload.get("error") is not None
                or payload.get("incomplete_details") is not None or type(payload.get("output")) is not list
                or type(payload.get("id")) is not str or type(payload.get("model")) is not str):
            raise ProviderError("Inference response is incomplete, failed or has an unexpected structure")
        texts = []
        for item in payload["output"]:
            if type(item) is not dict:
                raise ProviderError("Unexpected inference output structure")
            if item.get("type") == "reasoning":
                # Provider-private reasoning is not evidence or a user assessment.
                continue
            if (item.get("type") != "message" or item.get("role") != "assistant"
                    or item.get("status") != "completed" or type(item.get("content")) is not list):
                raise ProviderError("Unexpected inference output; tool actions are not supported")
            for content in item["content"]:
                if type(content) is not dict or content.get("type") != "output_text" or type(content.get("text")) is not str:
                    raise ProviderError("Inference refused the request or returned unsupported content")
                texts.append(content["text"])
        if len(texts) != 1 or not texts[0].strip():
            raise ProviderError("Inference must return exactly one structured assessment")
        # Preserve operational provider metadata, without exposing raw error text,
        # private reasoning, tool payloads, or other unvalidated response content.
        metadata = {key: payload[key] for key in ("id", "usage", "service_tier") if key in payload}
        metadata.update({"requested_model": self.config.model, "output_mode": "strict_json_schema",
                         "finish_reason": None, "response_status": payload["status"],
                         "max_output_tokens": self.config.max_output_tokens})
        return InferenceResponse(texts[0], "openai", payload["model"], False, metadata)


@dataclass(frozen=True)
class MeterlessConfig:
    base_url: str
    runtime_token: str = field(repr=False)
    model: str
    timeout_seconds: float = 180
    max_output_tokens: int = 6000

    def __post_init__(self) -> None:
        try:
            parts = urlsplit(self.base_url)
            valid_url = (parts.scheme in ("http", "https") and parts.hostname in ("127.0.0.1", "localhost", "::1")
                         and parts.username is None and parts.password is None and not parts.query and not parts.fragment
                         and parts.path.rstrip("/") in ("", "/v1") and parts.port != 0
                         and not any(c.isspace() or ord(c) < 32 for c in self.base_url))
            if not valid_url:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise ProviderError("METERLESS_BASE_URL must be an HTTP(S) loopback origin, optionally ending in /v1") from None
        object.__setattr__(self, "base_url", f"{parts.scheme}://{parts.netloc}/v1")
        if (not isinstance(self.runtime_token, str) or not self.runtime_token or self.runtime_token.startswith("<")
                or self.runtime_token == "rt-local" or any(not 33 <= ord(c) <= 126 for c in self.runtime_token)):
            raise ProviderError("A valid METERLESS_RUNTIME_TOKEN is required for the meterless provider")
        if not isinstance(self.model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", self.model):
            raise ProviderError("METERLESS_MODEL must specify the exact runtime model identifier")
        if self.runtime_token in self.base_url or self.runtime_token in self.model:
            raise ProviderError("Invalid Meterless configuration")
        if (type(self.timeout_seconds) not in (int, float) or not math.isfinite(self.timeout_seconds)
                or not 1 <= self.timeout_seconds <= 600):
            raise ProviderError("Inference timeout must be between 1 and 600 seconds")
        if type(self.max_output_tokens) is not int or not 256 <= self.max_output_tokens <= 32_000:
            raise ProviderError("Inference output token limit must be between 256 and 32000")

    @classmethod
    def from_environment(cls, env_file: Path = ROOT / ".env.local", *, environ: Mapping[str, str] | None = None) -> MeterlessConfig:
        try:
            settings = _read_settings(env_file)
            settings.update(dict(os.environ if environ is None else environ))
            return cls(settings.get("METERLESS_BASE_URL", ""), settings.get("METERLESS_RUNTIME_TOKEN", ""),
                       settings.get("METERLESS_MODEL", ""), float(settings.get("ARGUS_INFERENCE_TIMEOUT_SECONDS", "180")),
                       int(settings.get("ARGUS_MAX_OUTPUT_TOKENS", "6000")))
        except (ZetarisError, ValueError, TypeError) as error:
            if isinstance(error, ProviderError):
                raise
            raise ProviderError("Cannot load Meterless configuration") from None


class MeterlessProvider:
    """Strict schema by default; explicit JSON mode still requires ARGUS validation.

    Verified small-schema route: Nightshift 1.2.7 / NVIDIA Nemotron Bolt E4B.
    Other runtimes/models must support this mode; no fallback or downgrade occurs.
    """

    MAX_RESPONSE_BYTES = 2_097_152
    OUTPUT_MODE = "strict_json_schema"
    SCHEMA_NAME = "argus_assessment"
    STRUCTURED_ERRORS = frozenset({"structured_output_unsupported", "structured_output_schema_rejected",
                                   "invalid_json_output", "schema_validation_failed", "response_truncated", "response_refused"})

    def __init__(self, config: MeterlessConfig, *, output_mode: str = "json_schema",
                 temperature: float | None = None) -> None:
        if output_mode not in ("json_schema", "json_object"):
            raise ProviderError("Meterless output mode must be json_schema or json_object")
        if (temperature is not None and (type(temperature) not in (int, float)
                or not 0 <= temperature <= 2 or not math.isfinite(temperature))):
            raise ProviderError("Meterless temperature must be a finite number between 0 and 2")
        self.config = config
        self.output_mode = output_mode
        self.temperature = temperature
        self.inference_output_mode = self.OUTPUT_MODE if output_mode == "json_schema" else "json_object"
        self._opener = build_opener(ProxyHandler({}), _NoRedirects())

    def _request_json(self, path: str, payload: dict | None = None) -> Any:
        body = json.dumps(payload, ensure_ascii=False) if payload is not None else None
        if body is not None and self.config.runtime_token in body:
            raise ProviderError("Meterless payload refused because it contains a configured credential")
        headers = {"Authorization": "Bearer " + self.config.runtime_token, "meterless-app": "argus",
                   "Accept": "application/json"}
        if body is not None:
            headers.update({"Content-Type": "application/json", "meterless-scope": "user"})
        request = Request(self.config.base_url + path, data=body.encode("utf-8") if body is not None else None,
                          method="POST" if body is not None else "GET", headers=headers)
        try:
            with self._opener.open(request, timeout=self.config.timeout_seconds) as response:
                if response.status != 200:
                    raise ProviderError("Meterless returned an unexpected HTTP status")
                raw = response.read(self.MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            # Read only a bounded body to recognise fixed runtime error codes.
            # Never retain upstream text, headers, arbitrary codes or schema paths.
            code = None
            failure_metadata = self._metadata(http_status=error.code)
            try:
                if error.code not in (401, 403):
                    raw_error = error.read(self.MAX_RESPONSE_BYTES + 1)
                    if len(raw_error) <= self.MAX_RESPONSE_BYTES and self.config.runtime_token.encode() not in raw_error:
                        payload_error = strict_json(raw_error.decode("utf-8"))
                        candidate = payload_error.get("error") if type(payload_error) is dict else None
                        if type(candidate) is dict and type(candidate.get("code")) is str and candidate["code"] in self.STRUCTURED_ERRORS:
                            code = candidate["code"]
                            failure_metadata = self._error_metadata(payload_error, error.code, code)
            except (ReasoningError, UnicodeError, OSError, HTTPException):
                pass
            finally:
                error.close()
            if code is not None:
                raise StructuredOutputError(code, model=self.config.model,
                                            metadata=failure_metadata, http_status=error.code) from None
            if error.code in (401, 403):
                raise ProviderError("Meterless authentication or authorisation failed") from None
            raise ProviderError("Meterless HTTP request failed") from None
        except (TimeoutError, URLError, OSError, HTTPException):
            raise ProviderError("Meterless connection failed or timed out") from None
        if len(raw) > self.MAX_RESPONSE_BYTES:
            raise ProviderError("Meterless response exceeds the byte limit")
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            raise ProviderError("Meterless response is not UTF-8") from None
        if self.config.runtime_token in text:
            raise ProviderError("Meterless response refused because it contains a configured credential")
        try:
            return strict_json(text)
        except ReasoningError:
            raise ProviderError("Meterless returned malformed JSON") from None

    def _metadata(self, *, http_status: int, payload: dict | None = None) -> dict:
        metadata = {key: payload[key] for key in ("id", "created", "usage") if payload is not None and key in payload}
        metadata.update({"output_mode": self.inference_output_mode, "requested_model": self.config.model,
                         "max_output_tokens": self.config.max_output_tokens, "http_status": http_status,
                         "meterless": {"structured_output": {"availability": "unavailable",
                             "requested_kind": self.output_mode, "requested_strict": self.output_mode == "json_schema"}}})
        if self.temperature is not None:
            metadata["requested_temperature"] = self.temperature
        return metadata

    def _error_metadata(self, payload: dict, http_status: int, code: str) -> dict:
        if __package__:
            from .reasoning_diagnostics import runtime_schema_diagnostics
        else:
            from reasoning_diagnostics import runtime_schema_diagnostics
        metadata = self._metadata(http_status=http_status)
        # Only the diagnostic normalizer will consume usage/termination fields;
        # it retains known finish reasons and bounded integer token counts.
        metadata["usage"] = payload.get("usage")
        container = payload.get("meterless")
        validation = container.get("validation") if type(container) is dict else None
        if type(validation) is dict:
            metadata["finish_reason"] = validation.get("finish_reason")
        metadata["meterless"]["structured_output"]["reported_error_code"] = code
        metadata["meterless"]["structured_output"]["runtime_validation"] = runtime_schema_diagnostics(validation)
        return metadata

    def _structured_metadata(self, payload: dict, metadata: dict) -> None:
        container = payload.get("meterless")
        if container is None:
            return
        if type(container) is not dict:
            raise StructuredOutputError("INVALID_STRUCTURED_METADATA", model=self.config.model, metadata=metadata,
                                        http_status=200, actual_model=self.config.model)
        raw = container.get("structured_output")
        if raw is None:
            return
        strict = self.output_mode == "json_schema"
        names = (None, self.SCHEMA_NAME) if strict else (None,)
        if (type(raw) is not dict or raw.get("kind") != self.output_mode or raw.get("strict") is not strict
                or raw.get("validation") != "passed" or raw.get("capability") not in (None, "unverified", "verified")
                or raw.get("name") not in names
                or "schema_fingerprint" in raw and (type(raw["schema_fingerprint"]) is not str
                    or re.fullmatch(r"[0-9a-f]{16}", raw["schema_fingerprint"]) is None)):
            raise StructuredOutputError("INVALID_OR_REJECTED_STRUCTURED_METADATA", model=self.config.model,
                                        metadata=metadata, http_status=200, actual_model=self.config.model)
        keys = {"kind", "strict", "validation", "capability", "name", "schema_fingerprint"}
        admitted = {key: raw[key] for key in sorted(keys) if key in raw}
        admitted["availability"] = "available"
        admitted.update({"requested_kind": self.output_mode, "requested_strict": strict})
        if raw.keys() - keys:
            admitted["omitted_field_count"] = len(raw.keys() - keys)
        metadata["meterless"]["structured_output"] = admitted

    def available_models(self) -> tuple[str, ...]:
        """Explicit authenticated discovery; generate() never changes the model."""
        payload = self._request_json("/models")
        if (type(payload) is not dict or payload.get("object") != "list" or type(payload.get("data")) is not list
                or any(type(row) is not dict or type(row.get("id")) is not str or not row["id"] for row in payload["data"])):
            raise ProviderError("Unexpected Meterless model-list structure")
        return tuple(row["id"] for row in payload["data"])

    def generate(self, prompt: tuple[str, str], schema: dict) -> InferenceResponse:
        # Strict mode's messages/payload remain unchanged. JSON-object mode has
        # no schema carrier on the wire, so supply the SAME contract in the
        # system message instead. This is explicit selection, never fallback.
        format_instruction = "Match the response_format JSON Schema exactly; ARGUS independently validates the result."
        if self.output_mode == "json_object":
            format_instruction = (model_contract_instructions(schema) + "\n"
                                  "Match the following ARGUS model interpretation JSON Schema exactly; "
                                  "ARGUS independently validates the result. JSON mode enforces syntax only.\n"
                                  + json.dumps(schema, ensure_ascii=False, separators=(",", ":")))
        instructions = (prompt[0] + "\nOutput mode: " + self.inference_output_mode + ". "
                        "Return one JSON object only: no markdown fences or extra text. "
                        "Within each object every property name must occur exactly once; duplicate keys are rejected. "
                        "Use separate array elements for multiple findings or citations, never repeated properties. "
                        "Use only standard JSON values, without NaN or Infinity. Keep the assessment concise. "
                        + format_instruction)
        response_format = {"type": "json_schema", "json_schema": {
            "name": self.SCHEMA_NAME, "strict": True, "schema": schema}}
        if self.output_mode == "json_object":
            response_format = {"type": "json_object"}
        request_payload = {
            "model": self.config.model, "messages": [{"role": "system", "content": instructions},
                                                       {"role": "user", "content": prompt[1]}],
            "max_tokens": self.config.max_output_tokens, "stream": False,
            "response_format": response_format,
        }
        if self.temperature is not None:
            request_payload["temperature"] = self.temperature
        payload = self._request_json("/chat/completions", request_payload)
        error = payload.get("error") if type(payload) is dict else None
        if type(error) is dict and type(error.get("code")) is str and error["code"] in self.STRUCTURED_ERRORS:
            raise StructuredOutputError(error["code"], model=self.config.model,
                                        metadata=self._error_metadata(payload, 200, error["code"]), http_status=200)
        if (type(payload) is not dict or payload.get("error") is not None or payload.get("object") != "chat.completion"
                or payload.get("model") != self.config.model or type(payload.get("choices")) is not list
                or len(payload["choices"]) != 1 or type(payload.get("id")) is not str or not payload["id"]):
            raise ProviderError("Meterless response has an unexpected structure or model identifier")
        choice = payload["choices"][0]
        if type(choice) is not dict or type(choice.get("index")) is not int or choice["index"] != 0:
            raise ProviderError("Meterless output did not finish successfully; expected one stopped completion")
        metadata = self._metadata(http_status=200, payload=payload)
        metadata["finish_reason"] = choice.get("finish_reason")
        self._structured_metadata(payload, metadata)
        if choice.get("finish_reason") != "stop":
            message = choice.get("message")
            text = message.get("content") if type(message) is dict and message.get("role") == "assistant" else None
            raise InferenceTerminationError(metadata,
                                            model=self.config.model, max_output_tokens=self.config.max_output_tokens,
                                            response_text=text if type(text) is str else None)
        message = choice.get("message")
        if (type(message) is not dict or message.get("role") != "assistant" or type(message.get("content")) is not str
                or not message["content"].strip() or message.get("refusal") not in (None, "")
                or message.get("tool_calls") not in (None, []) or message.get("function_call") is not None):
            raise ProviderError("Meterless returned no model output, refused, or requested unsupported actions")
        return InferenceResponse(message["content"], "meterless", payload["model"], False, metadata)
