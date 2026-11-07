"""Produce a provisional assessment from an existing bundle; never retrieve evidence."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

if __package__:
    from .evidence_collector import serialise
    from .evidence_queries import identifier
    from .reasoning_assessment import MODEL_OUTPUT_SCHEMA, strict_json, validate_model_content, validate_factual_claims, validate_model_structure
    from .reasoning_assembly import VALIDATION_STAGES, assemble_assessment, validate_final_assessment
    from .reasoning_diagnostics import failure_details, inference_metadata
    from .reasoning_prompts import build_prompt
    from .reasoning_providers import InferenceTerminationError, StructuredOutputError, MeterlessConfig, MeterlessProvider, MockProvider, OpenAIConfig, OpenAIProvider, ReasoningProvider
    from .reasoning_view import DEFAULT_VIEW_BYTES, ReasoningError, build_reasoning_view, digest
    from .zetaris_client import ROOT
else:
    from evidence_collector import serialise
    from evidence_queries import identifier
    from reasoning_assessment import MODEL_OUTPUT_SCHEMA, strict_json, validate_model_content, validate_factual_claims, validate_model_structure
    from reasoning_assembly import VALIDATION_STAGES, assemble_assessment, validate_final_assessment
    from reasoning_diagnostics import failure_details, inference_metadata
    from reasoning_prompts import build_prompt
    from reasoning_providers import InferenceTerminationError, StructuredOutputError, MeterlessConfig, MeterlessProvider, MockProvider, OpenAIConfig, OpenAIProvider, ReasoningProvider
    from reasoning_view import DEFAULT_VIEW_BYTES, ReasoningError, build_reasoning_view, digest
    from zetaris_client import ROOT


class ReasoningFailure(ReasoningError):
    """Bounded diagnostic report; optional raw text is excluded from repr/str."""

    def __init__(self, message: str, report: dict, *, response_text: str | None = None) -> None:
        self.report = report
        # Never included in repr/str/report. CLI retention requires explicit opt-in.
        self.response_text = response_text
        super().__init__(message)


def reason_bundle(bundle: dict, provider: ReasoningProvider, *, max_view_bytes: int = DEFAULT_VIEW_BYTES) -> tuple[dict, dict]:
    """No I/O here except the explicitly supplied inference provider."""
    view = build_reasoning_view(bundle, max_bytes=max_view_bytes)
    stages = {name: "not_run" for name in VALIDATION_STAGES}
    try:
        response = provider.generate(build_prompt(view), MODEL_OUTPUT_SCHEMA)
    except (InferenceTerminationError, StructuredOutputError) as error:
        stage = "provider_structured_output" if isinstance(error, StructuredOutputError) else "provider_termination"
        report = {"schema_version": "3.0", "validated_assessment_written": False, "alert_id": view["alert_id"],
                  "bundle_sha256": view["bundle_sha256"], "reasoning_view_sha256": digest(view),
                  "provider": {"name": "meterless", "model": error.model, "mock_generated": False},
                  "validation_stages": stages, "validation_stage": stage, "error": str(error),
                  "details": {"stage": stage, **(error.details if isinstance(error, StructuredOutputError) else {"check": "UNSUCCESSFUL_TERMINATION"})},
                  "inference": error.inference,
                  "response_sha256": hashlib.sha256(error.response_text.encode("utf-8", errors="surrogatepass")).hexdigest() if error.response_text is not None else None}
        raise ReasoningFailure(str(error), report, response_text=error.response_text) from None
    stage = "json_parsing"
    assessment = None
    try:
        assessment = strict_json(response.text)
        stages[stage] = "passed"
        stage = "schema"
        validate_model_structure(assessment)
        stages[stage] = "passed"
        stage = "citations_and_gaps"
        validate_model_content(assessment, view, check_facts=False)
        stages[stage] = "passed"
        stage = "factual_consistency"
        validate_factual_claims(assessment, view, require_coverage=False)
        stages[stage] = "passed"
        stage = "final_assembly"
        result = assemble_assessment(assessment, view, response)
        validate_final_assessment(result, view)
        stages[stage] = "passed"
    except ReasoningError as error:
        stages[stage] = "failed"
        details, termination = failure_details(stage, error, response.text, response.metadata, assessment, schema=MODEL_OUTPUT_SCHEMA)
        termination.update(inference_metadata(response.metadata, response.model))
        report = {"schema_version": "3.0", "validated_assessment_written": False, "alert_id": view["alert_id"],
                  "bundle_sha256": view["bundle_sha256"], "reasoning_view_sha256": digest(view),
                  "provider": {"name": response.provider, "model": response.model, "mock_generated": response.mock_generated},
                  "validation_stages": stages, "validation_stage": stage, "error": str(error), "details": details,
                  "inference": termination,
                  "response_sha256": hashlib.sha256(response.text.encode("utf-8", errors="surrogatepass")).hexdigest() if isinstance(response.text, str) else None}
        raise ReasoningFailure(f"{stage}: {error}", report,
                               response_text=response.text if type(response.text) is str else None) from None
    result["validation_stages"] = stages
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


def failure_response_path(diagnostic_path: Path, input_path: Path) -> Path:
    target = diagnostic_path.with_suffix(".response.txt")
    if target.is_symlink() or (target.exists() and target.stat().st_nlink > 1) or target.resolve() == input_path.resolve():
        raise ReasoningError("Failed response cannot overwrite the evidence or follow linked files")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--provider", choices=("mock", "openai", "meterless"), required=True,
                        help="Explicit selection required. Real providers send the view to their inference service.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "assessments")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.local", help="Used only by real providers")
    parser.add_argument("--max-view-bytes", type=int, default=DEFAULT_VIEW_BYTES)
    parser.add_argument("--meterless-output-mode", choices=("json_schema", "json_object"),
                        help="Meterless only: strict json_schema by default; explicit json_object diagnostic mode still requires every ARGUS validator")
    parser.add_argument("--meterless-temperature", type=float,
                        help="Meterless only: optional explicit temperature from 0 to 2; omitted by default")
    parser.add_argument("--retain-failed-response", action="store_true",
                        help="Opt in to retaining untrusted model text locally beside failure diagnostics; never printed")
    args = parser.parse_args(argv)
    if args.provider != "meterless" and (args.meterless_output_mode is not None or args.meterless_temperature is not None):
        parser.error("Meterless options require --provider meterless")
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
            provider = MeterlessProvider(meterless_config, output_mode=args.meterless_output_mode or "json_schema",
                                         temperature=args.meterless_temperature)
            credential = meterless_config.runtime_token
        else:
            provider = MockProvider()
        try:
            view, result = reason_bundle(bundle, provider, max_view_bytes=args.max_view_bytes)
        except ReasoningFailure as error:
            diagnostic = serialise(error.report)
            raw_failure = error.response_text if args.retain_failed_response else None
            if credential and (credential in diagnostic or raw_failure is not None and credential in raw_failure):
                raise ReasoningError("Failure diagnostic refused because it contains a configured credential") from None
            target = failure_path(args.output_dir, error.report, args.bundle)
            raw_target = failure_response_path(target, args.bundle) if raw_failure is not None else None
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(diagnostic, encoding="utf-8", newline="\n")
            if raw_target is not None:
                # backslashreplace preserves malformed surrogate output safely;
                # the diagnostic hash always describes the original response.
                raw_target.write_bytes(raw_failure.encode("utf-8", errors="backslashreplace"))
                print(f"Untrusted failed response retained locally: {raw_target}")
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
