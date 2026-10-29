# Phase 3A: deterministic evidence collection

This phase collects evidence for an arbitrary security alert ID. It does not
classify an alert, identify an attacker, resolve an incident, generate a narrative
assessment, or invoke an LLM. Collection status describes data availability and
consistency, not security concern. Every operational record comes from the
existing `ARGUS_SECURITY.*_compat_test` tables through the REST client.

## Components and invocation

- `scripts/evidence_queries.py`: a closed query catalogue with typed parameters.
- `scripts/evidence_collector.py`: `ZetarisEvidenceSource` handles retrieval;
  `EvidenceCollector.collect` produces a snapshot; `build_bundle(snapshot)` is
  pure construction without I/O, clocks or generated UUIDs. `build_trace` handles
  the separate volatile transport provenance.
- `scripts/collect_evidence.py`: CLI, credential loading and JSON artifact writing.
- `scripts/zetaris_client.py`: existing transport safeguards, extended with
  `execute_evidence_query`. The original fixed-SQL allowlist remains unchanged.

Configure the existing ignored `.env.local` as described in [REST setup](zetaris_rest.md).
From the repository root:

```sh
python scripts/collect_evidence.py ALT0017
python scripts/collect_evidence.py ALT0018 --before-hours 48 --after-hours 1 --row-limit 300
python -m unittest discover -s tests -v
```

On the current Windows Python installation:

```powershell
$argusPython = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe"
& $argusPython scripts/collect_evidence.py ALT0017
& $argusPython -m unittest discover -s tests -v
```

`--output path/to/bundle.json` changes the destination. `--env-file` selects local
configuration; `--max-queries` changes the total query budget. Defaults are
repository-relative, independent of the working directory. An invocation writes:

```text
artifacts/evidence/<alert_id>.json
artifacts/evidence/<alert_id>.trace.json
```

The first is the deterministic evidence bundle. The second contains volatile
request UUIDs, wire query UUIDs, timing metadata and response hashes. Existing
artifacts at those paths are replaced; operational files are never overwritten.
Outputs must be JSON files outside `data/`, `evaluation/` and `.git/`; symlink
and hardlink output files are refused. The CLI rejects an output containing the
configured API key before writing either file, and emits only counts/status/paths.

Exit code 0 means `collected`; 2 means a written partial, unavailable, or unknown
alert bundle; 1 means an invalid invocation/configuration or output failure.
Partial bundles remain useful evidence and must not be interpreted as complete.

## Bounded collection plan

The collector follows one finite plan for all alerts, including multi-user alerts:

1. Look up the alert by its original `alert_id`.
2. Decode `related_event_ids` using the existing tested exporter decoder. Preserve
   original order, duplicate references and the encoded source cell. Resolve each
   selected ID against both event tables, without guessing its type from its prefix.
3. Resolve supporting successful authentication sessions for referenced accesses.
   These authentications may be outside the history window and are labelled `session`.
4. Collect authentication and access histories for the referenced/alert users and
   source IPs. Users and IPs are queried in separate bounded batches and merged.
   The default inclusive interval is **alert time minus 24 hours through alert time
   plus one hour**. An explicitly referenced event is retained outside this interval.
5. Resolve sessions behind contextual access records. Fetch user metadata for every
   observed user and resource metadata for every observed access resource.
6. Look up exact approved-network records for all observed IPs, retaining their
   original `status`. No match is a network lookup result, not a maliciousness label.

History subjects are expanded only once, from the alert, explicit events and their
initial sessions. Other users discovered through a shared IP get metadata, but do
not recursively trigger more history searches. This prevents open-ended collection.
Every source record retains provenance from all queries that retrieved it.

Defaults and hard bounds are:

- Before window: 24 hours, configurable from 1 through 168.
- After window: one hour, configurable from 0 through 24.
- Per-query row limit: 200, configurable from 1 through 1,000 and capped by
  `ZETARIS_QUERY_LIMIT` if that is lower.
- Total executed queries: 80, configurable from 1 through 200.
- Distinct explicit references: 100, configurable through the Python options
  from 1 through 200; omitted references remain listed with their status.
- Distinct subjects for each enrichment stage: 100, configurable through Python
  from 1 through 200. Skipped values appear in `subject_limit` issues.
- IDs/IPs per filter: at most 20. Event queries order by timestamp and event ID;
  entity queries order by the original entity key.

At or above a row cap, the query is **potentially truncated**, even when the last
row might coincidentally be the final match. A reported total exceeding returned
rows also triggers this flag. No rows are silently sliced. There is no pagination,
automatic retry or attempt to retrieve entire event datasets. Budgets and failed
queries appear explicitly. Server-side scan costs are not bounded by row limits.

## SQL restrictions and timestamp handling

New queries use only fixed table names and fixed columns, with templates for
primary-key IN lookup, successful-session lookup, exact IP lookup, and inclusive
user/IP histories. Identifiers must match `[A-Za-z][A-Za-z0-9_-]{0,63}`. IPs must
be literal valid IPv4/IPv6 addresses with no zone IDs. Apostrophes, SQL fragments,
caller-selected columns/tables/functions and arbitrary SQL are not accepted.
Time-window bounds must be canonical, timezone-aware UTC timestamps. The existing
`execute_read_only` SQL-string interface does not accept these new generated queries;
only the typed `execute_evidence_query` path does. No write or DDL template exists.

