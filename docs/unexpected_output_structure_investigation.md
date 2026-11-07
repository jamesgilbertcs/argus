# Phase 3B.8: unexpected output structure investigation

Investigated 7 November 2026. **No offline source of
`assessment_classification` was identified.** Exactly two authorized minimal-schema
inference calls were then made; both failed with alternative root properties,
but neither reported that exact alias. Full-contract complexity is therefore not
necessary for a failure on the current route. The origin of the old alias remains
unresolved, and no production/schema/validation remedy is established.

No production code, prompts, assessment contracts, validation acceptance rules,
credentials, installed runtime files, datasets, ground truth, evidence bundles or
historical artifacts were modified. No ALT0018 inference, automatic retry,
fallback, repair, commit or push was performed in this phase.

## 1. Provenance search

Before either new call, the offline audit searched 125 repository text files
(including ignored assessment artifacts), 195 installed Nightshift text files,
and 117 local runtime state/job/log text files. There were no unreadable,
oversized or file-limit omissions in those searched sets. Credential files,
`.env*`, Git internals, virtual environments, dependencies and evaluation data
were excluded. This is not an exhaustive binary, encrypted-state, process-memory
or upstream-server inspection. No secret values, raw private logs or model text
were printed. The new investigation directory was excluded to avoid counting
this audit's own occurrence of the alias as an input source.

**Exact matches:** five repository files: the A0 and B0 Phase 3B.7 failure artifacts,
their two result summaries, and `docs/temperature_isolation_experiment.md`. The four
JSON files represent **two inference events**, not four. The report was written
after observing the alias. Git pickaxe history found it introduced only in that
report (`72b010f`), not in a prior tracked prompt/template/adapter.

**No exact matches:** searched production source, tests, saved request messages,
saved request schemas, installed runtime source/docs/configuration or retained
runtime state/log text. A bounded structured-error recovery walk of current runtime
state (9,446 nodes) recovered no retained validation container. It did not recover
the old completion or the old redacted unknown property name.

Ten saved request artifacts were inspected independently: six Phase 3B.5 probes,
the Phase 3B.6 production reconstruction and three Phase 3B.7 requests. None
contains the alias in messages or response format/schema. The reconstruction is
not a retained copy of the original HTTP body; historical upstream bytes remain
unknown.

Relevant occurrences of related words were inspected and categorized:

| Location | Actual occurrence | Model-facing use | Conclusion |
|---|---|---|---|
| `scripts/reasoning_prompts.py:11–85` | “assessment JSON”, provisional “classification”, `assessed_hypothesis` | System instructions | No combined alias or instruction to wrap output |
| `scripts/reasoning_prompts.py:88–90` | `untrusted_evidence` | Input wrapper in user message | Not an output contract |
| `scripts/reasoning_assessment.py:80–109` | `classification` in legacy 1.0, 2.0 and interpretation contract for final 3.0 | Current `MODEL_OUTPUT_SCHEMA` is sent; archival schemas remain available for old callers | All require exactly `classification`; none defines the alias |
| `scripts/reasoning_providers.py:78–106` | Mock output `classification`; local variable `assessment` | Mock response only; not an example inserted into real-model prompts | No alias introduction |
| `scripts/reasoning_providers.py:284,404–419` | Schema name `argus_assessment`; “Keep the assessment concise” | Format name and common system suffix | Exact name/word occur, but concatenation into the alias is only a semantic hypothesis |
| `scripts/reasoning_assessment.py:125` | Default diagnostic path `assessment` | Internal validation path | Never supplied as an output wrapper instruction |
| `scripts/reasoning_assembly.py:66–94` | Final artifact key `assessment` | Constructed after model validation | This wrapper cannot explain a pre-acceptance model field through the inspected call path |
| `tests/test_reasoning.py:65–76`, contract/structured-provider tests | Mock/fake responses use `classification`; final artifact assertions use `assessment` | Unit-test fixtures, not real-model messages | No exact alias or alternate naming template |
| `tests/test_runtime_rejection_diagnostics.py:106–155` | Generic unexpected `assessment` property | Mock HTTP diagnostic regression only | Tests the sanitizer; not an observed rejected completion or model prompt |
| `docs/assessment_contract.md`, reasoning/strict-output docs and older artifacts | Document output shapes and internal wrappers | Documentation/archive only | Not read by `build_prompt` or the Meterless call |
| Generator/evaluation-related test code | Evaluation-only expected assessments | Outside inference path | Not loaded or sent by these probes; no evaluation answers used |

