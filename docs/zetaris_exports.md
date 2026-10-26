# Zetaris-compatible evidence exports

## Purpose and boundary

Canonical Phase 1 evidence lives in the six CSV files directly under `data/`.
`scripts/generate_data.py` creates them, using correct standard CSV quoting and
JSON arrays in `resources.allowed_departments` and
`security_alerts.related_event_ids`. Those files remain the source of truth.

In the project's manual Zetaris trial, multi-element JSON arrays were truncated,
closing brackets disappeared, and doubled CSV quotes appeared in query results.
A small independent test reproduced the problem. Attempts to add `quote` and
`escape` settings produced malformed DDL (`quote """, escape """`) and failed in
`LightningDdlExtensionParser` before reading the CSV. These are reported observations
of the current deployment/configuration, not a claim about every Zetaris CSV reader.

`scripts/export_zetaris.py` makes a separate representation with no quoted cells,
embedded cell commas, double quotes, or multiline records. It reads only the six
explicit operational filenames, preserves schemas, ordering and row counts,
and never calls the generator. It neither reads nor copies scenario ground truth.
It imports schema constants from the generator to prevent schema drift.

`evaluation/scenario_ground_truth.json`, evaluation labels, tests and scenario
answers must remain outside the investigating agent's accessible sources. No new
metadata columns, verdicts or sidecar files are added to the exports. The export
directory must contain only the six expected CSVs; mixed directories are rejected.
An unrelated `data/test.csv`, if present, is not an input and is not copied.

## Generate and test

From the repository root, with Python 3.10 or later:

```sh
# Export existing canonical data; does not regenerate it.
python scripts/export_zetaris.py
python -m unittest discover -s tests -v
```

Defaults are resolved relative to the repository, independent of the shell's
working directory. Outputs are the six matching CSV filenames in `data/zetaris/`.
The script prints each file's row count, byte size and SHA-256 checksum.

For a different canonical dataset, supply the directory containing its six CSVs:

```sh
python scripts/export_zetaris.py --input-dir scratch/seed42/data --output-dir scratch/seed42/zetaris
```

Without `--output-dir`, the destination is `<input-dir>/zetaris/`. Canonical input
and repository canonical output paths are protected, including resolved path aliases
and hard links. Exporting into `data/` is refused. Inputs are fully parsed and
encoded before any output files are written, so malformed data does not replace
an existing valid export. Files are UTF-8 without a BOM, with LF line endings and
a header row; identical inputs produce identical bytes. No random seed is used.
Outputs are derived from the input seed already used in Phase 1.
The scoped `.gitattributes` rule also keeps these export CSVs at LF on checkout.

If you intentionally change/regenerate the synthetic environment:

```sh
python scripts/generate_data.py --seed 2026
python scripts/export_zetaris.py
python -m unittest discover -s tests -v
```

Generation changes canonical files only when explicitly invoked; it does not
refresh Zetaris exports automatically. The tests compare repository exports with
a fresh export, so stale exports fail the reproducibility check. Phase 1 tests
still expect the default reference dataset. They are retained unchanged, and
their additional generation runs use temporary directories.

If Python is not on PATH on the current Windows workstation, use its installed
interpreter (or the same interpreter configured in the IDE):

```powershell
$argusPython = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe"
& $argusPython scripts/export_zetaris.py
& $argusPython -m unittest discover -s tests -v
```

## Audit of all six schemas

The default fixture audit found commas in every `users.primary_location` (25),
`approved_networks.advertised_location` (10), and
`authentication_logs.reported_location` (1,000). All 15 `allowed_departments`
and 20 `related_event_ids` cells contain JSON quotes, with internal commas in
multi-element arrays. The remaining fields have no embedded commas, double
quotes, CR/LF, control characters, percent signs or pipes. In particular,
`security_alerts.description` currently needs no character changes, but it is
a narrative field that could contain commas or quotes in future data.
No current value contains a literal pipe or percent sign.

The following are the explicit transformation rules:

- `resources.allowed_departments` and `security_alerts.related_event_ids`:
  decode JSON string arrays and encode pipe-framed lists, as specified below.
- `users.display_name`, `users.role`, `users.primary_location`,
  `resources.resource_name`, `approved_networks.description`,
  `approved_networks.advertised_location`, `authentication_logs.reported_location`,
  and `security_alerts.description`: escape reserved characters if present.
  Text without reserved characters remains byte-for-byte identical as a field value.
