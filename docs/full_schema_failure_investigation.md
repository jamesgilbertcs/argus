# Phase 3B.5: full-schema rejection investigation

**Finding:** the unchanged full interpretation schema succeeded in both controlled
synthetic full-contract probes. No particular nested structure or unsupported
schema feature was identified. The original rejection was post-generation schema
nonconformance, but its exact field violation cannot be recovered from retained
records. A structural or architectural remedy is not yet evidence-supported.

Only safe diagnostic preservation was implemented. The schema, prompts, inference
settings, citation/factual validators and final assembly were not changed. No
installed Nightshift files or historical artifacts were modified; no ALT0018
inference was run. Exactly **six synthetic inference probes** were made, with no
client retries, repair or fallback. All six passed runtime and independent schema
validation. This does not establish general reliability or investigative quality.

## Runtime rejection path

The installed runtime is **1.2.7**, reconfirmed through the installed CLI's read-only
`GET /admin/status` control endpoint. All six response envelopes name
`nvidia/nemotron-3.5-bolt-e4b`. Sources below are under
`%LOCALAPPDATA%/Nightshift/engine/` and were inspected read-only.

1. `js/engine/proxy.js:646–659` performs format/schema preflight, shared by the
   headless endpoint in `openai.mjs`. Request schema problems and unsupported
   adapters are distinguished from generated-output problems.
2. `js/engine/structured-output.js:224–351` parses the final content and checks
   types, required fields, enums, additional properties, composition and other
   supported constraints. Schema mismatch yields up to **8** `{path, message}`
   entries, e.g. a required-property path or expected/actual type. Invalid JSON,
   output-limit termination and filtering have separate codes. It does not repair
   content. The runtime's keyword coverage is not all of JSON Schema; unknown
   keywords may be treated as annotations.
3. `js/engine/orchestrator.js:992–1003` rejects a candidate's generated content,
   attaching `err.validation = {code, errors, finish_reason}`. The failed race
   result does not retain the provider response object. A separate final check at
   `3883–3895` checks content after later transformations. Thus validation can occur
   both before and after transformations; the old report does not identify which
   check rejected its output.
4. The single pinned-lane failure path at `2746–2782` preserves that error; a
   multi-lane aggregate can lose per-lane validation detail. Pinned generation was
   used here. `js/engine/proxy.js:608–641` exposes the structured error as
   `meterless.validation`, including its error list, and maps output violations to
   HTTP **502**. `openai.mjs` calls that formatter when generation has no accepted
   result. A request-schema refusal instead uses HTTP 400.

The old response's `schema_validation_failed` plus `finish_reason: stop` means
the runtime reported valid JSON failing a schema check, rather than a JSON parser
failure or a reported token-limit termination. It does **not** establish the
particular violated keyword, whether the initial candidate or final output failed,
or which upstream decoding mechanism was used.

## Why diagnostics were lost and what is now retained

ARGUS's Phase 3B.4 `_error_metadata()` read `meterless.validation.finish_reason`
but dropped `meterless.validation.errors`. Source inspection plus the retained
finish reason strongly indicate that detailed errors were exposed by the original
API path; the original HTTP body was not saved, so its exact error list is no
longer available to inspect. This is an ARGUS retention gap, not evidence that
Nightshift can report only the category.

The new diagnostic-only normalizer preserves safe details under:

```text
inference.meterless.structured_output.runtime_validation
```

It records availability, at most **8** errors, their category, safe path, violated
keyword, expected type/enum from ARGUS's own schema, and runtime-reported actual
type when present in a recognized runtime diagnostic. It preserves finish reason,
available integer completion usage and existing requested-model/HTTP metadata.
Actual values are unavailable in these runtime error messages and are not invented.

