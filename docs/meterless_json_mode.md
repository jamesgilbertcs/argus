# Phase 3B.9: explicit Meterless JSON diagnostic mode

Strict `json_schema` remains the default. ARGUS now also accepts an explicit
`--meterless-output-mode json_object` selection for the existing Meterless provider.
It changes the runtime's output-format request, **not ARGUS's acceptance rules**.
Phase 3B.10 adds [schema-derived contract presentation](model_contract_presentation.md)
to this explicit mode, with unchanged schemas and validation.
There is no automatic fallback, model switch, retry, JSON repair, field coercion,
unwrapping, inference-directed query or invented missing content.

## Verified capability and scope

The installed Nightshift **1.2.7** `structured-output.js` distinguishes:

| Wire mode | Installed behavior | Local evidence |
|---|---|---|
| Strict `json_schema` | Parse and validate content against caller schema; reject nonconforming output | Prior small/full synthetic successes and repeated typed rejection reports |
| `json_object` | Parse content as a JSON object; no caller-schema shape gate | Installed spec/validator/forwarding code and the saved 6 November JSON-mode probe (HTTP 200, valid object) |
| No `response_format` | Ordinary best-effort completion; this structured validator does not run | Installed code and historical prompt-JSON inference artifacts |

These are the local runtime/NVIDIA route's observed and inspected capabilities,
not a promise of compatibility with every model or OpenAI-compatible service.
JSON-object mode can still reject invalid JSON, refusal or token-limit termination
before ARGUS receives content. It is **not** unrestricted access to rejected
runtime completions and does not bypass authentication, privacy controls or size
limits. Ordinary/prompt-only mode is documented here but is **not** a newly
implemented CLI option; only strict and explicit JSON-object modes are selectable.

The configured model remains `nvidia/nemotron-3.5-bolt-e4b`. NVIDIA credentials stay
inside Nightshift; ARGUS uses the existing loopback URL, runtime token and model
settings. No OpenAI key, Zetaris connection or new dependency is required.

## Request construction

Default behavior is unchanged: the complete interpretation schema is supplied in
strict `response_format.json_schema`; default temperature remains omitted. Offline
preflight compared the complete default request for ALT0018 with the retained
Phase 3B.6 reconstruction and found exact JSON-object equality.

Explicit JSON mode sends:

```json
{"response_format": {"type": "json_object"}}
```

This format has **no schema carrier**. Consequently the adapter supplies the exact
unchanged `MODEL_OUTPUT_SCHEMA` in its system-message format instructions instead.
Otherwise the model would be asked to follow a schema that it never received.
The shared investigative `SYSTEM_PROMPT`, evidence view, task, field meanings,
types, enums and citation/fact requirements are unchanged. Only the provider's
format paragraph identifies `json_object`, says syntax-only enforcement, and
introduces the schema text. It does not supply scenario answers or output examples.
This schema-placement adjustment is explicit and mode-specific; strict mode's
messages and response format remain unchanged.

Python callers may use:

```python
provider = MeterlessProvider(config, output_mode="json_object", temperature=0)
```

The constructor accepts only `json_schema` and `json_object`. An optional finite
temperature between 0 and 2 is forwarded exactly; omission preserves the existing
default. CLI Meterless options with another provider are rejected. Invalid options
fail before inference, and unsupported runtime formats fail rather than changing
the requested mode. The timeout, model, token cap, disabled proxies/redirects,
credential checks and 2 MiB response bound are shared with strict mode.

## Independent validation and observability

Returned model content goes through the existing `reason_bundle` stages:

1. Strict JSON parsing, including duplicate-key/non-finite-value rejection.
2. The unchanged model interpretation schema.
3. Source table/ID/variant citations and valid evidence-gap references.
4. Supported factual claims checked against recomputed deterministic facts.
5. Deterministic final assembly, **then final assessment/wrapper validation**.

The existing artifact groups the last two operations under `final_assembly`; both
must pass before that stage is marked passed. No result is saved as validated
before all gates pass. Arbitrary prose and conclusions are still not mechanically
fact-checked. An accepted schema is not a finding of correctness or compromise.

As before, interpretation_v1 permits model `unknowns` not to repeat every
collection gap. Final assembly independently preserves **all** gaps and completeness
limitations. JSON mode changes neither that policy nor the rejection of invented
gap IDs or contradictory factual claims.

Metadata distinguishes `output_mode: json_object`, requested kind `json_object`,
and `requested_strict: false`. Nightshift's reported `kind`, `strict`, schema name,
fingerprint, capability and validation are checked against the selected mode;
JSON-object metadata requires `strict: false` and no schema name. Missing runtime
metadata remains unavailable; it is not invented. A runtime syntax pass is never
reported as an ARGUS schema/factual pass. Available usage/finish reason, requested
temperature and actual model echo remain in provider metadata. Effective backend
temperature is not inferred from the request.

Default failure artifacts contain only existing bounded schema/JSON diagnostics,
stage outcomes and hashes, not raw model responses. The existing separate raw
retention flag remains explicit; **it is not used for this phase's live call**.
Credential-safe provider/HTTP errors and termination diagnostics preserve the
selected mode, including when a completion is rejected later by ARGUS.

## Commands

