# Phase 3B.10: model contract presentation

## Audit and scope

The Phase 3B.9 JSON-mode system message contained the shared security-analysis
instructions, provider JSON-format safeguards, and the complete compact
`MODEL_OUTPUT_SCHEMA`. The schema was delivered in prompt text because
`response_format: {"type":"json_object"}` has no schema carrier. The user message
contained only the unchanged `untrusted_evidence` view.

All eleven required properties were present in that schema, in authoritative
schema order. `assessed_hypothesis` and `factual_claims` were the last two root
properties. The shared prose also named them: it asked for a specific security
hypothesis and explained selection/copying of supported fact claims. It already
distinguished interpretation from ARGUS-owned collection metadata and final
assembly. No confirmed conflicting wrapper instruction or alternate contract was
identified. Their omission does **not** establish that the prompt was defective.

The presentation nevertheless lacked one standalone checklist making every root
field explicitly mandatory. Its compact schema nested citation/finding structures
among syntax/type keywords, and the metadata-exclusion and fact-selection prose
was spread across several paragraphs. Repeated formatting safeguards agreed with
the schema; they did not authorize a partial object.

The prior response used 3,393 completion tokens against a 12,000-token cap and
reported `stop`. The retained diagnostics do not indicate output-token truncation;
the failed raw completion was not retained, so the reason for omission is unknown.
This phase keeps the cap at 12,000 and changes presentation, not the acceptance
contract or investigative meaning.

## Refinement

`scripts/reasoning_prompts.py:model_contract_instructions(schema)` now generates a
deterministic human-readable checklist directly from the supplied authoritative
schema's `required`, property types, enums and nested required keys. It enumerates:

1. `classification`
2. `confidence`
3. `executive_summary`
4. `key_findings`
5. `supporting_evidence`
6. `counterevidence`
7. `alternative_explanations`
8. `unknowns`
9. `recommended_next_steps`
10. `assessed_hypothesis`
11. `factual_claims`

This list in the report describes today's schema; the implementation derives it
at request construction time rather than maintaining another field inventory.
It renders array/object shapes and enum values without inventing findings,
classification choices, fact IDs or source citations. A checklist was chosen over
a JSON skeleton because placeholder strings/empty nested examples could look like
valid evidence, be copied verbatim, or contradict nonempty-text/citation checks.

The accompanying prose explicitly states:

- Return exactly one JSON object; include each required top-level field once,
  without omissions, aliases or a wrapper.
- `assessed_hypothesis` is a nonempty string identifying the security hypothesis
  tested by supporting and counterevidence, not a declaration of compromise.
- `factual_claims` is an array of selected supported `relationships.facts` entries,
  with exact fact ID, kind, value/type and complete citations. Finding objects do
  not belong there. The field must exist even when the contract permits `[]`.
- Only supplied source table/ID/variant citations are permitted. Network matches
  require supplied relationships, not address similarity. Unsupported certainty
  and invented evidence are prohibited; insufficient evidence needs uncertainty.
- The object is model interpretation. ARGUS owns alert/version/collection metadata,
  completeness, limitations, provenance and final assessment assembly.

The existing full schema still follows the checklist and remains authoritative.
No required fields, enum values, empty-array policy, claim semantics or validation
rule was added/removed. This is model guidance, not code that fills model output.
The existing limitation remains: validators cannot mechanically establish that
every prose assertion has an appropriate structured claim or that an interpretation
follows from its citations. Human review remains necessary.

The helper is used **only in Meterless JSON-object mode**. The shared
`SYSTEM_PROMPT`, `build_prompt`, mock, OpenAI, strict Meterless messages, schemas,
relationship derivation, compatibility normalization, validators and final assembly
remain unchanged. Preflight compared the strict request with the historical
reconstruction and found exact request-object equality. Strict mode is still the
default, with no automatic fallback or repair.

## Offline verification

**318 tests passed**: all 306 existing tests plus 12 focused presentation tests,
with all external inference mocked. New tests cover the exact schema-derived field
inventory/order, nested structures/enums, explicit hypothesis/claims meanings,
responsibility boundaries, deterministic nonmutating generation, absence of invented
evidence examples, correct JSON-mode integration, unchanged other-provider paths,
and continued rejection of missing fields, unsupported citations and contradictory
claims without defaults or retries.

ALT0018 preflight confirmed:

- Only the JSON-mode system message changed; user evidence, schema, output mode,
  temperature, model and token budget are identical to Phase 3B.9.
- The schema-derived guidance is **2,329 characters**. JSON-mode system text grew
  from **8,925** to **11,255** characters; the user message remains **81,109**.
- Every required field is listed once in the checklist; the unchanged full schema
  follows it. No output skeleton or example evidence value is supplied.
- Canonical schema hash:
  `866755d8ce06c991af8c02219fd2f0c3e86fa467811f46200cb8465e855b4683`.
