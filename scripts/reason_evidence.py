"""Produce a provisional assessment from an existing bundle; never retrieve evidence."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

if __package__:
    from .evidence_collector import serialise
    from .evidence_queries import identifier
    from .reasoning_assessment import ASSESSMENT_SCHEMA, parse_assessment, strict_json
    from .reasoning_prompts import build_prompt
    from .reasoning_providers import MockProvider, OpenAIConfig, OpenAIProvider, ReasoningProvider
    from .reasoning_view import DEFAULT_VIEW_BYTES, ReasoningError, build_reasoning_view, digest
    from .zetaris_client import ROOT
else:
    from evidence_collector import serialise
    from evidence_queries import identifier
    from reasoning_assessment import ASSESSMENT_SCHEMA, parse_assessment, strict_json
    from reasoning_prompts import build_prompt
    from reasoning_providers import MockProvider, OpenAIConfig, OpenAIProvider, ReasoningProvider
    from reasoning_view import DEFAULT_VIEW_BYTES, ReasoningError, build_reasoning_view, digest
    from zetaris_client import ROOT


def reason_bundle(bundle: dict, provider: ReasoningProvider, *, max_view_bytes: int = DEFAULT_VIEW_BYTES) -> tuple[dict, dict]:
    """No I/O here except the explicitly supplied inference provider."""
    view = build_reasoning_view(bundle, max_bytes=max_view_bytes)
    response = provider.generate(build_prompt(view), ASSESSMENT_SCHEMA)
    assessment = parse_assessment(response.text, view)
    result: dict[str, Any] = {
        "schema_version": "1.0", "bundle_sha256": view["bundle_sha256"], "reasoning_view_sha256": digest(view),
        "provider": {"name": response.provider, "model": response.model, "mock_generated": response.mock_generated,
                     "metadata": response.metadata},
        "validation": "Structure, citation membership and gap coverage checked; factual correctness is not established.",
        "assessment": assessment,
    }
    return view, result


def artifact_paths(directory: Path, alert_id: str, provider: str, input_path: Path) -> tuple[Path, Path]:
    """Only two application-selected filenames within the designated directory."""
    try:
        identifier(alert_id)
    except ValueError:
        raise ReasoningError("Invalid assessment artifact identifier") from None
    if provider not in ("mock", "openai"):
        raise ReasoningError("Invalid assessment artifact provider")
    target = directory.resolve()
    if any(target.is_relative_to(ROOT / name) for name in ("data", "evaluation", ".git", "scripts", "tests", "docs", "artifacts/evidence")):
        raise ReasoningError("Choose a dedicated assessment output directory outside protected project inputs")
    paths = tuple(target / f"{alert_id}.{provider}.{suffix}.json" for suffix in ("assessment", "view"))
    for path in paths:
        if path.is_symlink() or (path.exists() and path.stat().st_nlink > 1) or path.resolve() == input_path.resolve():
            raise ReasoningError("Assessment output cannot overwrite the evidence or follow linked files")
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--provider", choices=("mock", "openai"), required=True,
                        help="Explicit selection required. openai sends the evidence view to OpenAI.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "assessments")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.local", help="Used only by openai")
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
        config = OpenAIConfig.from_environment(args.env_file) if args.provider == "openai" else None
        provider = OpenAIProvider(config) if config else MockProvider()
        view, result = reason_bundle(bundle, provider, max_view_bytes=args.max_view_bytes)
        payload, view_payload = serialise(result), serialise(view)
        if config and (config.api_key in payload or config.api_key in view_payload):
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
    print(f"Assessment: {assessment_path}\nReasoning view: {view_path}")
    print("Human review required; no additional evidence was retrieved or action executed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
