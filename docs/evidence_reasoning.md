# Phase 3B: evidence-grounded provisional reasoning

This phase interprets an existing Phase 3A JSON bundle. It never queries Zetaris,
reads local operational CSVs, reads evaluation answers, executes suggested actions,
or asks a model to retrieve evidence. Collection and reasoning remain separate.
All source records originate in the supplied bundle; its original contents remain
unchanged. No third-party Python dependencies are required.

## Run it

From the repository root, with an existing bundle:

```sh
python scripts/reason_evidence.py artifacts/evidence/ALT0018.json --provider mock
python -m unittest discover -s tests -v
```

On Windows using the installed Python:

```powershell
$argusPython = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe"
& $argusPython scripts/reason_evidence.py artifacts/evidence/ALT0018.json --provider mock
& $argusPython -m unittest discover -s tests -v
```

An arbitrary valid alert ID is taken from the input bundle. Provider selection is
required; there is no automatic mock fallback. Zetaris need not be running.
The default UTF-8/LF outputs are:

```text
artifacts/assessments/<alert_id>.<provider>.assessment.json
artifacts/assessments/<alert_id>.<provider>.view.json
```

`--output-dir` designates another assessment directory. Only these two fixed
filenames are written, after successful inference and validation. Existing outputs
at these paths are replaced. Source evidence cannot be overwritten; data,
evaluation, code, documentation, Git and the evidence-artifact directory are
protected. Linked destination files are refused. The CLI returns 0 on success,
1 on invalid input/configuration, provider/validation or I/O failure, and 2 for
argument errors. Output writes are separate operations, not a two-file transaction;
check both files and their hashes after an I/O interruption.

## Components

- `reasoning_view.py`: pure bundle validation, provenance verification,
  compatibility normalisation and deterministic view construction.
- `reasoning_prompts.py`: fixed higher-priority instructions and a JSON user
  payload containing untrusted evidence.
- `reasoning_assessment.py`: machine-readable `ASSESSMENT_SCHEMA`, strict JSON
  parser and deterministic structural/citation/coverage validation.
- `reasoning_providers.py`: minimal `ReasoningProvider.generate(prompt, schema)`
  interface returning `InferenceResponse`; explicit mock and OpenAI adapters.
- `reason_evidence.py`: orchestration, CLI and designated artifact writing.

`reason_bundle(bundle, provider)` returns `(view, result)`. The provider receives
only the fixed prompt, serialised view and output schema. It receives no Zetaris
client, credentials for Zetaris, query capability, filesystem handle or tools.

## Reasoning-view schema, version 1.0

Only Phase 3A bundle version `1.0` is accepted. Unknown bundle/envelope fields are
refused for review rather than silently dropped. The view contains:

- `schema_version`, `alert_id`, `bundle_sha256`: the hash is SHA-256 of the
  collector's canonical, sorted-key JSON representation of the entire source
  bundle, not a hash of its formatting or on-disk bytes.
- `selection_policy`: `all_records_all_variants_no_truncation`. Every supplied
  source record, every field, every conflicting variant and every role survives.
  Directly referenced, contextual, session, metadata and network roles remain
  distinct. Record order is deterministic by canonical source reference.
- `collection_status`, `scope`: original collection status, bounds, options and
  subject-expansion policy. Successful bounded collection is never exhaustive.
- `records`: a flat array of objects with `source`, `roles`, `values`, optional
  `timestamp_interpretation`, and, when necessary, `timestamp_observations`.
  `source` is exactly `{table, id, variant_id}`; table names retain the full
  `ARGUS_SECURITY.*_compat_test` names. `values` retains original column names.
  Source primary-key fields are retained inside `values` as well as in `source`.
- `references`, `network_checks`, `issues`: original references (including
  duplicate positions, unresolved statuses and variant links), exact network
  lookup results and collection issues.
- `query_completeness`: query IDs, scope, status, limits, counts, error types,
  unusable rows and unreviewed metadata markers. Repeated SQL, column headers and
  routine timestamp projections are excluded. Wire trace files are never read.
- `gaps`: deterministic `{gap_id, kind, details}` objects covering bounded scope,
  partial status, every collection issue, unresolved references, incomplete
  queries/network checks, unreviewed data, absent alerts, variant conflicts and
  normalisation failures. IDs hash the kind/details. Repeated identical gaps
  share one ID. These are limitations, not maliciousness labels.

