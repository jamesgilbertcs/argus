# Phase 3B.7: temperature isolation experiment

Run on 7 November 2026 against the existing Nightshift 1.2.7 installation.
**Exactly three new inference POSTs were made, once per case, without ARGUS
retries, response repair, fallback models or a permanent temperature change.**

**Result:** explicit temperature 0 is accepted as a request parameter and preserved
by the installed adapter, but it did **not** produce schema-conforming output in
either new temperature-0 call. All three calls were rejected by Nightshift's
post-generation schema gate. This experiment provides no observed schema-acceptance
improvement from lowering temperature; it does not establish that temperature has
no effect on generation or that another setting caused these failures.

## Controls and preflight

The existing model, `nvidia/nemotron-3.5-bolt-e4b`, was advertised by authenticated
`GET /models`. This was a connectivity check, not an additional inference.

Read-only source inspection and an offline replay of the installed NVIDIA adapter
with stubbed fetch confirmed that explicit **0 stays 0**, explicit **0.7 stays 0.7**,
and messages/schema/token budget are unchanged. The adapter uses nullish defaults,
not a truthiness fallback that would replace zero. Previous retained full-schema
probes also demonstrated successful API use at 0. The live API does not echo its
effective backend sampling temperature; its internal application cannot be
independently verified from these responses.

The experiment uses the existing `MeterlessProvider` and `reason_bundle` pipeline.
A local, Git-ignored experiment subclass adds **only** `temperature` at the existing
HTTP boundary, before delegating to the unchanged provider transport. It refuses a
second dispatch for a case, records each spent case before HTTP dispatch, limits
the run to three, and compares every payload with an offline-prepared request.

All new requests used:

- The unchanged full `MODEL_OUTPUT_SCHEMA` interpretation contract for final schema
  3.0 and `strict: true`, `json_schema`, name `argus_assessment`.
- The unchanged production system prompt and provider suffix, with the existing
  `untrusted_evidence` user-message construction; two message roles, system/user.
- Explicit model selection, **12,000 output tokens**, **180-second timeout**,
  non-streaming, app `argus`, scope `user`.
- No explicit thinking switch, reasoning budget, top-p, seed, tool configuration
  or additional inference options. Existing authentication, redirect/proxy
  protections and bounded response handling remain in place.

Stage A used the same fixed synthetic evidence bundle from the generic
`tests.test_reasoning_contract.completed_bundle()` fixture in both calls, projected
through the existing reasoning-view builder. No scenario evaluation labels were
loaded or supplied to inference. The two prepared requests compare equal after
removing only `temperature`. The fixture's placeholders/missing context remain
visible as evidence limitations; no expected assessment was supplied to the model.

Stage B used the original `artifacts/evidence/ALT0018.json`. Removing `temperature`
from its prepared request gives exact object equality with the Phase 3B.6 production
request reconstruction. Canonical hashes match the prior failed production call:

- Bundle: `ea27ef9f637468bc5506b13d005e6b8d0e05f1b2efbc28f8d0b7d2136e001089`.
- View: `224830e2289e386b817f83ead950f62d7ad8987608f19d72a0512919b6a3cddd`.

The historical HTTP body was not retained, so equality with that original wire
request cannot be proven beyond the saved metadata and reconstructed inputs.

## Results for every call

All requested the model and strict mode above. Actual answering model identifiers
were withheld with the failed completions, so the configured/requested identifier
must not be described as a verified model echo. Effective live temperature was
not echoed; the installed forwarding behavior was verified offline for every case.

| Case | Evidence | Requested temperature | HTTP/provider outcome | Finish | Measured token usage | Validated assessment |
|---|---|---:|---|---|---|---|
| A0 | Fixed synthetic bundle | 0 | 502 `schema_validation_failed` | `stop` | Unavailable | No |
| A07 | Same synthetic bundle | 0.7 | 502 `schema_validation_failed` | `stop` | Unavailable | No |
| B0 | Original ALT0018 bundle | 0 | 502 `schema_validation_failed` | `stop` | Unavailable | No |

The typed runtime error means Nightshift reported **valid JSON that failed schema
validation**. ARGUS did not receive that JSON and could not independently parse or
validate it. For all three calls, ARGUS stages were:

- JSON parsing: **not_run**.
- Model interpretation schema: **not_run**.
- Citations and evidence gaps: **not_run**.
- Structured factual consistency: **not_run**.
- Deterministic final assembly and final-schema validation: **not_run**.

No raw completion was returned or retained. There is no accepted classification,
confidence or investigative reasoning to review, including for the network,
device and time relationships in ALT0018. No quality assessment is inferred from
HTTP status, stop reason or the count of reported errors.

