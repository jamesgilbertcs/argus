# Phase 3B.4: strict Meterless structured output

ARGUS explicitly requests strict JSON Schema output from the existing Nightshift
chat-completions endpoint. It never falls back to JSON mode or prompt-only JSON,
switches models, repairs output or retries. No new Python dependency is needed.
Strict mode remains the default. Phase 3B.9 adds a separately selected
[JSON diagnostic mode](meterless_json_mode.md); it is never an automatic fallback
and still requires every ARGUS acceptance check.

## Request and model contract

The verified small-schema combination is Nightshift **1.2.7** with
**nvidia/nemotron-3.5-bolt-e4b**. See the earlier
[capability investigation](meterless_structured_output_capabilities.md), including
the distinction between the full local runtime and Web Session Hotspot.

`MeterlessProvider.generate(prompt, schema)` now sends:

```python
payload = {
    "model": config.model,
    "messages": [{"role": "system", "content": instructions},
                 {"role": "user", "content": prompt[1]}],
    "max_tokens": config.max_output_tokens,
    "stream": False,
    "response_format": {
        "type": "json_schema",
        "json_schema": {"name": "argus_assessment", "strict": True,
                        "schema": MODEL_OUTPUT_SCHEMA},
    },
}
```

The serialized request contains the exact schema object and an integer budget.
The provider no longer duplicates the schema in system-prompt text. Its format
framing identifies `strict_json_schema`; shared reasoning instructions and evidence
selection remain unchanged. Authentication, loopback URL restrictions, disabled
proxies/redirects, timeout, byte limits, explicit model and token budget are retained.
No temperature or thinking-policy change is introduced by this integration.

The authoritative model-facing schema is the existing
`reasoning_assessment.MODEL_OUTPUT_SCHEMA` (`interpretation_v1`, final schema 3.0).
Every object declares its exact required properties and `additionalProperties:
false`. Classification/confidence, finding kinds and six fact kinds retain their
exact enums. Nested findings, citations, alternatives, unknowns and claims retain
their structures. Claim values retain the existing `anyOf` string/integer/null.
ARGUS-owned alert/version/collection/completeness/provenance metadata is excluded.
No investigative semantics or schema feature was changed for this integration.
Provider acceptance of the complete schema is a live verification question;
small-schema support alone is not proof of full-schema compatibility.

## Independent acceptance and final validation

All five existing stages remain mandatory:

1. Strict JSON parsing: duplicate keys, invalid JSON, non-finite constants and
   oversized output remain rejected. No repair or extraction from fences.
2. Model interpretation structure: ARGUS checks its original schema, independently
   of the runtime's claim that the response passed validation.
3. Citations and gap handling: source table/ID/variant membership and investigative
   constraints; collection gaps remain independently preserved in final assembly.
4. Factual consistency: selected structured assertions must match recomputed
   evidence facts exactly, including types, provenance and citations.
5. Final assembly **and independent final-artifact validation**. The new
   `reasoning_assembly.FINAL_ASSESSMENT_SCHEMA` validates the assembled assessment;
   `validate_final_assessment(result, view)` also checks exact wrapper fields,
   alert/collection identity, completeness policy, hashes, every source variant,
   deterministic relationships, collection limitations, provider labels and
   inference metadata. Type-sensitive canonical comparisons prevent `1` from
   impersonating boolean `true`. Saved v3 artifacts may be checked again, including
   their five passed stage markers. Historical 1.0/2.0 validators are unchanged.

Only after final validation succeeds does orchestration mark `final_assembly`
passed and write the assessment/view artifacts. A provider-enforced schema cannot
prove that cited prose follows from evidence, that hypotheses are sensible, or
that a provisional classification is correct. Human review remains separate.

## Runtime diagnostics and failure behaviour

Available runtime fields are retained under
`provider.metadata.meterless.structured_output`, also projected into
`inference.meterless.structured_output`. They include kind, strict flag, schema name,
fingerprint, capability and validation result, with explicit availability and
requested-kind/strict markers. A runtime `capability: unverified` is preserved as
reported; it is not relabelled as provider-verified by ARGUS. Missing metadata stays
unavailable, never fabricated. It does not change the strict request mode or bypass
independent validation. Explicit contradictory/rejected format metadata fails.

Only known fields are admitted; unknown fields are counted and omitted without
copying their names/values. Full runtime receipts, private reasoning, upstream
messages and authentication headers are not copied. A malformed/mismatched
metadata block is rejected with a fixed diagnostic. Known typed HTTP format errors
are retained as `provider_structured_output` failures, with HTTP status and bounded
metadata. The allowlist covers `structured_output_unsupported`,
`structured_output_schema_rejected`, `invalid_json_output`,
`schema_validation_failed`, `response_truncated` and `response_refused`.
Known finish reasons and integer completion usage are retained when returned in
error metadata; absent responding models stay unknown. Generic HTTP/authentication,
connection, size and malformed-response failures retain safe static errors.

The request mode, HTTP status, requested/returned model, configured output cap,
available usage and termination metadata survive successful inference and later
ARGUS validation failures. No raw upstream error message enters diagnostics.
The existing optional `--retain-failed-response` remains explicit, local,
credential-guarded and separate from validated artifacts. Runtime post-validation
errors may withhold the completion, in which case ARGUS cannot retain that text.