- Every other column is passed through unchanged. Reserved characters appearing
  in these columns cause a contextual error rather than an implicit schema or
  identifier change. Add an explicit rule and tests if the schema evolves.

Thus only five columns actually change in the current dataset: the two array
columns and three location columns. Spaces and slashes in departments remain
literal. Identifiers, IPs, device IDs, UTC timestamps, departments, sensitivities,
authentication outcomes, alert classifications and integer digits remain exact.
`resource_access_logs.csv` is byte-identical to the canonical file. No numeric
parsing, reformatting, precision loss or thousands separators are introduced.

## Reversible encoding

Text escaping replaces these characters with uppercase percent-encoded UTF-8 bytes:
percent (`%25`), comma (`%2C`), double quote (`%22`), pipe (`%7C`), backslash
(`%5C`), Unicode control characters, and Unicode line/paragraph separators.
Examples of controls are LF (`%0A`), CR (`%0D`), tab (`%09`) and NUL (`%00`).
Other text, including spaces, slashes, apostrophes, plus signs and normal Unicode,
is preserved. `London, UK` becomes `London%2C UK`. A literal `%2C` becomes
`%252C`, so decoding once recovers the literal text without interpreting it twice.
This is a custom minimal percent codec, not form encoding: `+` is never a space.

For arrays, escape each element independently, join elements with `|`, and frame
the result with leading and trailing pipes. Examples:

```text
Canonical JSON                         Exported field
["Finance", "IT / Security"]            |Finance|IT / Security|
["Sales"]                              |Sales|
["Sales|Engineering", "100%", "a,b"]     |Sales%7CEngineering|100%25|a%2Cb|
[]                                     |
[""]                                   ||
["", ""]                               |||
missing (empty canonical CSV cell)      empty export CSV cell
```

Order, duplicates, empty strings and every element are preserved. The framing
also supports exact SQL membership checks without matching substrings. Missing
is distinct from an empty list and an empty element. The Phase 1 fixture currently
has only non-empty arrays of non-empty strings, but the codec supports these future
cases without modifying canonical Phase 1 validation rules.

Use the supplied decoder after normal CSV parsing:

```python
from scripts.export_zetaris import decode_array, decode_row, decode_text

assert decode_array('|Finance|IT / Security|') == ['Finance', 'IT / Security']
assert decode_array('|Sales|') == ['Sales']
assert decode_array('|') == []
assert decode_array('||') == ['']
assert decode_array('') is None
assert decode_text('London%2C UK') == 'London, UK'
# decode_row('resources', parsed_row) restores fields and serialises arrays as JSON.
```

`decode_row` normalises JSON array whitespace; it preserves the array's semantic
value, not the original JSON source formatting. Non-array fields round-trip exactly.
The decoders reject malformed/non-canonical percent escapes and invalid UTF-8.
The exporter rejects invalid JSON, JSON null/objects, nested arrays and non-string
array elements instead of dropping/coercing values. CSV headers, row widths,
quoting and UTF-8 are checked with the standard-library CSV reader. Export validation
concerns representation; run the full integrity suite for security semantics.

## Manual upload and registration

