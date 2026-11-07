# Phase 3B.6: prompt and request isolation

Audited 7 November 2026. **No live inference or other network requests were made.**
No prompt, provider request behavior, assessment schema, validator acceptance rule,
dataset, ground truth, evidence bundle or historical assessment was changed.

**Finding:** no actual prompt/schema contradiction or wrapper instruction was found.
The unchanged schema matches the successful full-contract probes. Production and
probes differ substantially in workload and sampling; causation is not established.
The exact rejected output and its unexpected property name remain unavailable.
The confirmed diagnostic blind spot is addressed without changing acceptance.

## Reconstructed ARGUS request

The offline audit calls the real `MeterlessProvider.generate()` with the real
`build_prompt(build_reasoning_view(bundle))` and `MODEL_OUTPUT_SCHEMA`, intercepting
the HTTP opener **before dispatch**. It uses a placeholder credential and never
loads `.env.local` or authentication material. No response is fabricated for the
failed request. The separate adapter replay uses a dummy response only to inspect
outbound parameter construction, never as a successful ARGUS assessment.

- Endpoint: `POST http://127.0.0.1:8088/v1/chat/completions`.
- Model: `nvidia/nemotron-3.5-bolt-e4b`; non-streaming.
- Two messages: system instructions plus a user JSON object containing only
  `untrusted_evidence`, whose value is the complete reasoning view.
- System text: `SYSTEM_PROMPT`, followed by the provider's strict-format paragraph.
- `response_format`: `{"type":"json_schema","json_schema":{"name":"argus_assessment","strict":true,"schema":MODEL_OUTPUT_SCHEMA}}`.
- Five payload fields: `model`, `messages`, `max_tokens`, `stream`, `response_format`.
- Output cap **12,000**, matching the saved failure metadata. Reconstructed timeout
  **180 seconds** is the current default; the failed artifact does not retain it.
- No client-supplied temperature, top-p, seed, thinking switch, reasoning budget,
  tools, tool choice, stop sequences or other provider options. Runtime app identity
  is `argus`, scope `user`; no authentication headers are saved in audit artifacts.

The view is **81,086 bytes**; its user-message envelope is **81,109 bytes**. It has
**38 source records** (7 authentications, 20 accesses, 6 resources, 3 networks,
1 user, 1 alert), **40 derived facts**, 10 historical required-fact flags, and one
bounded-scope gap. System text is **5,786 bytes**. Compact schema is **3,092 bytes**;
the serialized HTTP body is **98,907 bytes** including JSON escaping/whitespace.

Messages contain 86,895 characters: approximately **21,724 tokens at four characters
per token**, plus approximately 773 if schema text is included as model context.
This is a rough character heuristic, **not a Nemotron tokenizer count or measured
usage**. Repeated hashes and JSON can materially change tokenization; actual failed
request usage is unavailable. Wire-body size is not a model input-token count.

Schema: 11 required root fields, all objects reject unexpected properties; nested
findings, citations, alternatives, unknowns and factual claims use the existing
contract. There are no deterministic ARGUS-owned metadata fields in the schema.
Its compact, insertion-order JSON SHA-256 is
`2a14d8db27516d65caa37de6f9475a6254254fba9bf81bab943694ad4082e79f`.
Both saved full-contract probe schemas compare equal and have that hash.

The failed call's saved canonical bundle/view hashes match the reconstruction:

- Bundle: `ea27ef9f637468bc5506b13d005e6b8d0e05f1b2efbc28f8d0b7d2136e001089`.
- View: `224830e2289e386b817f83ead950f62d7ad8987608f19d72a0512919b6a3cddd`.

The reconstructed HTTP-body hash is
`3310db4da2c274faf28675b964aea1a35df31bb35f22d41d3ecd67678e42382d`.
**The original HTTP body/hash was not retained**, so byte equality with that
historical request cannot be proven. The failure verifies model/cap/mode and input
hashes, not the exact rendered system message or post-runtime upstream payload.
The older `ALT0018.meterless.view.json` differs and is not used as the reconstruction
authority. No old artifacts were overwritten.

## Prompt/schema audit

No conflicting excerpts were identified. Relevant inspected instructions:

- `scripts/reasoning_prompts.py:44–47`: “Return investigative interpretation only”
  and “Do not output those metadata fields.” This agrees with the model schema's
  exclusion of schema version, alert ID, collection status and completeness.
- `scripts/reasoning_prompts.py:52–53`: required flags “describe historical version
  2.0 copying policy, not this interpretation contract.” This is an explicit
  disambiguation, not an instruction to emit a version 2.0 assessment.
- `scripts/reasoning_prompts.py:74`: “Do not regenerate collection_status or
  evidence_complete.” Consistent with the same exclusion.
- `scripts/reasoning_prompts.py:88–90`: `untrusted_evidence` is the **input** wrapper;
  no instruction asks the model to copy it or wrap output in `assessment`.
- `scripts/reasoning_providers.py:404–419`: the appended format paragraph says
  “Return one JSON object only” and “Match the response_format JSON Schema exactly.”
  It contains no example object or alternative field inventory.
- `scripts/reasoning_assessment.py:108–109`: authoritative interpretation schema
  excludes ARGUS-owned metadata. Legacy schemas remain for archival validation,
  but are not passed to this production provider call.

The input view still has `schema_version: "2.0"` and historical `required` flags,
as documented in `docs/assessment_contract.md`. They are **input schema metadata**,
not an obsolete output contract. They could contribute to model confusion but
there is no evidence tying them to the withheld property. No schema 1.0 output
example, obsolete output field list, wrapper example or request to omit required
interpretation fields was found in the rendered instructions.

Output-format directions are repeated in the system prompt and provider suffix;
they agree. Collection-metadata exclusions are repeated for emphasis. The JSON
schema itself is sent once in `response_format`, not duplicated as prompt prose.
Instructions to be concise or select supported facts do not authorize omitting
required root fields. No evidence text is interpolated into the system message.

All view strings were scanned offline for a small explicit list of instruction
markers (`ignore previous`, `ignore instructions`, `return only`, `return exactly`,
`system prompt`, `override`, `you are `, code fences and chat role delimiters).
There were **zero matches**. This is a limited audit aid, not proof that arbitrary
prompt injection is impossible. Manual inspection of the rendered prompt and
record-bearing input found ordinary synthetic telemetry and collector metadata;
evidence remains untrusted data.

## Nightshift transformations and limits of reconstruction

Read-only inspection used installed files under `%LOCALAPPDATA%/Nightshift/engine/`.
An isolated VM loaded the actual model registry, redactor and NVIDIA adapter with
stubbed fetch and placeholder authentication. It made **zero network requests**.

- `openai.mjs:425–493` preserves caller temperature and max tokens, forwards the
  format, and disables response caching for structured requests.
- `js/engine/orchestrator.js:1691–1738` pins a named model to one final-synthesis
  phase. `messagesForPhase()` at 1376–1425 returns the base messages when there is
  no prior plan or scaffold; the pinned route disables the reasoning scaffold.
- `js/engine/providers.js:1087–1101,1455–1497` forwards chat messages and the format
  unchanged. With the installed Bolt row and empty setpoints, the replay supplies
  **temperature 0.7** for production; the saved probe supplies **0**.
- `models.js:1981–1994` declares this row `reasoning: false`, no `thinkingControl`,
  no sampling defaults, context 65,536 and output maximum 16,384. Thus the default
  adapter adds **no thinking switch or reasoning budget** to production. The probe
  explicitly forwards `enable_thinking: false`; its internal effect is unverified.
- `providers.js:1832–1838` runs cloud outbound redaction. Applying the installed
  redactor to this reconstructed request produced **zero substitutions**, with
  messages byte-equivalent after JSON serialization. Schema forwarding was exact.
- `orchestrator.js:2082–2127` can prepend a “PRIOR VETTED ANSWERS” system message
  from existing runtime memory, including for a pinned route; no ARGUS request
  option disables this in the current call. This is an installed-runtime path,
  **not an ARGUS memory implementation or evidence of actual injection**.
- Other post-generation selection/transformation paths exist; schema checks run
  before a candidate is accepted and on final output. Retained failure metadata
  cannot identify which check rejected the historical completion.