- Bundle hash:
  `ea27ef9f637468bc5506b13d005e6b8d0e05f1b2efbc28f8d0b7d2136e001089`.
- View hash:
  `224830e2289e386b817f83ead950f62d7ad8987608f19d72a0512919b6a3cddd`.

Preflight verified authenticated model discovery without inference. No hidden
evaluation information or local operational CSV was loaded for model context.
Audit artifacts save contract-only instructions and bounded hashes/metrics, not
the model-facing evidence message.

## Reproduction and artifacts

```powershell
$argusPython = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe"
& $argusPython -m unittest discover -s tests -v
# Deliberate live inference, not part of the automated suite:
& $argusPython scripts/reason_evidence.py artifacts/evidence/ALT0018.json `
  --provider meterless --meterless-output-mode json_object --meterless-temperature 0 `
  --output-dir artifacts/assessments/contract-review
```

Use existing local credentials/configuration; never include secrets in arguments
or reports. Choose a fresh output directory. Do not replay spent verification
helpers merely to inspect their artifacts.

Changed implementation: `scripts/reasoning_prompts.py`, the JSON-mode presentation
branch in `scripts/reasoning_providers.py`, and
`tests/test_contract_presentation.py`. No dependency was introduced. Canonical data,
ground truth, evidence bundles and historical assessment artifacts are preserved;
no commit or push was made.

New ignored local artifacts are under
`artifacts/assessments/phase3b10-contract-presentation/`: `contract-instructions.txt`,
`preflight.json`, `run_once.py`, a spent-before-dispatch marker, and the CLI's
validated artifacts or bounded failure report. Raw failed completions are not
retained or printed. The helper permits at most one live invocation, with no retry,
fallback model or autonomous retrieval.

## Single live result: 7 November 2026

**The fully validated live-assessment objective was not achieved.** Exactly one
ALT0018 inference was performed after the offline suite passed, using the existing
bundle/view, unchanged interpretation schema, Meterless Nightshift, configured
`nvidia/nemotron-3.5-bolt-e4b`, explicit `json_object`, temperature **0**, output cap
**12,000** and timeout **180 seconds**. There was no retry or fallback.

HTTP/provider outcome: **200**, completion returned; requested and returned API
model both `nvidia/nemotron-3.5-bolt-e4b`, `finish_reason: stop`, **3,620 completion
tokens**. Effective backend temperature is not echoed. Prompt/total usage is not
retained in the existing bounded failure-metadata projection. The model identity
is an API envelope echo, not an independently inspected weight revision.

Runtime metadata reported JSON-object syntax validation **passed**, `strict: false`,
capability `unverified`, fingerprint `56331b130db3e075`. That is not an ARGUS
interpretation-schema or factual-consistency pass.

| ARGUS stage | Result |
|---|---|
| 1. JSON parsing | Passed |
| 2. Interpretation schema validation | Failed: root `FIELDS` check |
| 3. Citation and evidence-gap validation | Not run |
| 4. Structured factual consistency | Not run |
| 5. Deterministic final assembly | Not run |
| 6. Final assessment/schema validation | Not run |

The existing artifact groups stages 5 and 6 under `final_assembly`. Both operations
remain mandatory before saving an accepted result.

Bounded schema diagnostics reported the same two omissions as Phase 3B.9:

- `assessment.assessed_hypothesis`: missing, expected string.
- `assessment.factual_claims`: missing, expected array.

No required field was defaulted, no empty claims list inserted, and no output
repaired or altered. A validated assessment was **not** saved. The failed completion
was not retained or printed. Only the existing bounded failure report, request/input
hashes, contract-only presentation, and once-only invocation metadata survive.

The new report is under `live/` in the phase's local artifact directory:

```text
ALT0018.meterless.failure.878e25a946ffa7485593913d5fbf7362ed0fe34bc5a326424cec826d092db346.json
```

Its response hash differs from Phase 3B.9, as does the reported completion count
(3,620 versus 3,393), but the two recorded missing fields are unchanged. These
observations do not establish identical complete response structures or prove
which instructions were followed. The presentation change did not resolve the
omission in this one observation. Neither run reported output-limit termination.

There is no accepted classification/confidence or model reasoning to review for
the unmatched London source, N005 VPN endpoint, shared reported device, six-minute
interval, physical-geography caveat or legitimacy uncertainty. **Investigative
quality remains unverified.** Passing syntax or receiving some required fields
cannot substitute for citation, factual and final-artifact checks.

The remaining blocker is persistent omission of two mandatory fields despite their
presence in both the explicit checklist/explanations and full schema. Its cause is
not established. Recommended next work is to verify effective system-message and
schema delivery at the Nightshift-to-NVIDIA boundary using supported, bounded
hash/property-name diagnostics; then consider a separately authorized, isolated
synthetic test of these two structures. Do not fill the omissions or relax the
contract. No additional inference, runtime change or such experiment was performed.