Only known schema property names and array indices 0–999 survive in paths.
Unknown/oversized paths stop at their nearest safe parent with `path_redacted`;
constraints of unknown leaves are not guessed. Expected constraints are explicitly
labelled `expected_from_argus_schema`. The code recognizes the installed runtime's
fixed messages for required/type/enum/additionalProperties/anyOf; unknown messages
remain a generic error with no keyword claim. Original messages, excerpts,
unexpected property names and arbitrary supplied values are never copied.
Absent detail remains `availability: unavailable`. A runtime-reported type is not
an independent inspection of a withheld completion. This normalization changes
neither acceptance nor inference requests.

Local recovery checked the old failed-attempt record, artifact directories,
logs/evidence records, signed receipt storage and runtime state. Failed attempt 11
has empty text, no new response artifact and no measured successful-completion
usage. Runtime state still contains four schema-failure message strings but no
retained structured validation detail (bounded scan, 4,488 nodes). Failure
bookkeeping counters must not be interpreted as actual zero inference usage.
No original raw completion or precise error path was recovered. No prompts,
model inputs, state keys or credentials were copied into recovery reports.

## Complete schema analysis

The unchanged `MODEL_OUTPUT_SCHEMA` is **3,092 bytes in compact JSON**. It has
**11 required top-level fields**, **7 top-level arrays**, **11 object occurrences**,
**12 array occurrences**, and maximum logical property/item depth **5**.
All object properties are required and every object has
`additionalProperties: false`. The objects are:

- Root: classification, confidence, executive_summary, key_findings,
  supporting_evidence, counterevidence, alternative_explanations, unknowns,
  recommended_next_steps, assessed_hypothesis, factual_claims.
- Citation: table, id, variant_id (three required strings).
- Finding: kind, statement, evidence (all required; evidence is a citation array).
- Fact claim: fact_id, kind, value, evidence (all required).
- Alternative: explanation, evidence, verification_needed (all required).
- Unknown: gap_ids, description (both required).

Classification has its three existing provisional values; confidence has
LOW/MEDIUM/HIGH; finding kind has OBSERVED_FACT/INTERPRETATION; claim kind has its
six existing evidence-fact values. There is one nullable union:
`factual_claims[].value` permits **string, integer or null** through `anyOf`.
Null is a permitted value, not permission to omit that property. There are no
optional properties, external references, recursive types or new custom keywords.
The wire schema permits empty arrays and strings; ARGUS's independent validators
add existing nonempty-text, citation, gap and factual checks.