## Commands and compatibility

Use the existing `METERLESS_BASE_URL`, `METERLESS_RUNTIME_TOKEN`, `METERLESS_MODEL`,
`ARGUS_MAX_OUTPUT_TOKENS` and `ARGUS_INFERENCE_TIMEOUT_SECONDS` configuration.
No OpenAI/NVIDIA key is needed in ARGUS; NVIDIA credentials stay in Nightshift.
Mock mode remains deterministic and labelled; the OpenAI Responses adapter is
unchanged. Other OpenAI-compatible runtimes/models are not assumed compatible.

```powershell
$argusPython = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe"
& $argusPython -m unittest discover -s tests -v
# GET-only discovery; this does not perform inference:
& $argusPython scripts/check_meterless.py
# One deliberate inference after configuration; choose a fresh output directory:
& $argusPython scripts/reason_evidence.py artifacts/evidence/ALT0018.json --provider meterless --output-dir artifacts/assessments/phase3b4 --retain-failed-response
```

Runtime and provider schema subsets/bounds, nested structures, output budgets,
termination and service availability can still cause failures. No compatibility
shim removes unsupported features. Failures must be investigated rather than
silently downgrading or weakening ARGUS validation.

## Verification: 6 November 2026

**271 automated tests passed** (all 254 existing cases plus 17 new regressions).
The normal suite uses mocked HTTP, not live inference. Tests cover the exact
request schema, required/enum/nested/extra-field rules, typed runtime errors,
strict-mode termination metadata, no downgrade/retry, credential-safe diagnostics,
independent citation/fact rejection despite reported runtime success, and final
artifact structure/provenance/completeness checks. Existing mock/OpenAI tests pass.

GET-only discovery confirmed the configured model. Exactly **one** subsequent
ALT0018 inference was made with the complete unchanged 3,092-byte interpretation
schema and a 12,000-token output cap, using a fresh `phase3b4` directory.

- **HTTP/provider:** Nightshift returned HTTP **502**, typed
  **`schema_validation_failed`**. It rejected the generated content before
  releasing a successful completion to ARGUS. No retry or fallback occurred.
- **Runtime termination:** `finish_reason: stop`, supplied in runtime validation
  diagnostics. This is a schema failure, not a reported output-limit termination.
- **ARGUS JSON parsing:** not run; no completion was supplied.
- **ARGUS model schema:** not run.
- **ARGUS citations/gaps:** not run.
- **ARGUS factual consistency:** not run.
- **ARGUS final assembly/schema validation:** not run; no validated live assessment
  was written.
- **Model:** requested `nvidia/nemotron-3.5-bolt-e4b`; the local failed-attempt
  record names the same model. A successful response's actual-model field was
  unavailable and is not invented.
- **Usage:** unavailable in the error response. The failed runtime attempt has
  zero accounting counters; those do not establish that inference used zero tokens.
- **Classification/confidence:** unavailable; no accepted assessment exists.

The diagnostic is retained at:

```text
artifacts/assessments/phase3b4/ALT0018.meterless.failure.7ac5da65a80d41ae4d9e55d9496eb48f1d9eb10b0c79ae989210160c9349c29b.json
```

Its `response_sha256` is null: Nightshift withheld completion text. The filename
uses the existing fallback hash of the report, not a falsely asserted response
hash. `--retain-failed-response` cannot save text that was never returned. Local
read-only inspection found failed attempt 11 in the existing app-stream job with
empty text and no new response artifact; the inspected logs/evidence records did
not retain the schema failure's detailed paths. The exact violating field is
therefore not established from retained diagnostics.

An additional **offline-only** check loaded the installed `structured-output.js`
in a sandboxed Node VM without credentials or outbound fetches. It accepted the
complete ARGUS schema with a valid, explicitly mock-generated fixture covering all
six factual-claim kinds, and rejected a finding-shaped object placed in claims.
This checks the runtime validator's schema compatibility, not NVIDIA enforcement
or correctness of the withheld model output. No unsupported feature was identified
that would justify altering the investigative contract.

Manual quality checks for the live output could **not** be performed: exact .51
versus N003 matching, N005 membership, shared reported device, six-minute interval,
VPN weighting, geographical caution and compromise distinction all remain
**unverified for this attempt**. No hidden ground truth was used to fill that gap.

Implementation and mocked checks are complete; the objective of a fully validated
live schema 3.0 assessment was **not achieved in the single permitted call**. The
next separate investigation should retain bounded, credential-safe runtime schema
error paths and examine full-schema/provider constraints using controlled tests.
Do not repair, reclassify or accept this failed response, and do not generalize the
earlier small-schema success into guaranteed full-contract reliability.

Local ignored artifacts include `offline_schema_check.py`,
`offline-schema-fixture.json` (explicit mock), `offline-schema-check.json`, and
`ALT0018.meterless.review.json` under `artifacts/assessments/phase3b4/`. Production
code was not changed after the live failure. Previous assessments, original bundles,
canonical/exported datasets and evaluation labels remain unchanged.
