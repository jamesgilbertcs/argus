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
to a future investigator. No AI agent, LLM calls, Zetaris connectivity, orchestration,
or frontend is implemented in this phase.