[NVIDIA's exact-model reference](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3_5-bolt-e4b-infer)
documents `response_format.json_schema` and a thinking switch, but does not
establish exhaustive keyword/size guarantees for this complete contract.
[Meterless's guide](https://meterless.ai/help.html) advertises structured outputs
for the local runtime. The installed adapter forwards the schema unchanged and
labels NVIDIA capability `unverified`. Offline runtime validation and the live
results below establish tested support for these cases, not universal enforcement.

## Controlled probe results

Each probe used the same runtime/model, non-streaming strict `json_schema`, a short
synthetic prompt, temperature **0**, and requested `enable_thinking: false`.
The thinking switch's actual internal effect was not independently established.
No evidence bundle, operational dataset or ground truth was loaded by the probe
runner. HTTP status was **200**, finish reason **stop**, runtime validation
**passed**, and independent strict JSON/schema validation **passed in all six**.
The runtime reported capability `unverified` throughout.

1. **Minimal:** two required fields, enum, no extra properties; conflicting prompt
   demanded an invalid enum/extra field. Output obeyed the schema. Usage:
   **51 prompt / 19 completion / 70 total** tokens; cap 256.
2. **Nested object:** enum plus required citation object. Usage:
   **86 / 47 / 133**; cap 2,048.
3. **Findings:** finding and supporting-evidence arrays, nested citation arrays,
   finding enum, required keys and no extras. Usage **142 / 103 / 245**; cap 2,048.
4. **Facts plus findings:** claim arrays, six-value fact-kind enum, citations and
   `anyOf` exercised with string, integer and null. Usage **319 / 280 / 599**;
   cap 2,048.
5. **Unchanged full contract:** all 11 required fields/seven top-level arrays,
   copying a compact synthetic fixture. Usage **362 / 323 / 685**; cap 4,096.
6. **Unchanged full contract, generation:** interpreted one short synthetic success
   record with one supplied citation/fact. Usage **199 / 355 / 554**; cap 4,096.

Total reported usage: **1,159 prompt + 1,127 completion = 2,286 tokens**.
These were schema-compatibility probes, not fully validated ARGUS investigations:
synthetic citations/facts were not looked up against a real evidence bundle.
The last output classified a bare synthetic success as LIKELY_TRUE_POSITIVE/HIGH
relative to its hypothesis that execution succeeded; this illustrates why shape
conformance is not an investigative-quality verdict. The probe used brief protocol
instructions, not ARGUS's full reasoning prompt, so it is not a production quality
benchmark or ground-truth score.

## Failure mechanism, uncertainty and remedy comparison

**Established:** the earlier runtime gate rejected generated JSON as nonconforming;
ARGUS discarded safe detail that could have named the offending field. A general
inability to generate the full contract, or a specific unsupported nested/union
feature, is **not supported by these probes**.

**Most plausible working hypothesis:** request/content-dependent schema drift
under the production workload, rather than an intrinsically invalid or universally
unsupported schema. Confidence in this attribution is limited: the original output
is unavailable. Longer context, reasoning instructions, output length, sampling,
runtime transformations and upstream constrained-decoding behaviour remain
unisolated possibilities. The production call omitted the explicit temperature/
thinking overrides used by these probes and had a much larger input and output
budget. The probes changed several conditions and did not isolate any one cause.
Neither sampling nor thinking is a proven fix. `stop` is not schema correctness.

- **Keep the full schema unchanged:** best-supported choice now; the two full
  synthetic probes passed, with no identified incompatibility.
- **Simplify a nested structure:** no failing structure was found; changing one
  would be speculative and could lose evidence/provenance meaning.
- **Reduce redundant model fields:** ARGUS-owned metadata is already absent;
  selectively echoed facts remain verified. No measured formatting failure here
  establishes that further removal is necessary or safe.
- **Split interpretation:** adds calls and cross-output consistency obligations.
  Reserve this for a repeatable localized failure; no supporting evidence yet.
- **Switch output mode:** `json_object` constrains syntax only, and prompt-only
  generation is weaker. No alternative documented mode was verified to provide a
  stronger contract on this route. Do not silently downgrade or accept bad output.

**Recommended minimum:** retain the full contract and all gates; use the newly
preserved safe rejection details to guide the next separately authorized test.
Before changing inference settings or architecture, isolate one production/probe
difference at a time using controlled synthetic requests, then inspect the exact
constraint of any repeatable failure. No inference/schema remedy is demonstrated
yet. No simplification, split, settings change or alternative mode was implemented.

## Changes, tests and local artifacts

**280 tests passed**, including all 271 existing tests and 9 focused diagnostic
regressions. HTTP in automated tests is mocked. No dependency was added.

Changed: `scripts/reasoning_diagnostics.py` (bounded runtime detail normalization),
`scripts/reasoning_providers.py` (retain that normalization for errors). Added:
`tests/test_runtime_rejection_diagnostics.py` and this report. Assessment schemas,
acceptance rules, prompts, orchestration and datasets are unchanged.

Git-ignored local artifacts are under
`artifacts/assessments/phase3b5-probes/`: six `pNN.request.json`/`pNN.result.json`
pairs, `full-schema-audit.json`, `retention-check.json`, `diagnose.py`, and
`inspect_retention.py`. Synthetic response content is retained there; no credentials
or authentication headers are recorded. The runner counts each attempt before
dispatch, refuses to replay a spent case and enforces a six-request maximum.
No further inference was made after the six successful probes.

```powershell
$argusPython = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe"
& $argusPython -m unittest discover -s tests -v
# Offline schema audit only (no inference):
& $argusPython artifacts/assessments/phase3b5-probes/diagnose.py --audit
```

No installed runtime files, canonical/exported datasets, hidden ground truth,
historical evidence or assessments were modified. No commits or pushes were made.