The replay uses **empty setpoints** and does not replay the entire orchestrator.
A bounded read-only scan of current persisted runtime state found no retained
validation containers or explicit recognized settings/memory-count entries. This
does **not** establish that live/in-memory settings or memory were empty during
the failed request. No historical upstream payload, injection trace, raw completion
or validation-property name was recovered. Installed adapter behavior and default
values are confirmed offline; the exact historical upstream request is not.

## Successful and failed request comparison

“Confirmed” below means inspected ARGUS reconstruction, saved probe payloads/results,
or installed-source offline replay. It does not attribute causality.

| Variable | Successful full-contract probe 5 / probe 6 | Failed ALT0018 | Evidence/status |
|---|---|---|---|
| Model and schema | Same model; unchanged full interpretation schema | Same requested model and reconstructed schema | Schema equality confirmed; failed response model unavailable |
| Roles | System + user | System + user at ARGUS boundary | Confirmed; historical runtime additions unknown |
| System text | 84 characters | 5,786 characters | Confirmed construction; original full body not retained |
| User text | 1,377 / 738 characters | 81,109 characters | Saved probes vs hash-verified reconstructed view |
| Wording | Copy a fixture / interpret one toy success | Full analyst instructions, competing explanations and evidence constraints | Confirmed; no explicit output-shape conflict found |
| Evidence/citations | One synthetic citation; short fixture or one fact | 38 source variants, 40 facts, exact table/ID/hash citations | Confirmed; submitted claim count is model-selected, not all 40 mandatory |
| Temperature | Explicit 0 | Omitted by ARGUS; installed default replay sends 0.7 | Confirmed default mechanism; historical wire not captured |
| Thinking | Explicit `enable_thinking: false` | Omitted; installed row default adds no switch | Confirmed construction; backend effect unknown |
| Output cap | 4,096 | 12,000 | Confirmed in saved payloads/failure metadata |
| Format/name | Strict `json_schema`, `argus_schema_probe` | Strict `json_schema`, `argus_assessment` | Same schema, different name; confirmed |
| App/scope | `argus-full-schema-diagnostic` / user | `argus` / user | Different app identity; policy/memory effects unverified |
| Client timeout | 120 seconds | Reconstructed default 180 seconds | Probe saved runner; historical production timeout not retained |
| Runtime/cache | Same installation; schema disables response cache | Same reported runtime; structured request disables response cache | Source behavior confirmed; historical memory/policy snapshot unavailable |
| Result | HTTP 200, independent schema pass | HTTP 502, runtime schema failure | Confirmed; no completion for independent ARGUS validation |
| Measured usage | 362/323 and 199/355 prompt/completion tokens | Unavailable | Probe envelopes; failed usage must not be guessed |

Saved probe responses 5 and 6 were parsed and checked again offline against the
current unchanged full schema; both pass. They are compatibility tests, not proof
of citation/fact correctness for an ARGUS investigation.

## Unexpected-property diagnostics

The installed runtime's `structured-output.js:274–290` constructs an error path
containing the actual unknown key, and `proxy.js:608–641` exposes it in
`meterless.validation.errors`. ARGUS previously replaced the unknown leaf with
the nearest known schema path and retained no name. The old redacted `$` alone
cannot distinguish a wrapper, alternate spelling, analysis field or another key.
Do not infer that the missing-field pattern proves any of these shapes.

The diagnostic-only change in `scripts/reasoning_diagnostics.py` now retains
`unexpected_property` **only** when the runtime's exact extra-property diagnostic
names a single leaf under a verified object whose schema forbids extra properties.
The name must match `[A-Za-z_][A-Za-z0-9_]*`, be at most **48 characters**, and not
match excluded credential-style prefixes or a hex identifier of 20+ characters.
Punctuation, Unicode, compound paths, bad parents and oversized names stay redacted.
The existing safe parent `path` and `path_redacted` remain unchanged; retention is
explicit through `unexpected_property_status`. Names remain untrusted labels.