## Exact available rejection details

**A0 and B0** both report seven `MISSING_FIELD` errors, keyword `required`:

1. `$.classification` — string, one of `LIKELY_TRUE_POSITIVE`,
   `LIKELY_FALSE_POSITIVE`, `INCONCLUSIVE`.
2. `$.key_findings` — array.
3. `$.supporting_evidence` — array.
4. `$.counterevidence` — array.
5. `$.alternative_explanations` — array.
6. `$.unknowns` — array.
7. `$.recommended_next_steps` — array.

Their eighth reported error is `UNEXPECTED_FIELD`, keyword
`additionalProperties`, safe parent path `$`, with the safely retained property
name **`assessment_classification`**. The schema requires `classification` and
forbids additional root properties. The unknown property's value is unavailable;
no claim is made about its content or about the complete rejected object shape.

**A07** reports eight `MISSING_FIELD` / `required` errors: the same seven paths
above, plus **`$.executive_summary`**, expected string. No unexpected-property
name appears in the eight returned errors. This is **not** evidence that there
were no additional properties: the installed validator caps its error list at
eight before ARGUS receives it. ARGUS's `errors_truncated: false` means it did not
further truncate the list received, not that runtime validation was exhaustive.

Actual values and actual types are unavailable for these required/extra-property
messages. Full bounded, sanitized error entries are saved in each case's result
and failure report. No arbitrary runtime message, credential, raw completion or
authentication header is included.

## Comparison and interpretation

The previous ALT0018 production attempt used an omitted temperature that the
installed default adapter path resolves to 0.7. It also returned HTTP 502,
`schema_validation_failed`, `stop`, the same seven missing required fields and
an unexpected property whose name was redacted at the time. Its effective backend
temperature, complete request and rejected completion were not retained.

The new explicit-zero ALT0018 attempt therefore did **not** improve the observed
acceptance outcome. The new diagnostics identify `assessment_classification` for
this attempt; they cannot recover or establish the old unknown property's name.

Stage A reached generation for both settings and produced output-schema errors,
not an unsupported temperature/model/request-schema error. It did not establish a
temperature-parameter incompatibility, so the one authorized Stage B call proceeded.
“Parameter-compatible” here is distinct from “produces a validated assessment.”

The new synthetic failures also show that failure does not require ALT0018's full
evidence volume: the unchanged production instructions plus a smaller synthetic
bundle reproduced schema rejection at both temperatures. Earlier successful
synthetic probes used different, shorter instructions and different evidence and
request options. These observations narrow useful future comparisons, but do not
prove a prompt conflict, broken constrained decoding, runtime interference or a
specific causal factor.

Only one sample per new condition was observed. Order, time, runtime state and
upstream behavior were not controlled; the historical real-evidence comparator
occurred at a different time. Temperature 0 also need not imply full reproducibility
across model-service executions. No reliability rate or causal conclusion follows.
There is no evidence-supported permanent temperature remedy from this experiment.

## Artifacts, tests and unchanged inputs

New local artifacts are under `artifacts/assessments/phase3b7-temperature/`:

- `a0.request.json`, `a07.request.json`, `b0.request.json`: payloads only, no headers.
- Three `.spent.json` markers and three `.result.json` reports.
- `a0/`, `a07/`, `b0/`: unchanged-pipeline, sanitized failure artifacts.
- `synthetic.bundle.json`, `preflight.json`, `forwarding-check.json`.
- `experiment.py`, `check_forwarding.cjs`: experiment-only helpers, not a production
  provider or permanent configuration change. Spent cases refuse further dispatch.
- `unittest.log`: complete mocked-suite output.

A first local invocation for A07 stopped **before dispatch** because the harness
compared binary float 0.7 with the evidence parser's exact decimal 0.7. The harness
was corrected to use the standard-library wire JSON parser for that request
comparison. No spent marker or HTTP request existed for that invocation. The later
A07 invocation was its sole inference attempt, not a retry of a live request.

**287 tests passed** in the complete automated suite; external inference was
mocked. No production code or tests were changed in this phase, and no new
dependency was introduced. The only tracked addition is this report. No canonical
data, ground truth, historical artifacts, evidence bundles, schema, prompt,
validator, installed runtime source or credential file was modified. No commit or
push was made. No further inference was performed after B0.

Automated tests can be repeated without inference:

```powershell
$argusPython = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe"
& $argusPython -m unittest discover -s tests -v
```

Review the retained payloads/results offline; do not replay spent inference cases
or treat this experiment runner as the normal reasoning CLI.
