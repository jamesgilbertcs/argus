# ARGUS

**Finding order in digital chaos.**

AI-powered security incident investigation through autonomous evidence gathering and analysis.
ARGUS is being developed for the Open Agent Hackathon 2026 to help human analysts
correlate evidence, evaluate competing explanations, and assess suspicious activity.

Phase 1 provides a fictional security environment for **Meridian Analytics Ltd**:
25 employees, 15 resources, 10 network records, 1,000 authentication attempts,
2,000 resource accesses, and 20 monitoring alerts over 14 days. It includes ordinary
activity and four designed investigations. All identities and telemetry are synthetic.

Use Python 3.10 or later; no third-party packages are required. From the repository root:

```sh
python scripts/generate_data.py
python scripts/export_zetaris.py
python -m unittest discover -s tests -v
```

If Python is available as `py` on Windows, substitute `py` for `python`.
The default seed is `2026`. Generation overwrites the six CSV files in `data/`
and the separate evaluation JSON with deterministic output. To experiment without
changing the reference dataset:

```sh
python scripts/generate_data.py --seed 42 --output-dir scratch/seed42
```

The tests validate the repository's reference files, including their agreement with
the default generator output. Restore the default seed before testing after any
in-place experiments.

For Zetaris, upload the six files from `data/zetaris/`. The standalone
`scripts/export_zetaris.py` reads the canonical CSVs without regenerating or
modifying them. It exports reversible pipe-framed lists and escapes unsafe text
characters to avoid embedded CSV quotes/commas. After changing canonical data,
rerun the exporter. It prints row counts, byte sizes, and SHA-256 checksums.
See [Zetaris exports and manual validation](docs/zetaris_exports.md) for encoding,
decoding, registration under `ARGUS_SECURITY`, and SQL checks. Local tests do
not establish successful Zetaris ingestion.

See [the synthetic environment documentation](docs/synthetic_environment.md) for
schemas, relationships, scenarios, assumptions, and import guidance.
[`evaluation/scenario_ground_truth.json`](evaluation/scenario_ground_truth.json)
contains evaluation answers: keep it separate from the operational evidence supplied
to an investigator. Autonomous investigation, orchestration and a frontend are
not implemented.

The read-only direct REST client is in `scripts/zetaris_client.py`. Configure
`ZETARIS_REST_URL`, `ZETARIS_ORG_ID`, and `ZETARIS_API_KEY` in the existing
Git-ignored `.env.local` (see `.env.example` for placeholders), then run:

```sh
python -m unittest discover -s tests -v
python scripts/check_zetaris.py
```

The second command explicitly performs live, read-only integration checks against
the existing `ARGUS_SECURITY.*_compat_test` tables. Normal tests mock HTTP and
require no live instance. See [REST setup, protocol, and safeguards](docs/zetaris_rest.md)
for the exact query allowlist, response representation, troubleshooting, and
the distinction between automated tests and live verification.

Phase 3A adds bounded, deterministic evidence collection for any alert ID:

```sh
python scripts/collect_evidence.py ALT0017
```

This reads operational evidence through Zetaris and writes a JSON bundle plus a
separate wire trace under `artifacts/evidence/`. It resolves references, sessions,
bounded histories, users, resources and exact network matches, recording missing
or incomplete evidence explicitly. It produces no incident assessment. See the
[evidence-bundle schema and collection guide](docs/evidence_collection.md).

Phase 3B reasons over an **existing** bundle without querying Zetaris:

```sh
python scripts/reason_evidence.py artifacts/evidence/ALT0018.json --provider mock
python scripts/reason_evidence.py artifacts/evidence/ALT0018.json --provider openai
```

Provider selection is mandatory. `mock` produces a labelled, deterministic plumbing
demonstration with no model interpretation. `openai` requires `OPENAI_API_KEY` and
`ARGUS_OPENAI_MODEL` in the environment or ignored `.env.local` and sends the compact
evidence view to OpenAI. It has no tools or database access. Both modes validate
structured citations and acknowledge collection limitations before writing an
assessment and its reasoning view to `artifacts/assessments/`. Validation does not
establish factual correctness; a human must review provisional assessments.
See [reasoning schemas, safeguards and provider verification](docs/evidence_reasoning.md).