No values, raw error messages, model text, unknown parent paths or authentication
material are copied. Existing transport and artifact checks still reject reflected
configured credentials, including a short token that itself fits the name policy.
This policy is a bounded character filter, not a universal secret detector; do not
publish unreviewed diagnostics containing arbitrary runtime-supplied labels.
Error count remains bounded at eight. Runtime output is already capped at eight,
so a retained list of eight cannot prove there were no other violations, even if
ARGUS reports `errors_truncated: false` for the list it received.

No live call tested the new retention policy. Mocked HTTP tests verify that the
existing provider carries the admitted name to the failure artifact while keeping
all assessment stages `not_run`. No validation gate was bypassed or relaxed.

## Defects, hypotheses and proposed experiment

**Confirmed defect addressed:** safe structural information was irreversibly lost
when every unexpected-property name was removed. No confirmed request-construction
or prompt/schema mismatch was found. No behavioral remedy is justified yet.

Ranked working hypotheses, not findings of causation:

1. **Workload-dependent output-shape drift:** supported by full-schema synthetic
   success versus the evidence-bearing request's root-field violations. Longer
   instructions, evidence structure and citation density are confounded; none is
   individually proven responsible. Length alone is not a contradiction or a
   demonstrated context-window overflow.
2. **Sampling difference:** 0 versus default 0.7 is a concrete, reproducible
   difference. No paired test shows it explains failure or that zero fixes it.
3. **Input-shape/metadata copying:** the large view and historical flags could
   encourage schema confusion despite explicit instructions. The rejected unknown
   name and completion are unavailable, so wrapper copying remains speculative.
4. **Runtime app policy/memory/transformation effects:** source paths and differing
   app identities exist, but no retained trace establishes their application.
   The offline zero-redaction result weakens ordinary redaction as an explanation.
5. **Thinking-switch effect:** the requests differ, but this installed model row
   deliberately supplies no thinking control and records its effect as absent in
   prior local tests. Little evidence supports this as the main cause.

A universally unsupported full schema is poorly supported: both saved unchanged
full-schema outputs still validate. Successful probes do not establish reliable
NVIDIA enforcement for every input, and the runtime's post-validator is only a gate.

**Proposed, not executed:** a two-call paired synthetic test, holding model, full
schema, schema name, app identity, messages, cap, thinking options and all other
settings constant. Use the unchanged production system prompt and a modest
synthetic record/fact payload with no ARGUS evidence or ground truth. Call A sends
temperature **0.7**, call B **0**. This isolates sampling under the production
instruction set and captures the new safe unknown-name diagnostics on rejection.
If both pass, it weakens a simple prompt/sampling explanation for that small input;
it does not rule out real-evidence size/content or runtime-state effects. If only
one fails, that motivates replication, not an immediate production change. A
subsequent separately approved pair could hold temperature fixed and change only
synthetic payload size. No experiments are authorized or scheduled by this report.

## Verification and files

**287 tests passed**, including all 280 prior tests and seven diagnostic regressions.
All inference HTTP calls in tests were mocked. `git diff --check` passed. No new
dependency, live inference, fallback, repair, installed-source modification, commit
or push was performed.

Tracked changes: this report, `scripts/reasoning_diagnostics.py`, and
`tests/test_runtime_rejection_diagnostics.py`. The prompts/provider/schema/validators
are unchanged. Existing tests exercise their behavior without modification.

New ignored audit artifacts are under `artifacts/assessments/phase3b6-audit/`:

- `ALT0018.reconstructed.request.json`: reconstructed payload only, no headers.
- `request-audit.json`: counts, hashes, schema comparison and option inventory.
- `installed-runtime-audit.json`: offline adapter/redaction/default observations.
- `audit_offline.py` and `inspect_runtime_offline.cjs`: offline reproductions;
  openers/fetch are stubbed and credentials are placeholders.

Reproduce offline without Nightshift or Zetaris running:

```powershell
$argusPython = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe"
& $argusPython artifacts/assessments/phase3b6-audit/audit_offline.py
& "$env:LOCALAPPDATA\Nightshift\node\node.exe" artifacts/assessments/phase3b6-audit/inspect_runtime_offline.cjs
& $argusPython -m unittest discover -s tests -v
```

The reproduction artifacts depend on retained local probe/failure files and the
installed runtime source. They are not required by the automated test suite and
will not exist in a clean clone because assessment artifacts are Git-ignored.