Source `variant_id` must match SHA-256 of the original encoded record using the
collector's canonical serialisation, and its table/key/ID must agree. Hashes detect
accidental inconsistency; they do not authenticate the bundle or a server. The
view does not recalculate variant IDs from decoded values. Exact source citations
can therefore be resolved back to the unmodified bundle.

### Normalisation and compaction

The existing `export_zetaris.decode_array` and `decode_text` functions are reused,
with the exporter's field allowlists:

- Arrays: resources' `allowed_departments`; alerts' `related_event_ids`.
- Text: users' `display_name`, `role`, `primary_location`; resources'
  `resource_name`; approved networks' `description`, `advertised_location`;
  authentication `reported_location`; alert `description`.

Pipe-framed arrays become JSON lists in their original order. Percent escapes
are decoded exactly once. Spaces, slashes, delimiter collisions and empty elements
survive; an empty encoded array becomes `[]`, while missing values remain `null`.
Malformed encoded values stay raw and produce a gap. Other source fields remain
unchanged, including integer values, strings containing numbers, tagged exact
decimals, nulls and timestamps. Location strings are reported/advertised claims,
not assertions of physical location. No numeric coercion or timezone guess occurs.

Verified per-record UTC interpretations remain attached. Conflicting or unverified
timestamp projections are retained as distinct `timestamp_observations`, with a
review gap; unmatched projections are retained as gaps. Only redundant routine
projections are omitted. The original bundle remains the authority for full query
membership, SQL and transport provenance.

The complete compact view is limited to **200,000 UTF-8 bytes** by default, measured
using canonical JSON, with an 8 MiB input-file bound. `--max-view-bytes` can change
the former. Oversized views fail before inference; records are never ranked,
sampled, chopped or dropped to fit. This byte limit is not a token estimate or
a guarantee that a chosen model's context window is sufficient. There is no
automatic chunking, retrieval, retry or provider substitution.

## Assessment schema, version 1.0

The authoritative JSON Schema is `ASSESSMENT_SCHEMA` in
`scripts/reasoning_assessment.py`. All object properties are required and extra
properties are forbidden. The assessment contains:

- `schema_version`: `1.0`; `alert_id`: exactly the bundle's alert ID.
- `classification`: `LIKELY_TRUE_POSITIVE`, `LIKELY_FALSE_POSITIVE`, or `INCONCLUSIVE`.
- `confidence`: `LOW`, `MEDIUM`, or `HIGH`.
- `executive_summary`: nonempty short text, at most 1,000 characters.
- `key_findings`: findings with `kind` (`OBSERVED_FACT` or `INTERPRETATION`),
  `statement`, and nonempty `evidence` citation arrays. Supplied records require
  at least one finding. An entirely empty collection may have none.
- `supporting_evidence`: source citations; nonempty when findings exist.
- `counterevidence`: mitigating findings in the same cited finding format;
  an empty array is allowed.
- `alternative_explanations`: at least one object containing `explanation`,
  `evidence`, and `verification_needed`. Uncited alternatives must be hypothetical,
  as instructed in the prompt.
- `unknowns`: objects containing `gap_ids` and `description`. Every supplied
  gap ID must appear; invented gap IDs are rejected. Additional analyst unknowns
  may use empty `gap_ids`. The descriptions should explain the limitation.
- `recommended_next_steps`: at least one nonempty suggestion. They are text only
  and are never executed.
- `collection_status`: exactly the view's value; `evidence_complete`: always false.

Each citation is `{table, id, variant_id}` and must match a supplied record exactly.
All citation-bearing sections are validated. Empty findings, invalid enums, missing
fields, unknown fields, malformed JSON, duplicate keys, non-JSON constants and
fabricated source/variant references are refused. General text fields are limited
to 6,000 characters and arrays to 1,000 elements. With no alert record, only
`INCONCLUSIVE` with `LOW` confidence is accepted. This is an evidence-availability
rule, independent of any scenario.

