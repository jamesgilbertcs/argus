# Phase 3B.2: deterministic relationships and factual consistency

This extends the existing reasoning pipeline without retrieval, scenario-specific
conclusions or new dependencies. The collector and version 1.0 source bundles
remain unchanged. Production reasoning reads only the supplied bundle; regression
tests may load operational CSVs into mocked Zetaris fixtures. Neither the model
nor the relationship builder receives evaluation labels.

## Schema migration

New reasoning views, assessment wrappers and assessments use **version 2.0**.
Views preserve every existing record, original source identifier, variant, role,
decoded value, issue, reference and completeness limitation. They add
`relationships`. Assessments retain every previous field and add two required
properties:

- `assessed_hypothesis`: the specific security proposition being provisionally
  assessed. Supporting and counterevidence must be relative to this proposition.
- `factual_claims`: the machine-checkable observations described below.

The authoritative schemas remain in `reasoning_assessment.py`. New mock, OpenAI
and Meterless inference all request version 2.0. Providers still use the existing
interface, prompt constructor, schema and validation pipeline. Meterless remains
in the explicitly recorded `prompt_json` mode; no API capabilities are assumed.

Old assessments remain ordinary readable JSON. For deliberate archival validation,
`validate_assessment(old_assessment, view, allow_legacy=True)` performs the old
structural/citation/gap checks. It does **not** add claims, upgrade conclusions or
certify factual consistency. New inference rejects version 1.0 by default. The CLI
offers no legacy-inference mode. Existing artifacts are not migrated in place.

## Relationship derivation

`scripts/evidence_relationships.py` is a pure function over the admitted reasoning
view. It performs no I/O, SQL, compatibility decoding or IP-range matching. The
view constructor calls it after the existing provenance and decoder checks.
The same derivation is reused by factual validation, so editing the view's fact
catalog alone cannot authorise a contradictory claim.

The `relationships` object contains:

- `network_links`: links each alert/authentication/access record to its existing
  collector check, exact source IP and catalog citations. No lookup is repeated.
  The catalog IDs named by the check are resolved to their record variants and
  their IP fields must agree with the collector's exact IP. Similar prefixes
  never produce membership. Catalog rows, including inactive ones, remain visible
  in the original view records.
- `device_groups`: all authentication records sharing a nonempty reported device
  identifier, with at least two distinct event IDs. Equal identifiers are an
  observation, not proof of a physical device, operator identity or authorisation.
- `authentication_timeline`: all authentication source variants, their exact
  original timestamp strings and collector-verified UTC interpretations, or null.
- `authentication_pairs`: per-user adjacent pairs in verified chronology plus
  per-user adjacent explicitly referenced authentications. Unknown-time references
  use deterministic source ordering. Pair operands are then ordered by source
  reference, and signed deltas always mean **second minus first**.
- `session_access_links`: accesses, successful authentications sharing the exact
  session ID, resource citations, and separate owner/IP/time comparisons. Missing
  or ambiguous sessions/resources remain explicit; null comparisons are unknown.
  A resource relationship is not a permission grant or proof of benign activity.
- `facts`, `required_fact_ids`: stable facts and mandatory claim coverage.
- `issues`: inconsistencies or absent network checks, also added as view gaps.

The pair policy avoids quadratic all-pairs expansion. It is not a claim that every
possible pair was compared. All source records and device groups remain available.
The existing 200,000-byte canonical-view limit applies after relationship expansion;
overflow fails before inference without dropping evidence.

Network relationship statuses are:

- `MATCHED`: the collector reported catalog membership and its cited rows resolve
  consistently with that exact IP. This neither proves exhaustive lookup nor
  active network approval, legitimate activity or an authorised operator.
- `NO_EXACT_MATCH`: the collector's completed exact lookup returned no row.
- `INCOMPLETE`: the collector reports unavailable, truncated or other incomplete
  lookup. It is never converted into a negative match.
- `NOT_CHECKED`: no collector check is supplied for the record's source IP.
- `CONFLICTING`: duplicate checks or inconsistent/missing catalog references
  prevent a consistent relationship. Original checks and variants are retained.

UTC values reuse the collector's `timestamp_interpretation`. Missing, unverified
or conflicting timestamp evidence vetoes derived time comparisons; no timezone
is guessed. Integer microseconds are calculated directly from timedelta days,
seconds and microseconds without floating-point arithmetic. Original source
timestamp spelling remains unchanged. This retains the collector's existing
microsecond precision boundary; nanosecond support is not introduced here.

