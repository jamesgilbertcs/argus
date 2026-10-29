# Read-only Zetaris direct REST client

## Scope and protocol verification

`scripts/zetaris_client.py` is a reusable, standard-library direct REST client.
`scripts/check_zetaris.py` is its opt-in live verification command. There is no
AI investigator, arbitrary SQL execution, JDBC connection, UI-proxy login,
database migration, upload, Meterless integration or NVIDIA integration.
The Phase 1 generator, CSV exporter, canonical datasets, compatibility exports
and ground truth are unchanged.

Before implementation, the official [Getting Started repository](https://github.com/zetaris/getting-started),
[Codex connection guide](https://zetaris.github.io/getting-started/docs/connections/codex-connection.html),
[shared client setup](https://zetaris.github.io/getting-started/docs/connections/client-setup.html),
and [direct REST example](https://github.com/zetaris/getting-started/blob/main/scripts/zetaris_sql.py)
were inspected. The documentation site's two connection pages were not accessible
through the research browser; their source Markdown was read in the official repository.

The configured local instance's `/redoc/docs.yaml` was fetched successfully with
Basic authentication and inspected on 26 October 2026. The full specification
was saved outside the repository because it can contain environment-specific
details. Its SHA-256 was
`f4204e2b199b5f22e63aaff9604f8ecacce9c820fbde4424a3f3022690c57ff6`.
The inspected definitions were `RunSqlRequest`, `QuerySource`, `HeaderRows`,
`HttpError`, and the SQL endpoint's bearer security/header requirements.
No unverified UI-proxy request fields were used.

## Configuration and credentials

Use the existing Git-ignored root `.env.local`, or set process environment
variables. Existing environment variables take precedence, including empty
values (which cause validation errors). The loader does not modify `os.environ`
or write the local file. The existing `.gitignore` `.env.*` rule covers
`.env.local`, and `!.env.example` allows the placeholder template.

Required settings, using placeholders only:

```dotenv
ZETARIS_REST_URL=http://localhost:8888/api/v1.0
ZETARIS_ORG_ID=1
ZETARIS_API_KEY=<your-api-key>
```

The API base must end in `/api/v1.0`, with no username/password, query string or
fragment in the URL. HTTPS is required except for explicit loopback addresses
(`localhost`, 127.x.x.x, or ::1), where local HTTP is supported. Organisation ID
must be a positive int64. The token must be nonempty visible ASCII without
whitespace or header control characters. Create the key through Zetaris's API
Tokens interface; the client does not create keys or request passwords.

Optional settings:

- `ZETARIS_TIMEOUT_SECONDS`: 30 by default, greater than zero and at most 600.
- `ZETARIS_QUERY_LIMIT`: 1,000 by default, an integer from 1 through 10,000;
  sent as the API's `limit` field.
- `ZETARIS_MAX_RESPONSE_BYTES`: 8,388,608 by default; 1,024 through 67,108,864.
  The response is read with this bound; exceeding it fails without returning
  truncated evidence.

The literal dotenv subset accepts blank/comment lines, `KEY=value`, optional
`export`, and matching outer single/double quotes. It does not execute shell
syntax, interpolate variables, unescape backslashes, or support inline comments.
Duplicate keys, malformed lines and files larger than 64 KiB are rejected with
line numbers, never their values. Keep comments on separate lines.
When using environment-only configuration, a nonexistent `env_file` is supported.

`ZETARIS_USERNAME` and `ZETARIS_PASSWORD` are only for the documented Basic-auth
OpenAPI fetch; they are not fields of `ZetarisConfig` and are not sent by the SQL
client. To refresh the spec, follow the official setup guide: GET
`/redoc/docs.yaml` at the **same configured origin**, using those credentials
from local configuration, disabling redirects, and saving outside the checkout.
The bearer API key is not a substitute for Basic authentication on that route.
Never put either kind of credential in a command argument, prompt or report.

## Verified wire format

The query request is:

```text
POST <ZETARIS_REST_URL>/sql-editor/sqls/run
Authorization: Bearer <API key>
X-Org-ID: <numeric organisation ID>
X-Request-ID: <fresh UUIDv4>
Content-Type: application/json
Accept: application/json
```

```json
{
  "queryId": "<a second fresh UUIDv4>",
  "sql": "SELECT 1 AS one",
  "source": "SqlEditor",
  "limit": 1000
}
```

OpenAPI requires `queryId`, `sql`, and `source`; `limit` is optional int32, but
this client always supplies its configured bound. `SqlEditor` is one of the
documented `QuerySource` enum values. Each request gets distinct fresh request
and query UUIDs. Bearer authentication is sufficient: no preliminary login,
cookies, JDBC driver or persistent session is used.

The documented HTTP 200 `HeaderRows` response is an object shaped as follows
(timing here is illustrative):

```json
{
  "headers": ["one"],
  "data": [["1"]],
  "total": 1,
  "timeUsed": 17
}
```

`total` is int32 and `timeUsed` int64; both are required. The spec does not define
the unit of `timeUsed` here, so it is retained without conversion. `headers` and
`data` are optional; data is an array of ordered rows, whose documented cells
are strings. The official guide warns that REST values can be strings and
decimal/exponent notation can occur. Do not assume that JSON transport supplies
SQL column types or timezone information.

The endpoint declares HTTP 400 validation failures as plain text and default
errors as JSON `HttpError` objects with a required string `msg`. HTTP 401/403
become `AuthenticationError`; other HTTP errors become `RequestError`.
Error bodies/reason phrases are deliberately omitted from diagnostics because
they may echo secrets. HTTP status and locally generated request UUID are retained.
An error-shaped response with HTTP 200 does not satisfy `HeaderRows` and is rejected.

## Restricted query interface

```python
from scripts.zetaris_client import ZetarisClient, ZetarisConfig

client = ZetarisClient(ZetarisConfig.from_environment())
result = client.execute_read_only(
    "SELECT COUNT(*) AS total "
    "FROM ARGUS_SECURITY.authentication_logs_compat_test"
)
rows = result.records()  # For example: [{"total": "1000"}]
request_id = result.request_id
```

The allowlist contains exactly 14 fixed statements: `SELECT 1 AS one`, the
large-transfer verification SELECT, six count SELECTs and six `SELECT *`
previews, restricted to these authoritative compatibility tables:

```text
ARGUS_SECURITY.users_compat_test
ARGUS_SECURITY.resources_compat_test
ARGUS_SECURITY.approved_networks_compat_test
ARGUS_SECURITY.authentication_logs_compat_test
ARGUS_SECURITY.resource_access_logs_compat_test
ARGUS_SECURITY.security_alerts_compat_test
```

Use `COUNT_QUERIES["users"]` or `PREVIEW_QUERIES["resources"]` for catalog
statements. Case, inter-token whitespace and one final semicolon are accepted.
The client then sends its own fixed statement. Comments, arbitrary functions,
additional predicates, CTEs, joins, extra statements, other tables and all write
statements are rejected **before HTTP**. Extending the catalog requires an
intentional code change and tests; this is not a general SQL parser.

These checks protect against accidental misuse of this client; **they are not
a security boundary against malicious Python code that can read the token**.
Use a dedicated least-privilege Zetaris account/API token with only operational
read access. The client does not change or inspect grants. Evaluation ground
truth is not in the catalog and must also remain inaccessible server-side.
No database objects are renamed, recreated or deleted.

## Results and evidence integrity

`QueryResult` retains:

- `headers`: ordered tuple of original field names, or `None` if absent/null.
- `rows`: ordered tuple of row tuples, or `None` if absent/null.
- `total`, `time_used`: original integer metadata values.
- `request_id`, `query_id`, and `limit`: local tracing/request context.
- `raw_response`: the complete parsed object, including unknown metadata.
- `response_bytes`: original successful JSON bytes for exact inspection.

No field names, timestamps, identifiers, encoded locations or list values are
renamed/decoded. Null remains `None`, empty string remains `""`, and an empty
data array becomes `()`. Absent versus explicitly null fields are distinguishable
in `raw_response`. Duplicate headers remain in the positional result, but
`records()` refuses ambiguous names instead of losing a column.

Documented string cells remain strings. Native JSON integers, decimals, booleans
and null are also retained if a deployment supplies these scalar variants:
integers use Python `int`, fractions/exponents use `decimal.Decimal`, and no
binary floating-point conversion occurs. Nested objects/arrays as cells are
rejected. Duplicate JSON object keys, non-standard NaN/Infinity, invalid UTF-8,
malformed JSON, missing required metadata and ragged rows cause `ProtocolError`.
Unknown top-level metadata is retained rather than dropped.

For explicit validation only, `exact_integer(value)` converts integral decimal
strings (including exponent form) or Decimal values to an exact Python integer;
it refuses floats, booleans, fractions and unbounded numeric text. It never
changes `QueryResult`. Any timezone interpretation or semantic column conversion
needs a future, separately tested adapter.

API row limits do not prove completeness. `possibly_truncated` flags results
at the requested limit or with `total` greater than returned rows. Returning
more rows than requested raises `ResultLimitError`; nothing is silently sliced.
The meaning of `total` beyond the observed responses is not assumed. There is
no pagination or automatic refetch in this phase.

Compatibility values are returned encoded. If a caller needs decoding, reuse
`decode_array`, `decode_text` or `decode_row` from `scripts/export_zetaris.py`
as documented in [the export guide](zetaris_exports.md). A reported location is
still a synthetic sensor observation, not proof of physical geography.

## Tests and live verification

Normal automated tests require no Zetaris instance or credentials:

```sh
python -m unittest discover -s tests -v
```

HTTP interactions are mocked. Tests cover configuration, literal env loading,
headers/UUIDs, exact query restrictions, success/empty results, metadata and
precision preservation, HTTP errors, authentication, connection/read failures,
timeouts, malformed responses, bounds, secret-safe tracebacks, redirect refusal,
proxy suppression, and the live checker's validation logic. Existing Phase 1
and exporter tests are unchanged. Mock results are not integration proof.

After configuring `.env.local`, opt into three live SELECTs:

```sh
python scripts/check_zetaris.py
# Optional alternative local file:
python scripts/check_zetaris.py --env-file path/to/local-settings
```

On the current Windows Python installation, portable PowerShell commands are:

```powershell
$argusPython = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe"
& $argusPython -m unittest discover -s tests -v
& $argusPython scripts/check_zetaris.py
```

The checks run sequentially and stop on failure:

```sql
SELECT 1 AS one;

SELECT COUNT(*) AS total
FROM ARGUS_SECURITY.authentication_logs_compat_test;

SELECT event_id, bytes_transferred
FROM ARGUS_SECURITY.resource_access_logs_compat_test
WHERE bytes_transferred IN (2400000000, 950000000);
```

Expected values are `one = 1`, `total = 1000`, and exactly two rows:
ACCESS001994 / 2400000000 and ACCESS002000 / 950000000. Row order is not assumed;
duplicates, wrong identifiers, extra columns and wrong values fail verification.
The checker reports original Python value types and the explicitly checked
integer type, with request UUIDs. It prints fixed verified values only, never
arbitrary server error text or raw response metadata. No dataset files are read
by the checker and no evaluation data is needed.

On 26 October 2026, the complete **72-test** suite passed (40 original tests
plus 32 REST/configuration/checker tests). The installed client then passed all
three SELECTs against the actual configured local Zetaris installation:

- `one` was the Python string `"1"`, explicitly verified as integer 1.
- `total` was the Python string `"1000"`, explicitly verified as integer 1000.
- Both event IDs were Python strings. Their `bytes_transferred` values were
  Python strings representing exactly 2400000000 and 950000000; explicit exact
  conversion produced Python integers with those values, without floating point.

These were direct bearer-authenticated REST calls using this implementation,
after unit tests passed. They establish live compatibility for these three
queries on this instance; they do not verify every allowlisted statement or
replace server-side least-privilege enforcement. No table or dataset was changed.

## Transport safeguards and troubleshooting

Redirects are refused for all destinations. Environment/system HTTP proxies
are disabled so the bearer token is sent only to the configured endpoint;
corporate-proxy access would need a separate reviewed transport. Standard TLS
certificate verification remains enabled. There is no automatic retry, so a
timeout does not silently duplicate query submissions. A timeout limits socket
operations, not a guaranteed total server-side execution deadline; a timed-out
read does not prove that the server cancelled work.

The module emits no logs. Configuration/result reprs exclude credentials and
raw evidence, and diagnostic exception chains suppress underlying server/network
text. Code explicitly handling raw results must still avoid logging sensitive
data; an in-process debugger can inspect credentials in memory. The client does
not attempt to sandbox arbitrary Python or change file permissions.

- `AuthenticationError` (401/403): check the local bearer key, numeric organisation
  ID and server permissions. OpenAPI Basic credentials are separate.
- HTTP 400: check the organisation ID and request/query UUIDs against the local
  API spec. Never paste credentials or raw error pages into a report.
- `ConnectionError` with network access denied: localhost may be blocked by the
  agent execution environment. Retry the same read-only checker with approved
  network access if available, or run the PowerShell command yourself. Do not
  infer that Zetaris is down, change Docker networking, or expose it publicly.
- Other connection failures: check the configured local service and port using
  your normal local tools. Remote instances require their configured HTTPS host.
- `RequestTimeout`: inspect the generated request UUID server-side; choose a
  suitable bounded timeout before an explicit retry.
- `ProtocolError`: check that the base points to the direct API, not `/redoc`
  or the UI proxy; review the current OpenAPI response and preserved successful
  bytes when available. No schema guessing or fallback login is attempted.
- `ResultLimitError` or `possibly_truncated`: narrow a future reviewed query or
  choose suitable limits. Never treat a limited preview as the whole evidence.
- Table-not-found errors: preserve the `_compat_test` suffixes. The client uses
  these existing registrations and does not create replacements.

Only the three live-check statements are part of the integration acceptance
check. The additional allowlisted previews/counts, remote HTTPS deployments,
server permission enforcement, pagination and larger workloads need their own
live validation before being relied upon.

Phase 3A additionally exposes `execute_evidence_query` for validated, closed
lookup/history templates. It shares this transport and its safeguards without
expanding the SQL-string allowlist. See [evidence collection](evidence_collection.md)
for scope bounds, timestamp provenance, completeness and evidence-bundle schema.
