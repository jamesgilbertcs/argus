# Phase 3B.3: interpretation and deterministic assessment assembly

New inference uses the **interpretation_v1** model contract and writes **3.0**
assessment wrappers/final assessments. Source bundles remain **1.0** and reasoning
views remain **2.0**, byte-for-byte unchanged for the same bundle. No evidence,
compatibility decoding, network lookup or source provenance algorithm changes.

## Responsibilities and contracts

`scripts/reasoning_assessment.py:MODEL_OUTPUT_SCHEMA` is the authoritative strict
model-output JSON schema. Every listed field is required; extra fields are rejected.
It retains the existing types, enum values and citation structures for:

- `classification`, `confidence`, `executive_summary` and `assessed_hypothesis`.
- `key_findings`, `supporting_evidence`, `counterevidence` and
  `alternative_explanations`.
- `unknowns`: investigative uncertainties, with `description` and `gap_ids`.
  Supplied gap IDs may be cited when relevant; empty lists are allowed for other
  investigative unknowns. Invented IDs still fail validation.
- `recommended_next_steps`.
- `factual_claims`: selected machine-checkable assertions, retaining exactly
  `fact_id`, `kind`, `value`, `evidence` and the six existing supported fact kinds.

The model does **not** return `schema_version`, `alert_id`, `collection_status` or
`evidence_complete`. Nor does it construct hashes, source inventories, deterministic
relationships or collection limitations. Returning these unsupported properties
fails validation; ARGUS never strips them to obtain an acceptable response.

The supplied schema and revised system instructions are shared by mock, OpenAI
and Meterless providers. OpenAI still requests strict JSON-schema output;
Meterless still uses `prompt_json`, without assuming server-side enforcement.
The mock remains explicitly labelled and contains no incident-classification logic.

## Facts and citations

The model should assert the relevant supported catalog facts when using those
facts in its findings or summary. Each submitted claim must match the recomputed
fact's ID, kind, exact value **and type**, and complete source/variant citations.
Unknown or duplicate claims, wrong kinds/values/citations and invented references
fail. Contradictions are never repaired, overwritten or retried.

Version 2.0 required the model to copy all `required_fact_ids`. The new contract
does not require restating unused deterministic facts or restating every gap.
The complete relationship catalog and every gap instead survive deterministic
assembly regardless of the model's selections. Historical `required` flags remain
in the unchanged reasoning view for archival compatibility; they are not a copying
requirement for interpretation_v1. An empty claims list is valid when the model
makes no assertions covered by those six fact kinds.

This is a contract migration, not a relaxation of claim-value or citation checks.
As before, arbitrary prose is **not** mechanically fact-checked. ARGUS cannot
establish whether the model has supplied a structured claim for every checkable
assertion in its prose, whether an interpretation follows from cited records, or
whether it chose the best hypothesis. Human review remains mandatory.

Network/device/time/outcome facts are recomputed using the existing relationship
builder, not trusted from an edited fact catalog. Session owner/IP/time and resource
associations remain in the deterministic relationship output with provenance;
they are context, not new supported model-claim kinds. No approximate IP matching
or second decoding implementation is introduced. Matching reported device IDs do
not prove physical-device or actor identity, and network membership does not prove
legitimate activity.

## Completeness policy

`reasoning_assembly.collection_metadata(view)` uses policy
`reported_bounded_plan_v1`. It distinguishes three questions:

1. `collection_succeeded` is true exactly when the collector reports `collected`.
   It is the collector's reported status, not an independent audit of its plan.
2. `within_scope_completeness` describes the **reported bounded collection plan**.
   `INCOMPLETE` means a non-collected status or at least one supplied gap other
   than `bounded_scope` (e.g. missing, conflicting, unavailable, unreviewed or
   potentially truncated evidence). Otherwise `UNKNOWN` means no query receipts
   or no declared history window. Otherwise it is `COMPLETE` for that reported
   scope only. `completeness_basis` records the reason using fixed codes.
3. `investigation_sufficiency` is `NOT_DETERMINED`. Successful or scope-complete
   collection cannot establish that all evidence needed to resolve a case exists.