## Structured factual claims

Each catalog fact contains `fact_id`, `kind`, `value`, `evidence`, `details`, and
`required`. The ID hashes the canonical fact, including its exact source variants.
Each model claim has exactly these four fields:

```json
{
  "fact_id": "F<deterministic hash supplied in the view>",
  "kind": "NETWORK_MATCH_STATUS",
  "value": "NO_EXACT_MATCH",
  "evidence": [
    {"table": "ARGUS_SECURITY.authentication_logs_compat_test", "id": "<source ID>", "variant_id": "<original variant>"}
  ]
}
```

The limited supported kinds are:

- `NETWORK_MATCH_STATUS`: the derived lookup status for an authentication source.
  Its provenance includes that authentication plus the collector-linked catalog
  records, if any. Negative or incomplete claims do not gain unrelated catalog IDs.
- `SHARED_REPORTED_DEVICE_ID`: the exact shared reported identifier for a selected
  pair, or null. Details distinguish shared, different and missing identifiers;
  null is not automatically proof of different physical devices.
- `AUTHENTICATION_OUTCOME`: exact `SUCCESS` or `FAILURE`, or null if unavailable
  or invalid in the supplied record.
- `AUTHENTICATION_TIMESTAMP`: exact original timestamp text, or null when absent.
- `AUTHENTICATION_UTC`: collector-verified normalised UTC, or null if unverified.
- `AUTHENTICATION_DELTA_MICROSECONDS`: signed exact integer difference for a
  selected pair, or null when either timestamp cannot be verified.

The model must include every `required_fact_ids` entry exactly once. Required
facts cover explicitly referenced authentications and selected pairs for which
both operands are explicitly referenced. Contextual authentication facts may be
included too. This finite coverage rule prevents omission of a known referenced
network/device fact without requiring an enormous restatement of all history.
Session and resource links are interpretation context, not additional claim kinds.

Validation rejects unknown facts, duplicate claims, wrong kinds, mismatching
values/types, substituted or incomplete evidence arrays and missing mandatory
claims. All citations include table, ID and variant; order inside a claim's
evidence array is immaterial, but its complete membership must match the fact.
Integers are not coerced from decimals, strings or booleans. IDs and values must
come from the fact catalog, not from the model's interpretation of similar IPs.

These checks operate on structured claims only. They do not search prose with
regular expressions, verify intent, prove an account uncompromised, calibrate
confidence, check arbitrary working-hours assertions or adjudicate every resource
permission. A model can copy correct claims and still write inconsistent prose.
Classification, hypothesis choice and narrative require human review.

## Failure handling and local diagnostics

See the [diagnostic reference and offline root-cause findings](inference_diagnostics.md)
for bounded multi-error schema reports, parsing/termination metadata and the
subsequently recovered original responses. The assessment schema and acceptance
rules are unchanged.

The output wrapper records four validation stages: `json_parsing`, `schema`,
`citations_and_gaps`, and `factual_consistency`, each passed, failed or not_run.
All must pass before writing an assessment as validated.

A returned assessment that fails writes only a diagnostic artifact:

```text
<output-dir>/<alert_id>.<provider>.failure.<response-sha256>.json
```

It contains the failed check/stage, a trusted schema-field path or safe JSON
error category (with syntax line/column when available), a claim index and trusted fact ID where
available, input/view hashes, provider/model identity and a response-text hash.
It explicitly states `validated_assessment_written=false`. It does not contain
the raw model response, untrusted claim values/IDs, model reasoning, credentials
or a repaired assessment. A developer can reproduce the reasoning view from the
input bundle and use the fact ID/index to inspect the failed comparison locally.
The response hash identifies decoded model text, not the HTTP envelope; UTF-8
surrogate-pass encoding is used for hashing malformed surrogate text safely.

Paths use the existing protected-directory and link guards. The configured
credential is checked before diagnostic writing. Errors never trigger a retry,
changed conclusion, new query, response repair or provider substitution. Transport
failures before any model response keep the existing static error behaviour.
Diagnostics are kept under the ignored assessment-artifact directory by default.

