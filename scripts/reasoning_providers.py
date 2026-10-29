"""Explicit mock and OpenAI Responses adapters. No provider has tool capabilities."""

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
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

if __package__:
    from .reasoning_assessment import strict_json
    from .reasoning_view import ReasoningError
    from .zetaris_client import ROOT, ZetarisError, _read_settings
else:
    from reasoning_assessment import strict_json
    from reasoning_view import ReasoningError
    from zetaris_client import ROOT, ZetarisError, _read_settings


class ProviderError(ReasoningError):
    pass


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
            "schema_version": "1.0", "alert_id": view["alert_id"],
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
        return InferenceResponse(texts[0], "openai", payload["model"], False, metadata)