The final `assessment.evidence_complete` is a compatibility field for question 2:
true / false / null for COMPLETE / INCOMPLETE / UNKNOWN. **Its meaning changes from
version 2.0's constant false**. Consumers must inspect the version and must never
read a true value as proof that the investigation is resolved. A bounded window
remains a limitation even when collection within it is complete. Missing query
receipts are not replaced with invented certainty. These rules apply to every
bundle, with no alert-specific exceptions.

## Final JSON contract and provenance

`reasoning_assembly.assemble_assessment(content, view, response)` is independently
testable, revalidates interpretation before assembly, and never mutates inputs.
It adds no clock time or random identifiers. Identical content, view and provider
response produce byte-identical output under the existing serializer. Actual live
model outputs and service request IDs may of course differ between requests.

The final wrapper has:

- `schema_version: "3.0"`, `model_output_contract: "interpretation_v1"`.
- Existing `bundle_sha256` and `reasoning_view_sha256` using canonical JSON hashes.
- `assessment`: the model content **unchanged**, plus ARGUS-assigned
  `schema_version: "3.0"`, `alert_id`, `collection_status`, `evidence_complete`.
- `collection`: policy and three completeness distinctions above; full `scope`,
  `limitations` (every view gap, including bounded scope), `issues`, `references`,
  `network_checks`, and `query_completeness`. Model unknowns cannot remove gaps.
- `relationships`: the complete recomputed facts, network links, device groups,
  authentication timeline/pairs and session/resource associations from the view.
- `provenance`: original view version, selection policy and every original source
  `{table, id, variant_id}`. Conflicting variants remain separate. The original
  encoded records remain in the unmodified source bundle; the view retains all
  normalized fields and source citations.
- `provider`: name, actual model, mock label and available operational metadata.
- `inference`: requested and actual model, output mode, finish reason and completion
  token count. Missing metadata is null with an explicit `unavailable` status;
  invalid termination metadata is redacted. Original available usage remains in
  provider metadata. OpenAI's completion count uses `usage.output_tokens`.
- `validation` and `validation_stages`: orchestration adds passed stages only
  after JSON parsing, model schema, citation checks/gap preservation, factual
  consistency and final assembly all succeed.

Assembly alone does not label its output validated. The CLI writes a successful
assessment only after all five stages pass. Collection metadata is deterministic,
but is still a report of supplied evidence, not proof of telemetry authenticity.

## Inference metadata and failures

Meterless already supplied `finish_reason`, usage, requested model and output mode
for accepted responses; those were not being discarded. Its adapter previously
discarded available termination details when rejecting a non-`stop` completion.
Those failures now retain bounded termination diagnostics and a response hash
without entering JSON/schema/factual validation or being accepted. Missing finish
reason remains a provider failure, not an assumed successful stop. OpenAI's verified
Responses interface has `status`, not chat-completions' finish reason: completed
status is retained while an unavailable finish reason stays null. Both providers
record the configured output cap; no automatic retry, fallback or downgrade exists.

`ARGUS_MAX_OUTPUT_TOKENS` remains configurable (default 6000, range 256–32000).
An incomplete JSON document alone does not establish token-budget exhaustion.
The earlier 4,391-token failure had no retained finish reason; its upstream cause
remains unverified. No malformed JSON is repaired.

Existing bounded, credential-safe failure reports and response hashes remain.
`--retain-failed-response` explicitly opts in to a separate, local
`<alert>.<provider>.failure.<hash>.response.txt` beside the diagnostic report.
This **untrusted model text** is never printed, interpreted as instructions or
treated as validated. Default behaviour retains no raw model output. Configured
credential guards and protected-directory/link guards apply before writing.
Malformed Unicode is escaped for safe UTF-8 debugging retention; the diagnostic
hash still identifies the original response, not an attempted repair. Runtime
caches may independently retain responses. Protect these ignored local artifacts.

## Historical compatibility and running

`ASSESSMENT_SCHEMA` and `LEGACY_ASSESSMENT_SCHEMA` retain their 2.0/1.0 contracts.
`validate_assessment(v2, view)` and explicitly
`validate_assessment(v1, view, allow_legacy=True)` retain their old acceptance rules,
including mandatory fact/gap copying for v2. They do not accept or upgrade 3.0
outputs. Use `validate_model_content` for interpretation_v1 and the assembly
function for new outputs. No historical artifact is rewritten in place. Existing
artifact names and hash algorithms are unchanged; choose a fresh directory to
preserve older outputs, because the CLI can replace its fixed successful filenames.

