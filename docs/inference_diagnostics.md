# Inference failure diagnostics

This diagnostic-only extension changes neither assessment schema 2.0 nor its
acceptance rules. The existing validator still decides whether to reject a
response. Prompt construction, provider selection, inference requests and retry
behaviour are unchanged. No new inference provider or dependency is introduced.

## Schema errors

Failure reports identify `validation_stage` and retain the existing
`validation_stages` outcomes. `details.errors` contains at most **20** schema
errors in deterministic schema/array order. Each entry contains:

- `stage`: `schema`.
- `check`: stable codes such as `TYPE`, `ENUM`, `MISSING_FIELD`,
  `UNEXPECTED_FIELDS`, `VALUE_TYPE`, `ARRAY_LIMIT`, `TEXT_EMPTY` or `TEXT_LIMIT`.
- `path`: e.g. `assessment.key_findings[0].evidence`.
- `expected`: the expected type, allowed enum values or relevant constraint.
- `actual_type`: the Python JSON representation's type, or `missing`.
- `actual_value`: included only for null, booleans or public string literals
  already defined by ARGUS's assessment enums. Arbitrary strings, numbers,
  containers and excerpts are not copied into diagnostics.

Missing properties get their precise schema-owned path. Unexpected model-owned
property names are withheld: `UNEXPECTED_FIELDS` identifies the parent and a
count. This intentional privacy exception prevents credentials embedded as keys
from entering paths. The diagnostic traversal does not change, fill in or repair
the assessment. It uses the same validator to identify errors, stops after the
error bound, and inspects at most 1,000 items of an oversized array. The list is
not promised to be an exhaustive enumeration of every possible fault in an
invalid container. `errors_truncated` indicates additional discovered errors
beyond `error_limit`. Existing first-error fields remain available in `details`.

## JSON errors and model termination

JSON failures include the existing `check`, plus a stable `parser_category` and
syntax `line`/`column` when available. Categories distinguish expected values,
properties, colons, commas, unterminated strings, extra data, invalid escapes,
duplicate properties, non-JSON constants and input/encoding/size failures.
Parser messages containing source text are never included.

`details` also records:

- `response_empty`: true for empty/whitespace text, false for nonempty text, null
  if no string response was available.
- `appears_truncated`: true for a reported output-token limit or unclosed JSON
  string/container; null when there is no conclusive indicator.
- `truncation_basis`: fixed reason codes, including `reported_output_limit`,
  `unclosed_string_at_end` and `unclosed_container_at_end`.
- `truncation_is_heuristic`: distinguishes syntax-based indications from an
  explicit provider-reported limit. Neither establishes where truncation occurred.
- `syntax_scan_limited` and `syntax_scan_character_limit`: the syntax-indicator
  scan stops at **262,144 characters**. Larger responses have unknown syntax-based
  truncation status unless metadata independently establishes an output limit.

The boundary scan respects quoted strings and escapes; braces inside strings do
not count as unclosed JSON containers. It does not complete, extract or repair
JSON. A `stop` finish reason does not guarantee well-formed JSON, and incomplete
JSON does not establish that the configured output-token budget was exhausted.

The top-level `inference` object records allowlisted `finish_reason` and integer
`completion_tokens`, when the existing provider response supplies them. OpenAI's
`usage.output_tokens` is recognised alongside chat-completions'
`usage.completion_tokens`. Unknown or invalid values are represented as null with
an explicit availability/redaction status. Arbitrary metadata is not copied.
Provider errors raised before an `InferenceResponse` exists retain their existing
safe behaviour; unavailable termination metadata is not invented.

The failure report retains hashes and stage information, but no raw assessment,
response excerpt, unknown property name or untrusted enum value. Existing output
path/link guards and the configured-credential check still apply. A failed result
never becomes a validated assessment and never triggers automatic inference retry.

ARGUS does not log raw responses by default. This does not control Nightshift's
own local artifact retention: the runtime can retain outputs independently.

## Offline findings for the original two attempts

The original ARGUS reports contained hashes but no raw response text. Offline
investigation found both original responses in the existing Nightshift cache:

```text
%LOCALAPPDATA%/ai.meterless.runtime/jobs/job_mvtqr10001/artifacts/8/response.md
%LOCALAPPDATA%/ai.meterless.runtime/jobs/job_mvtqr10001/artifacts/9/response.md
```

Their byte hashes exactly match the original report hashes beginning
`4ee630741b419404` and `60af0ec1bad50d99`. Their corresponding cached attempt records
also contain runtime `tokens_out` counts. Neither cached record retains
`finish_reason`; this is reported as unavailable rather than guessed.

**Attempt 1:** JSON parsing succeeds. The only structural schema error found is
`assessment.evidence_complete`: expected enum `[false]`, actual boolean `true`.
This violates the existing bounded-evidence contract. It was not a schema-version
or citation error. Citation/gap and factual-consistency checks remain unexecuted;
the response was not corrected or accepted. The cached runtime output-token count
is **5,393**.

**Attempt 2:** The text is nonempty (9,095 characters). Parsing fails at EOF,
line **1**, column **9,096**, with category `EXPECTED_COMMA`. The `unknowns` array
(opened at zero-based character 8,838) and root object (opened at character 0)
remain unclosed; the response ends outside a quoted string. This establishes
incomplete JSON syntax, not its upstream cause. The cached runtime output-token
count is **4,391**. No assertion is made that an output-token limit caused it.
No delimiters were appended and no repaired interpretation was validated.

Enriched reports, including source-hash verification and offline provenance,
were written separately under:

```text
artifacts/assessments/phase3b2-diagnostics/
```

Original reports, source bundles, assessments and runtime cache files were left
unchanged. Raw cached responses were read locally and were not copied into ARGUS
or sent to any service. **No new inference or network request was made.**

## Verification

The full suite passed **223 tests** (205 existing plus 18 new regressions). Tests
cover the two original failure patterns, nested types, incorrect enums, missing
fields, multiple/bounded errors, truncation uncertainty, termination metadata and
credential-safe artifacts. Tests use synthetic text and mocked inference, without
depending on an installed runtime or its cache.

```powershell
$argusPython = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe"
& $argusPython -m unittest discover -s tests -v
```