Use the existing ignored `.env.local` or process environment for
`METERLESS_BASE_URL`, `METERLESS_RUNTIME_TOKEN`, `METERLESS_MODEL`,
`ARGUS_MAX_OUTPUT_TOKENS` and `ARGUS_INFERENCE_TIMEOUT_SECONDS`. Do not put secrets
in command-line arguments, reports or checked-in files.

```powershell
$argusPython = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe"
& $argusPython -m unittest discover -s tests -v
# Explicit diagnostic inference; choose a fresh directory to preserve old artifacts:
& $argusPython scripts/reason_evidence.py artifacts/evidence/ALT0018.json `
  --provider meterless --meterless-output-mode json_object --meterless-temperature 0 `
  --output-dir artifacts/assessments/json-mode-review
# Existing strict default, unchanged:
& $argusPython scripts/reason_evidence.py artifacts/evidence/ALT0018.json --provider meterless
```

The inference commands make real calls; they are not automatic verification steps
and should not be replayed as part of inspecting a spent experiment.

## Automated verification

**306 tests passed**, including all 287 pre-existing cases and 19 focused JSON-mode
tests. All external inference in automated tests is mocked. Coverage includes mode
selection, the unchanged strict request, exact schema delivery, valid final
assembly, malformed JSON, missing/extra fields, invalid enums, invented citations
and gaps, contradictory facts, preserved collection gaps, mismatched runtime
metadata, unsupported formats, termination, size/connection/timeout handling,
credential-safe diagnostics, no retry and CLI failure artifact boundaries.

New implementation: mode/optional temperature selection in
`scripts/reasoning_providers.py`, explicit CLI flags in `scripts/reason_evidence.py`,
and `tests/test_meterless_json.py`. Shared prompts, schemas, facts, validators and
assembly are unchanged. Mock and OpenAI behavior remain covered by the full suite.

## Single live verification: 7 November 2026

**The fully validated live-assessment objective was not achieved.** JSON mode
provided an observable completion and ARGUS rejected its incomplete contract;
no acceptance rule was relaxed to produce an artifact.

Exactly **one** inference POST was made with ALT0018, the unchanged evidence view,
current interpretation schema, configured Nemotron model, explicit JSON-object
mode, temperature **0**, output cap **12,000**, and timeout **180 seconds**. A
spent-before-dispatch marker prevents replay through the local verification helper.
No raw-failure retention flag was supplied. No further inference followed failure.

- HTTP/provider outcome: **200**, completion returned, `finish_reason: stop`.
- Requested and returned API model: **nvidia/nemotron-3.5-bolt-e4b**. This is the
  response envelope's model identity, not an independently inspected weight revision.
- Runtime: `kind: json_object`, `strict: false`, `validation: passed`, capability
  `unverified`, format fingerprint `56331b130db3e075`.
- Requested temperature: **0**; effective backend temperature is not echoed.
- Completion usage: **3,393 tokens**. Prompt/total usage is not retained in the
  existing bounded failure-metadata projection; do not infer zero usage or token
  truncation. The model reported `stop`, with a 12,000-token cap.
- ARGUS JSON parsing: **passed**.
- Model interpretation schema: **failed**, `FIELDS` at `assessment`.
- Citation/evidence-gap validation: **not_run**.
- Structured factual consistency: **not_run**.
- Deterministic final assembly: **not_run**.
- Final assessment/schema validation: **not_run** (part of `final_assembly`).
- Validated assessment saved: **no**.

The bounded schema diagnostics report two missing required fields:

1. `assessment.assessed_hypothesis` — expected string.
2. `assessment.factual_claims` — expected array.

They are requirements of the unchanged contract. ARGUS did not invent a hypothesis,
insert an empty claims list, repair/unwrap JSON or retry the model. No claim is
made that every other aspect was independently accepted: the main validator stops
on the root field-set error, and later evidence checks did not run.

There is no accepted classification, confidence or assessment to audit for network
matches, reported devices, timing, VPN explanation or investigative quality. Those
questions remain unverified; parseable JSON and a runtime syntax pass do not
establish factual correctness. The failed raw completion was not saved or printed.
Only bounded diagnostics, source/view/response hashes and inference metadata were
retained.

Local artifacts under `artifacts/assessments/phase3b9-json-object/` are new and
Git-ignored. They include the payload-only reconstructed request, preflight/input
hashes, once-only helper/marker, CLI exit result, and this failure artifact under
`live/`:

```text
ALT0018.meterless.failure.e224b0ac8d2d2ee7091b6fd90f4183d5a443d108576366c5d70d126a2f588375.json
```

Preflight confirmed canonical bundle hash
`ea27ef9f637468bc5506b13d005e6b8d0e05f1b2efbc28f8d0b7d2136e001089`
and view hash `224830e2289e386b817f83ead950f62d7ad8987608f19d72a0512919b6a3cddd`.
All canonical evidence, ground truth and historical artifacts were preserved;
no hidden evaluation information was sent to inference.

The remaining blocker is model omission of required interpretation fields,
now visible to ARGUS independently of Nightshift's schema gate. Why those fields
were omitted is not established. The smallest next step is an offline review of
contract delivery and formatting emphasis for the complete field inventory,
followed only by a separately authorized verification. Do not turn the two missing
fields into optional/defaulted values or infer that JSON mode is reliable from one
returned completion. No additional experiment or prompt change was made here.
