"""Produce a provisional assessment from an existing bundle; never retrieve evidence."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any

if __package__:
    from .evidence_collector import serialise
    from .evidence_queries import identifier
    from .reasoning_assessment import ASSESSMENT_SCHEMA, strict_json, validate_assessment, validate_factual_claims, validate_structure
    from .reasoning_diagnostics import failure_details
    from .reasoning_prompts import build_prompt
    from .reasoning_providers import MeterlessConfig, MeterlessProvider, MockProvider, OpenAIConfig, OpenAIProvider, ReasoningProvider
    from .reasoning_view import DEFAULT_VIEW_BYTES, ReasoningError, build_reasoning_view, digest
    from .zetaris_client import ROOT
else:
    from evidence_collector import serialise
    from evidence_queries import identifier
    from reasoning_assessment import ASSESSMENT_SCHEMA, strict_json, validate_assessment, validate_factual_claims, validate_structure
    from reasoning_diagnostics import failure_details
    from reasoning_prompts import build_prompt
    from reasoning_providers import MeterlessConfig, MeterlessProvider, MockProvider, OpenAIConfig, OpenAIProvider, ReasoningProvider
    from reasoning_view import DEFAULT_VIEW_BYTES, ReasoningError, build_reasoning_view, digest
    from zetaris_client import ROOT


class ReasoningFailure(ReasoningError):
    """Failed validation with bounded diagnostics, never the raw model response."""

    def __init__(self, message: str, report: dict) -> None:
        self.report = report
        super().__init__(message)


def reason_bundle(bundle: dict, provider: ReasoningProvider, *, max_view_bytes: int = DEFAULT_VIEW_BYTES) -> tuple[dict, dict]:
    """No I/O here except the explicitly supplied inference provider."""
    view = build_reasoning_view(bundle, max_bytes=max_view_bytes)
    response = provider.generate(build_prompt(view), ASSESSMENT_SCHEMA)
    stages = {name: "not_run" for name in ("json_parsing", "schema", "citations_and_gaps", "factual_consistency")}
    stage = "json_parsing"
    assessment = None
    try:
        assessment = strict_json(response.text)
        stages[stage] = "passed"
        stage = "schema"
        validate_structure(assessment)
        stages[stage] = "passed"
        stage = "citations_and_gaps"
        validate_assessment(assessment, view, check_facts=False)
        stages[stage] = "passed"
        stage = "factual_consistency"
        validate_factual_claims(assessment, view)
        stages[stage] = "passed"
    except ReasoningError as error:
        stages[stage] = "failed"
        details, termination = failure_details(stage, error, response.text, response.metadata, assessment)
        report = {"schema_version": "2.0", "validated_assessment_written": False, "alert_id": view["alert_id"],
                  "bundle_sha256": view["bundle_sha256"], "reasoning_view_sha256": digest(view),
                  "provider": {"name": response.provider, "model": response.model, "mock_generated": response.mock_generated},
                  "validation_stages": stages, "validation_stage": stage, "error": str(error), "details": details,
                  "inference": termination,
                  "response_sha256": hashlib.sha256(response.text.encode("utf-8", errors="surrogatepass")).hexdigest() if isinstance(response.text, str) else None}
        raise ReasoningFailure(f"{stage}: {error}", report) from None
    result: dict[str, Any] = {
        "schema_version": "2.0", "bundle_sha256": view["bundle_sha256"], "reasoning_view_sha256": digest(view),
        "provider": {"name": response.provider, "model": response.model, "mock_generated": response.mock_generated,
                     "metadata": response.metadata},
        "validation": "Structure, citation membership, gap coverage and supported structured facts checked; prose and conclusions need human review.",
        "validation_stages": stages,
        "assessment": assessment,
    }
    return view, result


def artifact_paths(directory: Path, alert_id: str, provider: str, input_path: Path) -> tuple[Path, Path]:
    """Only two application-selected filenames within the designated directory."""
    try:
        identifier(alert_id)
    except ValueError:
        raise ReasoningError("Invalid assessment artifact identifier") from None
    if provider not in ("mock", "openai", "meterless"):
        raise ReasoningError("Invalid assessment artifact provider")
    target = directory.resolve()
    if any(target.is_relative_to(ROOT / name) for name in ("data", "evaluation", ".git", "scripts", "tests", "docs", "artifacts/evidence")):
        raise ReasoningError("Choose a dedicated assessment output directory outside protected project inputs")
    paths = tuple(target / f"{alert_id}.{provider}.{suffix}.json" for suffix in ("assessment", "view"))
    for path in paths:
        if path.is_symlink() or (path.exists() and path.stat().st_nlink > 1) or path.resolve() == input_path.resolve():
            raise ReasoningError("Assessment output cannot overwrite the evidence or follow linked files")
    return paths


def failure_path(directory: Path, report: dict, input_path: Path) -> Path:
    assessment, _ = artifact_paths(directory, report["alert_id"], report["provider"]["name"], input_path)
    suffix = report["response_sha256"] or digest(report)
    target = assessment.with_name(f"{report['alert_id']}.{report['provider']['name']}.failure.{suffix}.json")
    if target.is_symlink() or (target.exists() and target.stat().st_nlink > 1) or target.resolve() == input_path.resolve():
        raise ReasoningError("Failure output cannot overwrite the evidence or follow linked files")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--provider", choices=("mock", "openai", "meterless"), required=True,
                        help="Explicit selection required. Real providers send the view to their inference service.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "assessments")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.local", help="Used only by real providers")
    parser.add_argument("--max-view-bytes", type=int, default=DEFAULT_VIEW_BYTES)
    args = parser.parse_args(argv)
    try:
        with args.bundle.open("rb") as handle:
            raw = handle.read(8_388_609)
        if len(raw) > 8_388_608:
            raise ReasoningError("Evidence bundle exceeds the input byte limit")
        bundle = strict_json(raw.decode("utf-8-sig"))
        # Validate/size-check inputs before credentials are loaded or inference runs.
        view = build_reasoning_view(bundle, max_bytes=args.max_view_bytes)
        assessment_path, view_path = artifact_paths(args.output_dir, view["alert_id"], args.provider, args.bundle)
        credential = None
        if args.provider == "openai":
            config = OpenAIConfig.from_environment(args.env_file)
            provider = OpenAIProvider(config)
            credential = config.api_key
        elif args.provider == "meterless":
            meterless_config = MeterlessConfig.from_environment(args.env_file)
            provider = MeterlessProvider(meterless_config)
            credential = meterless_config.runtime_token
        else:
            provider = MockProvider()
        try:
            view, result = reason_bundle(bundle, provider, max_view_bytes=args.max_view_bytes)
        except ReasoningFailure as error:
            diagnostic = serialise(error.report)
            if credential and credential in diagnostic:
                raise ReasoningError("Failure diagnostic refused because it contains a configured credential") from None
            target = failure_path(args.output_dir, error.report, args.bundle)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(diagnostic, encoding="utf-8", newline="\n")
            print(f"Validation diagnostics: {target}")
            raise
        payload, view_payload = serialise(result), serialise(view)
        if credential and (credential in payload or credential in view_payload):
            raise ReasoningError("Output refused because it contains a configured credential")
        assessment_path.parent.mkdir(parents=True, exist_ok=True)
        assessment_path.write_text(payload, encoding="utf-8", newline="\n")
        view_path.write_text(view_payload, encoding="utf-8", newline="\n")
    except ReasoningError as error:
        print(f"Reasoning failed: {error}")
        return 1
    except (OSError, UnicodeError):
        print("Cannot read the evidence bundle or write the assessment artifacts")
        return 1
    print(f"{'MOCK-GENERATED demonstration' if result['provider']['mock_generated'] else 'Provisional model assessment'}: {view['alert_id']}")
    print(f"Provider: {result['provider']['name']}; model: {result['provider']['model']}")
    if "output_mode" in result["provider"]["metadata"]:
        print(f"Output mode: {result['provider']['metadata']['output_mode']}")
    print(f"Assessment: {assessment_path}\nReasoning view: {view_path}")
    print("Human review required; no additional evidence was retrieved or action executed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