The output wrapper adds `provider` (`name`, `model`, `mock_generated`, operational
`metadata`), bundle and view hashes, and a validation notice. Provider identity is
set by application code, not by the generated assessment. Private model reasoning
and raw provider errors are not persisted. Mock output is labelled in its provider
metadata, summary, filename and CLI message.

## Real provider: verified protocol, no live result yet

The dependency-free OpenAI adapter implements the documented Responses REST API:
`POST https://api.openai.com/v1/responses`, Bearer authentication, `model`, fixed
`instructions`, a JSON evidence string in `input`, `max_output_tokens`, and
`text.format` with `type=json_schema`, `name`, `strict=true` and the assessment
schema. See the official [text generation guide](https://developers.openai.com/api/docs/guides/text)
and [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).

Responses must be `completed`, without error/incomplete markers. The adapter reads
assistant `message` items and one `output_text` content item from `output`. It
rejects refusals, incomplete responses, tool/action items, multiple assessment texts,
HTTP errors and malformed responses. Optional private `reasoning` items are not
included in the assessment. Response ID, model identity, usage and service tier,
when supplied, are retained as operational metadata. These protocol checks are
covered with mocked HTTP; no undocumented model-specific request options are used.

`store=false` disables response storage as described in the official
[Responses migration guide](https://developers.openai.com/api/docs/guides/migrate-to-responses).
It does not mean the inference request is local or constitute a broader retention
guarantee. Explicit `--provider openai` sends the reasoning view to OpenAI.

Set these in the process environment or the existing ignored `.env.local` using
your own values, without sharing credentials in chat or committing them:

```dotenv
OPENAI_API_KEY=<your-openai-api-key>
ARGUS_OPENAI_MODEL=<your-supported-model-id>
# Optional:
ARGUS_INFERENCE_TIMEOUT_SECONDS=60
ARGUS_MAX_OUTPUT_TOKENS=6000
```

Select a model supporting Responses and strict Structured Outputs. There is no
default model. The existing literal dotenv reader is reused without interpolation
or environment mutation; process variables override file settings. Credentials are
excluded from config representations, diagnostic messages and output. No credential
or endpoint setting from Zetaris is sent to the inference service. The endpoint
is fixed HTTPS with normal TLS checks, redirects disabled, and environment proxies
disabled. Timeout is bounded to 1–600 seconds and output tokens to 256–32,000;
responses larger than 2 MiB are refused.

Run a real inference explicitly:

```sh
python scripts/reason_evidence.py artifacts/evidence/ALT0018.json --provider openai
```

Inspect the generated assessment, provider identity, cited variants, gap
acknowledgements and its view before using it. Missing configuration, authentication,
context-window, unsupported-model/schema, refusal, timeout and validation errors all
fail closed. Errors never trigger a mock assessment. Increase a limit only after
reviewing the input size or output truncation cause; no data is silently truncated.

During this implementation no OpenAI key/model configuration was present. No live
inference was attempted, and there is no verified live-model result. The supplied
ALT0018 example is **mock-generated over an existing collected bundle**, not a new
live Zetaris query or model investigation. Mock always returns a generic
inconclusive demonstration and contains no planted-scenario classification logic.

## Grounding limits and security scope

Structural validity and existing citations do **not** establish that cited records
support the accompanying statements. A model can misread evidence, use weak
citations, omit relevant facts from its narrative or make unsupported claims in
free text. Gap-ID coverage ensures acknowledgement, not a meaningful explanation
of every gap. Human review remains essential; there is no semantic verifier,
ground-truth scoring or calibrated probability model in this phase.

Evidence strings are untrusted data in a lower-priority JSON payload, never prompt
instructions. The prompt asks for balanced interpretations, calibrated confidence,
cited facts, hypotheses, missing evidence and cautious handling of geolocation,
approved networks, authentication failures and transfer volumes. Injection tests
verify separation and the lack of execution paths; they do not prove a model will
always resist injection. A provider can generate text but cannot execute commands,
construct executed SQL, retrieve evidence or choose output paths. Recommendations
remain inert text. This boundary assumes trusted application/provider code; a
malicious Python program with token access is outside it.

Mock construction, view construction and validation are deterministic. Real model
outputs are not promised to be reproducible. Neither mode concludes that a person
is guilty, innocent or definitively compromised. No database objects, source
datasets, exports, ground truth or existing evidence bundles are modified.