Searches for related spellings/phrases (`assessment classification`, camel-case
variants, `classification_result`, `incident_assessment`, `security_assessment`,
`assessment_type`) did not identify an alternate model-facing contract in ARGUS
or an installed security-assessment template. Generic uses of “assessment” and
“classification” are **not** exact-string provenance for the observed alias.

## 2. Installed Nightshift path

Read-only inspection used files under `%LOCALAPPDATA%/Nightshift/engine/`.

- `openai.mjs:397–493`: named model pinning, format preflight, caller messages and
  `response_format` passed to `runChat`; structured requests disable response cache.
- `js/engine/orchestrator.js:1691–1738`: a resolvable pin collapses the request to
  one final-synthesis phase. `messagesForPhase()` at 1376–1425 returns base messages
  when there is no prior plan/scaffold. The pinned route disables the optional
  reasoning scaffold; no security-assessment-specific output template was found.
- `orchestrator.js:2082–2127`: existing runtime answer memory **can** prepend a
  system instruction beginning “Consult these PRIOR VETTED ANSWERS”. This is a
  real injection path, not proof of injection for the failed calls. Its stored
  content can be dynamic. No matching literal or usable historical injection
  snapshot was found in searched persisted state. This phase implements no memory.
- `orchestrator.js:2171–2181,2723`: the response format is added to final-phase
  generation options. No root-property renaming/wrapping is performed there.
- `js/engine/providers.js:1455–1497,1087–1101`: NVIDIA chat handling forwards the
  schema/properties and messages unchanged. There is no inspected NVIDIA-specific
  assessment template. Earlier actual-installed-module offline checks verified
  exact schema forwarding; that is not a capture of historical live upstream bytes.
- `providers.js:1119–1135`: non-streaming extraction reads
  `choices[0].message.content` as text. The outer chat response envelope is not
  interpreted as the assessment. `readBodyTolerant()` can assemble SSE/NDJSON
  transport envelopes; no assessment field renaming is implemented there.
- `providers.js:1832–1858`: cloud messages are redacted and placeholders restored
  in returned text. Phase 3B.6's installed-redactor replay made zero substitutions
  in the reconstructed real request. Restoration precedes the first schema gate.
- `orchestrator.js:980–1002`: first gate validates the returned assistant **text**,
  after provider extraction/any placeholder restoration, before scaffold stripping
  or later quality transformations. It parses that text as JSON; it does not map
  it into a new security-assessment object. The failure branch drops the completion
  but attaches bounded paths, messages and finish reason.
- `orchestrator.js:3883–3895`: a second gate checks the final text after possible
  later transformations. Retained error metadata does not identify which gate
  rejected a particular historical call.
- `js/engine/structured-output.js:224–351` enumerates the parsed value's properties
  and compares them to the supplied schema; it neither invents the unknown property
  name nor renames keys. `proxy.js:608–641` exposes its bounded error list.

Thus the diagnostics concern model-content text, **not ARGUS's deterministic final
artifact**. They are not necessarily a byte-for-byte view of the original upstream
transport response. Dynamic runtime state, the precise historical upstream payload
and NVIDIA's internal decoding behavior remain unavailable. No installed file or
validation gate was modified or bypassed.

## 3. Historical comparison

All rows below requested `nvidia/nemotron-3.5-bolt-e4b` with strict `json_schema`.
“M7” means these seven `required` paths: `$.classification`, `$.key_findings`,
`$.supporting_evidence`, `$.counterevidence`, `$.alternative_explanations`,
`$.unknowns`, `$.recommended_next_steps`.

