# Meterless / Nemotron structured-output capability report

Investigated **6 November 2026**. No ARGUS production code, assessment schema,
validator, dataset, evidence bundle or historical assessment was changed. No
ALT0018 inference was run. Exactly **two** tiny inference requests were made.

## Installed runtime and evidence

- **Nightshift runtime 1.2.7**, confirmed by the installed `ns status --json`
  command's read-only `GET /admin/status` control endpoint. The initial sandboxed
  status check could not read that version; the approved outside-sandbox check
  confirmed it. The daemon binary also embeds `meterlessd/1.2.7`.
- Endpoint: `http://127.0.0.1:8088/v1/chat/completions`.
- Requested and returned model: **nvidia/nemotron-3.5-bolt-e4b**. This identifies
  the routed API model, not an independently inspected backend weight revision.
- ARGUS's existing `MeterlessProvider.generate()` supplies schema instructions in
  messages but sends **no `response_format`**. It currently does not request the
  enforced-format mechanism tested here.
- [Meterless's official guide](https://meterless.ai/help.html) advertises
  structured outputs for the full local runtime. Its separate Web Session Hotspot
  refuses structured outputs; that restriction does not describe this installation.
- [NVIDIA's exact-model API reference](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3_5-bolt-e4b-infer)
  documents `response_format` with `json_schema` for constrained output, plus
  `chat_template_kwargs.enable_thinking`. These are documented capabilities;
  the results below are the actual local observations.

## Live probes

Both requests used the same messages, temperature 0, non-streaming mode, a
256-token cap, and explicitly requested `enable_thinking: false`. No actual
effect of that thinking switch is claimed from these probes. Each POST was made
once without client retries. The new isolated runtime job also records two
successful attempts, with measured usage matching the response envelopes.

The prompt demanded exactly `{"status":"PROMPT_ONLY","unexpected":true}`.
Only the strict schema request, **not its messages**, supplied this schema:

```json
{
  "type": "object",
  "properties": {
    "status": {"type": "string", "enum": ["SCHEMA_ONLY_READY", "SCHEMA_ONLY_BLOCKED"]},
    "count": {"type": "integer"}
  },
  "required": ["status", "count"],
  "additionalProperties": false
}
```

**`response_format: {"type":"json_object"}` — accepted and observed working.**
HTTP **200**, runtime-reported finish reason **stop**, usage **59 prompt / 15
completion / 74 total tokens**. Content:
`{"status": "PROMPT_ONLY", "unexpected": true}`. It parses as one JSON object
but fails the two-field test schema. This mode does not promise schema adherence;
the schema was not sent in this request. This positive probe alone does not prove
provider-side syntax enforcement for every possible prompt.

**`response_format: {"type":"json_schema","json_schema":{"name":"argus_capability_probe","strict":true,"schema":…}}`
— accepted and schema-conforming in the discriminating live probe.** HTTP **200**,
runtime-reported finish reason **stop**, usage **59 prompt / 21 completion / 80
total tokens**. Content: `{"status": "SCHEMA_ONLY_READY", "count": 1}`.
ARGUS's existing structural validator independently confirmed both required
fields, the enum, integer type and absence of extra properties. The result follows
the schema despite the conflicting prompt, using field/enum names available only
in `response_format`: strong evidence that the schema parameter is effective,
not silently ignored on this route. This is more than an HTTP-success check.

## Forwarding and enforcement: what was verified

Installed files under `%LOCALAPPDATA%/Nightshift/engine/` show the complete path:

- `openai.mjs:413–457`: headless preflight, preserves `response_format`, and
  disables the response cache for a structured request.
- `js/engine/proxy.js:646–659`: shared preflight parses/checks formats.
- `js/engine/providers.js:1455–1497` and `1087–1101`: NVIDIA adapter passes the
  caller's format to the OpenAI-shaped request unchanged. **No NVIDIA schema
  translation, prompt-only downgrade or stripping of `strict` is performed.**
- `js/engine/orchestrator.js:3883–3894`: final content is checked before success;
  invalid output produces a typed failure rather than being repaired.
- `js/engine/structured-output.js`: bounded JSON parsing/schema validation;
  NVIDIA's default capability is explicitly **unverified**, not provider-verified.

An **offline mocked-transport check of the actual installed JS files** confirmed
exact forwarding of both formats and preservation of the schema. The installed
validator accepted the valid fixture and rejected wrong enums, missing required
fields, extra properties, invalid JSON and `finish_reason: length`. These are
source-level/mocked checks, **not additional live NVIDIA tests**. The installed
proxy maps format/schema preflight errors to HTTP 400 and invalid generated output
to typed HTTP 502 errors such as `schema_validation_failed`/`response_truncated`.
Those negative HTTP behaviours were not exercised live.

The headless API places format-check metadata under
**`meterless.structured_output`**, not a top-level `structured_output`. The isolated
live recorder captured status, content, model, finish reason, usage and the response
hash, but did not retain that nested metadata. Its printed top-level null therefore
does **not** establish that runtime validation metadata was absent. The existing
ARGUS adapter likewise does not retain this nested block; that is a future
integration consideration, not a change made here.

## Recommendation and remaining limits

Use **explicit strict `json_schema`** in a future, separately reviewed Meterless
integration, while retaining ARGUS's strict parsing, citation, gap and factual
consistency checks. Keep JSON mode and prompt-only mode explicitly distinguished;
never silently downgrade after an API failure. Preserve nested runtime validation
metadata and handle typed HTTP failures without leaking upstream messages.

Confirmed here: both standard request formats are accepted; the small strict
schema is effective end to end; the installed adapter forwards parameters exactly;
the installed runtime contains a rejecting post-validation gate. **Not established:**
the provider's precise decoding algorithm, universal enforcement, full ARGUS
schema support/reliability, nested/recursive schema limits at NVIDIA, streaming
guarantees, or reasoning-enabled behaviour. One successful small-schema response
is not a reliability benchmark and does not establish factual correctness.
The installed validator has schema-size/depth/work limits and treats unknown
schema keywords as annotations; its gate is not a universal JSON Schema engine.
Review the full requested schema against both runtime and provider subsets before
integration, and retain ARGUS's independent acceptance checks.

Separate `guided_json`, regex/GBNF grammar or `nvext` constrained-decoding controls
were not probed. They are not among this installed proxy's generic passthrough
keys, so they are **not verified supported controls through this route**. Use the
documented `response_format.json_schema` mechanism rather than assuming arbitrary
provider-specific parameters will reach NVIDIA. No installation or provider
reconfiguration is indicated by these results.

## Reproducibility artifacts

Local, Git-ignored investigation files are in
`artifacts/assessments/structured-output-probes-20261106/`:
`probe.py`, `json_object.json`, `json_schema.json`, `inspect_installed.cjs`,
`installed-source-check.json`, and `environment.json`. They contain no credentials
or authentication headers. Do not rerun `probe.py` merely to inspect results: each
invocation creates a real inference. The installed-source check is fully offline.

Runtime SHA-256 identifiers:

- `meterlessd.exe`: `f8ea788f11830fa12990bcbfad5e360a4a5a62cac159f3c348c5b7f1ea3670d8`
- `meterless-runtime.exe`: `bbe3b9d0908568d2105d87812dadb69ad85610c69427238cd6b9ee370de91196`

The production test suite was not rerun because production code was unchanged.
Verification consisted of the two live probes, independent response validation,
read-only version/source inspection and the offline installed-module checks.