```powershell
$argusPython = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe"
& $argusPython -m unittest discover -s tests -v
& $argusPython scripts/reason_evidence.py artifacts/evidence/ALT0018.json --provider mock --output-dir artifacts/assessments/phase3b3-mock
# With existing Meterless configuration loaded, GET-only discovery (no inference):
& $argusPython scripts/check_meterless.py
# One explicitly requested real inference; no retries:
& $argusPython scripts/reason_evidence.py artifacts/evidence/ALT0018.json --provider meterless --output-dir artifacts/assessments/phase3b3 --retain-failed-response
```

No Zetaris instance is needed to reason over a saved bundle. No operational CSV,
hidden ground truth, autonomous querying, tool execution or remediation is involved.

## Verification on 6 November 2026

The full suite passed **254 tests**: all 223 existing cases plus 31 new contract,
completeness, assembly, provider and failure-retention regressions. Historical tests
were adjusted only where mocked inference payloads needed the new contract; the
historical schema and original completeness-failure regression remain tested.

A labelled mock final assessment and unchanged-schema reasoning view were generated
under `artifacts/assessments/phase3b3-mock/` using ALT0018. All five stages passed.
The supplied bundle reports successful collection with only a bounded-scope gap:
the new within-scope completeness is COMPLETE, while investigation sufficiency is
NOT_DETERMINED. This illustrates the versioned semantics; it is no incident finding.

Authenticated GET-only model discovery confirmed
`nvidia/nemotron-3.5-bolt-e4b`. Exactly **one** subsequent real ALT0018 inference was
made through the existing local Meterless Runtime, with a 12,000-token output cap.
The response reported `finish_reason: stop` and **4,211 completion tokens**.
Requested and actual model identifiers agreed; output mode was `prompt_json`.

- JSON parsing: **passed** (complete, syntactically valid JSON).
- Model-output schema: **failed**.
- Citation/gap validation: **not run**.
- Factual consistency: **not run**.
- Final assembly: **not run**; no validated live assessment was written.

The bounded diagnostic listed nine structural errors: missing `key_findings`, plus
two finding-shaped objects at `factual_claims[11]` and `[12]`. Each lacks `fact_id`
and `value`, uses a finding enum (`OBSERVED_FACT`/`INTERPRETATION`) instead of a fact
kind, and adds a `statement` property. These objects were not moved, stripped or
repaired. The response omitted the removed collection-metadata fields as intended.

The rejected output *proposed* LIKELY_TRUE_POSITIVE / HIGH; this is not an accepted
classification. Manual inspection found that it correctly described
198.51.100.51 as having no exact network match, 203.0.113.10 as matching VPN N005,
and the two referenced authentications as sharing reported ID DEV-U001-01. It
mentioned VPN/proxy use as a possible explanation but did not meaningfully weigh
the known VPN endpoint as counterevidence (its counterevidence list was empty).
It overstated the security implication of reported geography and even suggested
legitimate London–New York travel within six minutes as an alternative. Those
interpretive problems remain, despite the improved exact-network/device statements.
Its summary also said "same device" rather than clearly limiting the observation
to a shared reported identifier; physical-device identity is not established.
No factual correctness or citation-validation result is claimed for this output.

Failure artifacts are retained separately from all historical outputs:

```text
artifacts/assessments/phase3b3/ALT0018.meterless.failure.10b31f0310f075f6ca308cc6b4588e34a82c1ddff431808de10c0d9a48c5b192.json
artifacts/assessments/phase3b3/ALT0018.meterless.failure.10b31f0310f075f6ca308cc6b4588e34a82c1ddff431808de10c0d9a48c5b192.response.txt
```

The response hash is recorded in the diagnostic. There was no retry, repair,
additional inference, ground-truth access or change to validation after this
failure. The next reliability work should address the model's confusion between
finding objects and factual-claim objects, without accepting or rewriting this
response. A future live evaluation must be a separate, deliberate request.