| Request | Schema | Temperature | HTTP | Missing required properties | Unexpected properties | Finish | Runtime/model/usage metadata |
|---|---|---:|---:|---|---|---|---|
| 3B.5 probes 1–4, short synthetic | Incremental small/subset schemas | 0 | 200 | None reported; independent pass | None; independent pass | `stop` | Runtime pass, capability `unverified`; model echoed; usage retained |
| 3B.5 probe 5, copy synthetic fixture | Unchanged full contract | 0 | 200 | None; independent pass | None; independent pass | `stop` | Runtime pass; model echoed; 362 prompt / 323 completion tokens |
| 3B.5 probe 6, short synthetic interpretation | Unchanged full contract | 0 | 200 | None; independent pass | None; independent pass | `stop` | Runtime pass; model echoed; 199 / 355 tokens |
| Controlled ALT0018 before 3B.6 | Unchanged full contract | Omitted; installed default path 0.7 | 502 | M7 | One name redacted | `stop` | Error `schema_validation_failed`; actual model and usage unavailable |
| 3B.6 reconstruction/audit | Unchanged full contract | Omitted | N/A | N/A | N/A | N/A | Offline only; no inference |
| 3B.7 A0, generic synthetic evidence | Unchanged full contract | 0 | 502 | M7 | `assessment_classification` | `stop` | Eight retained errors; actual model and usage unavailable |
| 3B.7 A07, same synthetic evidence | Unchanged full contract | 0.7 | 502 | M7 plus `$.executive_summary` | Not reached/reported in bounded list | `stop` | Eight retained errors; actual model and usage unavailable |
| 3B.7 B0, ALT0018 | Unchanged full contract | 0 | 502 | M7 | `assessment_classification` | `stop` | Eight retained errors; actual model and usage unavailable |

The recurring observed structure is missing prescribed root keys, sometimes with
an alternative root key. The validator has an eight-error ceiling, so an eight-row
missing list cannot establish absence of extras. The older redacted property
cannot be named retrospectively. **Matching errors do not prove identical complete
objects, values, nested layouts or generating causes.**

## 4. Two controlled calls

The offline audit finished before preparation/dispatch. Authenticated discovery
confirmed the configured model. Exactly one POST per condition was made using the
existing bounded Meterless transport, with no retry or fallback.

Both used the specified two-field schema exactly: required `classification` string
with the three current classification enum values, required `executive_summary`
string, root `additionalProperties: false`. This isolated probe schema does not
change any production contract. Common settings: explicit **temperature 0**,
**1,024 output tokens**, 180-second timeout, non-streaming, schema name
`argus_assessment`, app `argus`, scope `user`, no thinking/reasoning override.

Common system message: “Use only the supplied synthetic event. Return one JSON
object matching the response_format JSON Schema. Do not use tools or perform
actions.” The existing provider adds its same strict-format suffix to both,
including “Keep the assessment concise”; therefore A is neutral **user framing**,
not a guarantee that the word “assessment” occurs nowhere in the whole request.
Neither message asks to violate the schema; A does not contain the phrase
“assessment classification”.

Common synthetic event: TEST_ACCOUNT has a failed password attempt, followed two
minutes later by a successful multifactor login from the same reported device and
known corporate VPN endpoint; further activity and user confirmation are absent.
No dataset, real evidence bundle or evaluation label was loaded by the probe.

Only the first sentence of the user message changes:

- **A:** “Give a structured classification of this synthetic security event.”
- **B:** “Produce a security incident assessment of this synthetic security event.”

Offline request equality checks confirmed that all other payload fields, system
messages and event text match. The model's effective backend temperature is not
echoed; explicit-zero forwarding is verified by installed source/offline checks.

| Call | HTTP/provider outcome | Missing `required` paths | Unexpected root names (`additionalProperties: false`) | Finish | Usage | Independent validation |
|---|---|---|---|---|---|---|
| A, neutral | 502 `schema_validation_failed` | `$.classification`, `$.executive_summary` | `event_type`, `severity`, `description`, `affected_entity` | `stop` | Unavailable | Not run: no completion |
| B, incident assessment | 502 `schema_validation_failed` | `$.classification`, `$.executive_summary` | `assessment`, `details`, `severity` | `stop` | Unavailable | Not run: no completion |

A returned six schema-error entries, B five; none was additionally truncated by
ARGUS. Unknown names passed the existing bounded safe-name policy. Their values
and types are unavailable and are not guessed. No `assessment_classification` name
was reported in these two calls. In particular, B's `assessment` name does **not**
establish the contents or type of that property's value.