Existing successful artifacts are not deleted on a later failure. Use a separate
output directory for an evaluation, and check the current exit status and hashes
rather than assuming an older file represents the latest attempt.

## Run and reproduce

```powershell
$argusPython = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe"
& $argusPython -m unittest discover -s tests -v
& $argusPython scripts/reason_evidence.py artifacts/evidence/ALT0018.json --provider mock --output-dir artifacts/assessments/phase3b2-mock
```

For Meterless, configure the existing three settings as described in the
[provider guide](evidence_reasoning.md#meterless-nightshift-provider). The runtime
may use NVIDIA cloud inference; localhost does not mean evidence stays on-device.

```powershell
$env:METERLESS_BASE_URL = 'http://127.0.0.1:8088/v1'
$env:METERLESS_RUNTIME_TOKEN = [IO.File]::ReadAllText("$env:LOCALAPPDATA\ai.meterless.runtime\proxy-token").Trim()
$env:METERLESS_MODEL = 'nvidia/nemotron-3.5-bolt-e4b'
$env:ARGUS_MAX_OUTPUT_TOKENS = '12000'
& $argusPython scripts/check_meterless.py
& $argusPython scripts/reason_evidence.py artifacts/evidence/ALT0018.json --provider meterless --output-dir artifacts/assessments/phase3b2
```

The explicit 12,000-token cap accommodates the added claims during this evaluation;
it remains below the existing 32,000 maximum. It is not an automatic retry policy.
No credential file is changed by these assignments. An unavailable runtime or
invalid response should be reported as such, independently of unit-test results.

## Verification on 2026-11-06

The complete unit suite passed **205 tests**, retaining all 177 previous tests.
The additional 28 tests cover generic relationships, exact/missing/incomplete
network evidence, variants, device identifiers, precise time ordering, claim
contradictions, explicit legacy handling and safe staged failure reports.

The ALT0018 regression fixture reads only operational compatibility CSVs into the
existing mocked Zetaris collector. It verifies that `198.51.100.51` has no exact
catalog match and does not acquire `N003` (`198.51.100.10`), that `203.0.113.10`
matches `N005`, and that `AUTH000962` / `AUTH000963` share reported identifier
`DEV-U001-01` with a 360,000,000-microsecond interval. A structured positive-match
claim for the unmatched authentication is rejected. No evaluation labels are read
by these new tests or the reasoning workflow.

The existing Nightshift runtime answered authenticated discovery for
`nvidia/nemotron-3.5-bolt-e4b`. Two explicitly initiated real ALT0018 evaluations
were made with prompt-only JSON and the 12,000-token cap:

1. JSON parsing passed; assessment schema validation failed. Citation/gap and
   factual-consistency checks were not run. The original report did not retain
   the exact invalid field. Safe schema-field-path diagnostics were subsequently
   added and instructions clarified that the schema version is a JSON string
   and fact kinds are copied from the catalog.
2. With the clarified instructions, strict JSON parsing failed. Schema,
   citation/gap and factual-consistency checks were not run. At that time the
   precise syntax defect was unknown because ARGUS had not retained raw output;
   the later offline cache investigation is documented in the diagnostic guide.

Both attempts wrote only failure diagnostics under
`artifacts/assessments/phase3b2/`; no validated assessment was written there.
The reports have response hashes beginning `4ee630741b419404` and
`60af0ec1bad50d99`, respectively. These are live failures, independently of the
passing mocked tests. Neither proposed classifications/confidences nor complete
raw model narratives were retained by ARGUS, so classification, confidence, recognition
of the VPN/shared-device explanation and remaining prose errors are **not
verified** for a new live assessment. Nightshift's own cached raw responses were
subsequently found and used solely for offline failure analysis, without accepting
or repairing either assessment. The old version 1.0 assessment must not be
mistaken for the result of this evaluation.

Checksums confirmed the source bundle and previous assessment were unchanged.
No credential files, datasets or ground truth were modified; no hidden answers
were supplied to inference. There was no automatic retry, provider/model switch,
weakened validation or extraction/repair of invalid output.

The remaining integration limit is Nemotron's reliability at emitting this
prompt-only version 2.0 contract. A future explicit evaluation can investigate
verified server-side schema support, model capacity or a smaller output contract.
Until a new live assessment passes and its prose is manually reviewed, do not
describe these unit-test results as a successful hardened model investigation.