The steps follow Zetaris's published [local file upload guide](https://kbase.zetaris.com/knowledge/how-to-ccnnect-to-json-from-local-machine)
and [file-source registration overview](https://kbase.zetaris.com/knowledge/connect).
The [CSV connection guide](https://kbase.zetaris.com/knowledge/connection-to-aws-s3)
documents delimiter, header and schema-inference settings. The storage here is the
existing NDP file store, not S3. Labels may vary by deployed version; retain the
deployment's working NDP storage selector and uploaded file paths.

1. Run the exporter and tests. In **NDP File System**, open `file-store`, create a
   separate folder such as `argus_zetaris`, and upload exactly the six local
   `data/zetaris/*.csv` files using **Upload → Browse Files**. Do not upload the
   parent `data/` directory, `evaluation/`, source code or tests.
2. Open **SQL Workspace** (or **NDP Fabric Builder**) and locate the existing
   **Virtual File Source** named **ARGUS_SECURITY**. Use its **+** action to register
   a file. Choose **File Store**, CSV format, the uploaded NDP file path, comma as
   delimiter, and `header = true`. Do not add the failed custom quote/escape parameters.
3. Register each file using its stem as the table name: `users`, `resources`,
   `approved_networks`, `authentication_logs`, `resource_access_logs`, `security_alerts`.
   If that table already exists, use the deployment's supported edit/re-register
   workflow to point it at the new export file. Check its path explicitly; do not
   leave the old canonical registration active under the name used by these queries.
4. Inspect preview/schema before confirming. Identifiers and encoded lists/text
   must be strings; `bytes_transferred` must be BIGINT/64-bit integer, never 32-bit
   integer or floating point. `inferSchema = true` can provide an initial schema,
   but inspect the result. Keep timestamp strings intact for the initial checks,
   or verify UTC second precision explicitly if the deployment infers a timestamp.
   Set UTC in any client/session that displays typed timestamps. If schema overrides
   are unavailable and inference is unreliable, register strings and use explicit
   BIGINT casts for validation; fix the registered numeric type before agent use.
5. Confirm that empty `user_id`, `session_id`, and `failure_reason` cells stay
   empty/null, not text such as `"NULL"`. A missing array may become SQL NULL;
   encoded `|` must remain a literal string, preserving the empty-list distinction.
6. Run all checks below in the SQL editor. Retain results and the local export
   checksums as the manual ingestion record. Preview alone and matching row counts
   are insufficient to establish that evidence survived parsing.

No upload, registration or query has been executed by the exporter. These steps
are manual validation instructions, not a claim of a successful integration.

## SQL validation under ARGUS_SECURITY

These examples use the inspected repository column names and Zetaris's documented
`source.table` convention and SQL operations ([SQL manual](https://kbase.zetaris.com/knowledge/sql-manual)).
They are intended to be run manually; they have not been tested against this
Zetaris deployment. Expected values below refer to the current default seed 2026.

**All row counts:**

```sql
SELECT 'users' AS dataset, COUNT(*) AS row_count FROM ARGUS_SECURITY.users
UNION ALL SELECT 'resources', COUNT(*) FROM ARGUS_SECURITY.resources
UNION ALL SELECT 'approved_networks', COUNT(*) FROM ARGUS_SECURITY.approved_networks
UNION ALL SELECT 'authentication_logs', COUNT(*) FROM ARGUS_SECURITY.authentication_logs
UNION ALL SELECT 'resource_access_logs', COUNT(*) FROM ARGUS_SECURITY.resource_access_logs
UNION ALL SELECT 'security_alerts', COUNT(*) FROM ARGUS_SECURITY.security_alerts;
```

Expect 25, 15, 10, 1,000, 2,000 and 20 respectively.

**Array parsing and department permissions:**

```sql
SELECT resource_id, allowed_departments
FROM ARGUS_SECURITY.resources
WHERE resource_id IN ('R001', 'R003', 'R006')
ORDER BY resource_id;

SELECT resource_id, resource_name, sensitivity
FROM ARGUS_SECURITY.resources
WHERE allowed_departments LIKE '%|Finance|%'
ORDER BY resource_id;
```

Expect `R001 = |Sales|`, `R003 = |Finance|Human Resources|`, and
`R006 = |Engineering|IT / Security|`, with both framing pipes and all elements.
The Finance query must return exactly R002, R003, R007, R009, R010, R012 and R015.
Do not use a JSON parser on the exported lists. For future names with escaped
characters, encode the membership token using `encode_text` before constructing
the predicate. Escape SQL LIKE metacharacters if the token includes `%` or `_`;
the five current department names have neither.

**Authentication values, locations and counts:**

```sql
SELECT event_id, timestamp, user_id, source_ip, reported_location,
       device_id, session_id, authentication_method, outcome, failure_reason
FROM ARGUS_SECURITY.authentication_logs
WHERE event_id IN ('AUTH000962', 'AUTH000963')
ORDER BY timestamp;

SELECT outcome, COUNT(*) AS event_count
FROM ARGUS_SECURITY.authentication_logs
GROUP BY outcome;
```

Expect the two timestamps `2026-09-10T10:05:00Z` and `2026-09-10T10:11:00Z`,
user U001, device DEV-U001-01, sessions SES000962 and SES000963, source IPs
198.51.100.51 and 203.0.113.10, and locations `London%2C UK` and `New York%2C US`.
Both are PASSWORD_MFA/SUCCESS. Counts are SUCCESS 871 and FAILURE 129.

**Session/resource/user relationships and access outcomes:**

```sql
SELECT COUNT(*) AS invalid_accesses
FROM ARGUS_SECURITY.resource_access_logs r
LEFT JOIN ARGUS_SECURITY.authentication_logs a ON r.session_id = a.session_id
LEFT JOIN ARGUS_SECURITY.users u ON r.user_id = u.user_id
LEFT JOIN ARGUS_SECURITY.resources p ON r.resource_id = p.resource_id
WHERE a.event_id IS NULL OR a.outcome <> 'SUCCESS'
   OR r.user_id <> a.user_id OR r.source_ip <> a.source_ip
   OR r.timestamp <= a.timestamp OR u.user_id IS NULL OR p.resource_id IS NULL;

SELECT outcome, COUNT(*) AS event_count
FROM ARGUS_SECURITY.resource_access_logs
GROUP BY outcome;
```

Expect zero invalid accesses, SUCCESS 1,938 and DENIED 62. The timestamp comparison
also works lexically for canonical UTC strings in this fixed format. Check schema
types first if one table inferred a timestamp and another retained strings.

**Exact large integers, with numeric filtering:**

```sql
SELECT event_id, resource_id, action, outcome,
       CAST(bytes_transferred AS BIGINT) AS bytes_transferred
FROM ARGUS_SECURITY.resource_access_logs
WHERE CAST(bytes_transferred AS BIGINT) > 100000000
ORDER BY CAST(bytes_transferred AS BIGINT) DESC;

SELECT MAX(CAST(bytes_transferred AS BIGINT)) AS largest_transfer
FROM ARGUS_SECURITY.resource_access_logs;
```

Expect exactly ACCESS001994 / R012 / EXPORT / SUCCESS / **2400000000** and
ACCESS002000 / R002 / EXPORT / SUCCESS / **950000000**; max is **2400000000**.
Any overflow, null or rounding here means the ingest/type configuration needs fixing.

**Check the longest alert list and its final member survived:**

```sql
SELECT alert_id, user_id, related_event_ids, LENGTH(related_event_ids) AS encoded_length
FROM ARGUS_SECURITY.security_alerts
WHERE alert_id = 'ALT0020';

SELECT COUNT(*) AS complete_alert
FROM ARGUS_SECURITY.security_alerts
WHERE alert_id = 'ALT0020'
  AND related_event_ids LIKE '%|AUTH000965|%'
  AND related_event_ids LIKE '%|AUTH001000|%';
```

Expect empty/null user, length **397**, 36 ordered members, and `complete_alert = 1`.
Inspect the complete string, not a UI-truncated cell preview. For full assurance,
retrieve all six ordered table results through Zetaris's supported result-export
facility and compare each parsed field to the local compatible CSV, decoding lists
with this module. Compare values after normalising only the chosen SQL null and
timestamp display conventions; do not compare a query-result CSV's quoting bytes.
Also confirm each table's primary identifier has the expected distinct count and
that none of the evaluation material is registered in `ARGUS_SECURITY`.

## Local guarantees and remaining risks

Unit tests exercise all canonical records, exact identifiers/timestamps/integer
digits, relationships through the original validator, ordered arrays, missing/empty
values, delimiter collisions, Unicode and controls, narrative quotes/newlines,
invalid inputs, path overwrite protection, CLI behaviour and SHA-256 reproducibility.
They check that every exported physical line has exactly the intended number of
comma-delimited cells, with no double quotes or embedded line breaks. They do not
invoke or emulate Zetaris's full parser, schema inference or query runtime.

Percent escapes and pipe framing must be interpreted by downstream investigators
where applicable. Literal encoded locations can be filtered in SQL, and current
department membership is readable without decoding. There is no SQL UDF or connector
installed. Generic URL decoders that apply form encoding or repeated decoding are
not interchangeable with the supplied decoder.

Zetaris may still have deployment-specific behaviour for nulls, Unicode, whitespace,
schema inference, caching or timestamp display. The canonical dataset remains the
authoritative evidence if any check fails. Regeneration requires a subsequent
manual upload and table-path check; do not query stale registrations. Output
writes are not a six-file transaction: an OS write failure can leave partial
exports, which must be regenerated before upload. No Docker, cloud service,
database, third-party Python package or automatic Zetaris connection is introduced.