Both errors report that Nightshift parsed JSON but rejected the requested schema.
ARGUS received no completion, so its own JSON parsing and independent schema check
could not run. No validated object or assessment was saved. The answering model
identifier was withheld: the requested identifier must not be presented as a
verified response echo. No unrestricted response text, raw error body, private
reasoning, credential or authentication header was retained or printed.

## 5. Evidence-ranked hypotheses and next action

**Confirmed:** the exact alias is present only in retained downstream diagnostics
and the later report in the searched material; neither current ARGUS instructions
nor any inspected historical schema asks for it. The rejection gate reports other
non-prescribed property names even for this tiny contract at temperature zero.
Full-schema complexity and large real evidence are **not necessary** for failure.

Ranked interpretations, with uncertainty:

1. **Content/request-dependent schema nonadherence somewhere on the current
   inference route** is the strongest general explanation supported by the gate's
   diagnostics. A fresh two-field contract also fails, unlike earlier small/full
   probes. This does not locate whether upstream constrained generation is absent,
   ignored, inconsistently applied or affected by runtime request state.
2. **Nemotron produces semantically familiar alternative security-field names**
   is plausible: neutral framing yielded event-description names, assessment
   framing yielded `assessment`/`details`. One sample per framing is insufficient
   for causation, and the withheld upstream completion prevents proving this
   originated in the model rather than elsewhere on the route. The exact old
   `assessment_classification` could be another such name, but that is inference.
3. **Runtime injection/transform effects** remain possible because actual injection
   paths exist. No exact alias, hardcoded alternate contract or key-rewriting
   implementation was found. No retained trace demonstrates runtime injection for
   these calls. Source-level forwarding is not historical upstream-wire capture.
4. **Full-contract complexity alone** is weakened by these minimal failures and
   the two prior full-contract successes. Complexity could still affect reliability;
   this experiment neither proves nor rules out such an effect.
5. **A repository prompt/template explicitly introduces the alias** has no support
   in the search. Generic words, schema name `argus_assessment`, and internal final
   wrappers are semantic connections, not identified origins.

**No field rename, output repair, unwrapping, schema simplification or validator
relaxation is justified.** Keep the existing rejecting boundary.

Recommended next diagnostic, **not executed**: a separately approved two-call
comparison could reuse this phase's exact neutral A request and change **only**
the response schema name between `argus_assessment` and `argus_schema_probe`.
The successful older probes and recent failures differ in this name as well as
other variables; this pair would isolate that one difference without instructing
the model to violate the schema. Preserve model, event, messages, temperature,
budget, app identity, scope and all other options. If both fail, prioritize
temporal/runtime/upstream-route diagnostics; if only one fails, investigate the
name/associated runtime state and replicate before claiming causality. This is a
narrow diagnostic, not a proposed schema-name fix or reliability test. No such
call was made in this phase, and no outcome would authorize accepting invalid output.

Where supported, ask the runtime/provider for bounded **upstream schema
fingerprint and property-name-only** diagnostics before/after transformations;
never request unrestricted private completions or bypass the rejecting gate.
The exact historical completion and origin may remain unrecoverable.

## 6. Files and verification

The only tracked addition is this report. New Git-ignored local artifacts under
`artifacts/assessments/phase3b8-origin/` contain:

- `offline.py`, `offline-audit.json`: read-only pre-call provenance/state audit.
- `probe.py`: two-case, once-only experiment driver; prepared-payload checks,
  spent-before-dispatch markers and a hard two-call cap prevent replay.
- `A.request.json`, `B.request.json`: payloads only, without authentication headers.
- `A.spent.json`, `B.spent.json`, `A.result.json`, `B.result.json`: counts, timestamps
  and safe metadata/error details. No raw completions are present.
- `unittest.log`: complete automated-suite output.

**287 automated tests passed**, all external inference mocked. No production
diagnostic behavior was changed, so no new regression tests or dependencies were
needed. `git diff --check` passed. Normal test execution remains:

```powershell
$argusPython = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe"
& $argusPython -m unittest discover -s tests -v
```

The retained probe cases are spent; do not replay them. No further inference was
performed after call B.