During live collection, Zetaris was observed to return inferred TIMESTAMP columns
as timezone-less strings such as `2026-09-15 01:28:00.0`. The collector initially
reported an unavailable history window instead of assuming UTC. Time-bearing
queries now include three explicitly separated SQL provenance projections:

```sql
current_timezone() AS _argus_session_timezone,
typeof(timestamp) AS _argus_timestamp_type,
date_format(timestamp, 'yyyy-MM-dd HH:mm:ss.SSSSSS') AS _argus_sql_timestamp
```

These are fixed Spark SQL functions ([official function reference](https://spark.apache.org/docs/latest/api/sql/index.html)),
not model-selected expressions. The transport adapter moves their results into
per-query `timestamp_context`, keyed by the source record fingerprint. They are
not inserted into the original source record. `headers` in a query summary still
preserves the complete returned header list, including these projection headers.

An explicit source timezone/offset is used directly. Otherwise, the collector
requires a same-query SQL session reported as UTC (UTC/Etc/UTC/GMT/Z/+00:00), an actual
`timestamp`/`timestamp_ltz` type, and a valid projected SQL timestamp. Only that
verified derived value is interpreted as UTC. Non-UTC sessions, timestamp-without-
timezone types, naive string columns, missing projections or conflicting temporal
evidence do not authorise an assumption. Raw timestamps are never rewritten.
Subsecond source values are preserved; derived SQL context supports microseconds.
Future nanosecond or non-UTC timezone support needs an explicit adapter.

## Evidence bundle schema, version 1.0

The bundle is a JSON object with the following fields. This is the normative
schema documentation; no third-party JSON Schema validator is required.

`schema_version`: string `"1.0"`.

`alert_id`: the requested original source ID, not an internal incident identifier.

`collection_status`: `collected`, `partial`, `alert_not_found`, or `alert_unavailable`.
`collected` means the planned bounded collection completed without recorded data
quality/completeness issues. It is not a claim that all relevant evidence exists
within the chosen scope. Duplicate reference positions alone do not make it partial.
Conflicting alert variants stop expansion instead of choosing an arbitrary anchor.

`scope`: contains `options` (all effective planning options), `history_window`
(`start` and `end` in UTC, or null when unavailable), and the named one-pass
history-subject expansion rule. Per-query `limit` records the actual transport cap.

`records`: an object keyed by each of the six logical dataset names. Each value
is an array of source-record envelopes:

```json
{
  "source": {
    "table": "ARGUS_SECURITY.authentication_logs_compat_test",
    "id_column": "event_id",
    "id": "AUTH000001"
  },
  "variant_id": "<SHA-256 of canonical original field values>",
  "record": {"event_id": "AUTH000001", "other_original_columns": "..."},
  "roles": ["contextual", "referenced"],
  "query_ids": ["Q<stable query fingerprint>"],
  "timestamp_interpretation": {
    "utc": "2026-09-07T08:00:07Z",
    "basis": "explicit_source_offset"
  }
}
```

The illustrative `record` above is abbreviated: real envelopes retain every original
column returned by the source. `timestamp_interpretation` is present only when
time can be verified; its alternative basis is `verified_same_query_utc_projection`.
All roles are collection roles: `alert`, `referenced`, `contextual`, `session`,
`metadata`, or `network`. A record can have multiple roles. Metadata does not
become explicitly referenced event evidence just because it was joined later.

Exactly identical rows are merged while retaining all query provenance. Rows
sharing a source ID but differing in any original field remain separate variants,
with a `conflicting_source_records` issue. Variant IDs supplement original IDs;
they never replace them. Duplicate rows within one response are also reported.
Every usable evidence record has its original key in both `source.id` and `record`.

`references`: one entry per original array position, including duplicates:
`position`, `event_id`, `duplicate`, `status`, and `records` (source table/ID/variant
links). Status is `resolved`, `ambiguous`, `missing`, `unavailable`,
`potentially_truncated`, `invalid_identifier`, or `not_collected_due_to_limit`.
A match discovered through history can resolve a reference; its actual retrieval
roles/provenance remain intact. An empty successful lookup is `missing` only if
both event-table lookups completed; an unavailable lookup cannot establish absence.

`network_checks`: one entry per observed nonempty source IP, with `source_ip`,
`status`, and matching `network_ids`. `matched` retains both active and inactive
catalog records; inspect each original `status`. `no_exact_match` means a completed
lookup found no row. Other statuses are `unavailable` or `potentially_truncated`.
Approval/absence is not a verdict about the operator's intent.

`queries`: stable summaries containing `query_id`, validated `scope`, exact fixed-
template `sql`, actual `limit`, `status`, `returned_rows`, `reported_total`,
original `headers`, `timestamp_context`, and safe `error_type`. Query status is
`complete`, `potentially_truncated`, `unavailable`, `malformed`, or
`budget_exhausted`. `unusable_rows`, when present, retains data that could not
be admitted as source records. Query IDs hash template SQL, scope and limit.

`issues`: deterministic, deduplicated objects with `code` and relevant source
table/ID, query ID, field or reference position. They include unavailable/truncated
queries, missing/unavailable user/resource/session context, mismatched session
owner or IP, access before authentication, malformed reference lists, invalid
fields, budget omissions, duplicate/conflicting source records and scope violations.
They contain data-quality facts, not incident classifications. No raw exception
or server error message is copied into an issue.

Original nulls and empty strings remain distinct. Large integers are never routed
through floats; original numeric strings remain strings. A native Decimal becomes
`{"$decimal": "<exact decimal text>"}` explicitly, rather than losing precision
during JSON serialisation. Structured fields/locations remain encoded except that
the existing decoder is used to follow alert references. No second codec exists.

## Trace, determinism and completeness

The separate trace contains stable query IDs plus request/wire query UUIDs,
HTTP error status, response hashes and response metadata. Data records are in the
bundle; unreadable-header responses retain their data in the trace when available.
Successful JSON responses malformed before the transport can parse them and
HTTP error bodies are not archived, preserving the existing secret-safe error
policy. Such failures are explicit; they are not treated as empty results.
Unknown successful response metadata remains in the trace, not silently dropped.
Additional top-level metadata also produces an `unreviewed_response_metadata`
issue and names the extra fields in its query summary, so a new server warning
cannot silently become a fully reviewed collection. Conflicting verified timestamp
projections are reported as data-quality issues while source records remain intact.

For identical source rows, metadata context, options and query-completeness
outcomes, the main bundle is byte-identical across runs. Construction sorts
source variants, roles, query provenance, issues and query summaries and omits
wall-clock times, random UUIDs and server timings. Traces intentionally differ.
Files use UTF-8 and LF. Determinism does not imply a transactionally consistent
snapshot: Zetaris can change between individual requests.

## Validation and limitations

Normal tests mock Zetaris and cover valid/unknown IDs, multi-event/multi-user
alerts, missing records/catalog context, duplicates, empty histories, query
failures/truncation, malformed responses, timestamp provenance, contradictions,
deterministic serialisation, source IDs, injection rejection and local-read guards.
Existing tests remain intact. Normal discovery does not contact a live instance.
The final complete suite passed **105 tests**, including all 72 pre-existing
tests and 33 new collector/query tests.

The collector does not read local telemetry CSVs or ground truth. Imports reuse
schema constants and the exporter decoder only; neither import performs data I/O.
Do not register evaluation material in Zetaris or grant the future agent access
to it. Source account permissions remain the real security boundary; malicious
Python with access to the API token is outside client-side SQL restrictions.

There is no pagination, asynchronous cancellation, cross-request transaction,
automatic retry, recursive relationship expansion or claim that bounded history
is the employee's full baseline. Source/user/resource catalogues are current
metadata rather than historical snapshots. A failed transport can leave some
planned links unresolved. Output files are not written as one transaction;
regenerate after an OS write failure. Artifact files contain operational evidence
and should be shared only with the intended analyst/evaluation workflow.

## Live verification on 29 October 2026

The collector was run against the configured local Zetaris installation after
automated tests. These are successful evidence collections, not completed
investigations or assessments:

- **ALT0017**, `UNUSUAL_DATA_EXPORT`: 11 queries; its one explicit reference
  resolved. The bundle contains 8 authentications, 10 accesses, 1 user, 5
  resources and the alert. ACCESS001994 retains its returned transfer value of
  2400000000. Exact network lookups completed with no matching approved records.
- **ALT0015**, `MULTIPLE_FAILED_LOGINS`: 10 queries; all 3 explicit references
  resolved. The bundle contains 8 authentications, 10 accesses, 1 user, 5
  resources and the alert.
- **ALT0018**, `IMPOSSIBLE_TRAVEL`: 11 queries; both explicit references resolved.
  The bundle contains 7 authentications, 20 accesses, 1 user, 6 resources and
  3 network records. The referenced authentications share DEV-U001-01. Source
  203.0.113.10 exactly matches active corporate VPN N005, with advertised New York
  egress. This is network context relevant to interpreting the automated alert;
  the collector makes no judgement about intent.

All three bundles have `collection_status = collected`, zero recorded issues
and no query marked potentially truncated. Samples and their traces are in
[`artifacts/evidence/`](../artifacts/evidence/), including
[ALT0017.json](../artifacts/evidence/ALT0017.json) and
[ALT0018.json](../artifacts/evidence/ALT0018.json). Alert types and referenced
events were inspected from the fetched operational evidence; the collection
workflow did not consult evaluation labels or local telemetry CSVs.

The live server reported `GMT`, type `timestamp`, and consistent SQL timestamp
projections. An independent repeated ALT0017 collection into a temporary
directory produced the identical main-bundle SHA-256:
`477468df421a33742f4865a517aeadf05c9c3a79a7a73c0490f805128b4c9de5`.
Wire traces deliberately have different request UUIDs and timings.
