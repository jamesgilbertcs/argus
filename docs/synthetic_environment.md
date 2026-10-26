# Meridian Analytics synthetic security environment

ARGUS aims to find order in digital chaos through transparent, evidence-backed
security investigation. This phase supplies the evidence environment, without
implementing the investigator. Meridian Analytics Ltd is a fictional UK data
analytics company headquartered in London, with approximately 80 employees and
hybrid and remote working. Only 25 invented employees are represented here.
Names were authored for this fixture and were not sourced from employee records
or public personal data; any resemblance to an actual person is coincidental.

The represented departments are Engineering (7 accounts), Finance (5), Sales (6),
Human Resources (4), and IT / Security (3). Roles, locations and working hours
provide context for assessing responsibility and behaviour. Shared applications,
cloud-hosted databases, a source repository, office egress and VPN infrastructure
connect the records. Department permissions are explicit in the resource catalog.

## Generate and test

Python 3.10 or later and its standard library are sufficient. Run from the repository root:

```sh
python scripts/generate_data.py
python -m unittest discover -s tests -v
```

Use `py` instead of `python` on Windows if that is how your interpreter is installed.
No package installation, credentials, external service, or network access is needed.
The generator uses a private `random.Random` instance with default seed **2026**.
It prints counts and validates both the in-memory records and the parsed files.
An invalid dataset raises an exception and causes a nonzero exit.

```sh
python scripts/generate_data.py --seed 42 --output-dir scratch/seed42
```

`--output-dir` is a destination root: the generator creates `data/` and `evaluation/`
under it. By default the root is the repository directory, regardless of the shell's
current directory. Generation overwrites these seven generated files. Other files
are left untouched. There is no configurable time period or record count in this
initial CLI; changing those requires coordinated changes to code and tests.

Identical seed and generator version produce identical CSV and JSON bytes, with
stable field ordering, chronological event ordering, UTF-8 encoding without a BOM,
and LF line endings across platforms. Identifiers have deterministic allocation
order, but are opaque: numerical order does not imply chronological order.
Changing the seed changes background evidence and may change scenario timestamps
by seconds in the password-spray window. Catalogs and scenario intent remain stable.
For durable evaluation results, record the seed, Python version and repository
revision. Cross-version stability of Python's random sampling algorithms is not promised.

The test suite checks the committed default-seed fixture, scenario evidence,
referential and temporal integrity, required fields, categories, JSON cells,
documentation-only addresses, and failure/denial semantics. It also generates two
temporary datasets and compares SHA-256 hashes of all seven files, compares these
to the reference files, exercises another seed, checks CLI execution and tests that
validation rejects corrupt session context. Restore the default fixture with
`python scripts/generate_data.py` before testing if you regenerated it with another seed.
Temporary generation used by the tests does not overwrite the reference files.

## Simulation period and conventions

The period is **2026-09-07T00:00:00Z inclusive through 2026-09-21T00:00:00Z
exclusive**, covering 14 consecutive calendar days (7–20 September). Every event
timestamp is canonical UTC ISO 8601 to the second: `YYYY-MM-DDTHH:MM:SSZ`.
`usual_work_start` and `usual_work_end` are also **UTC**, formatted `HH:MM`;
they are behavioural context, not enforced access controls. No local-time or DST
conversion is needed for comparison with event timestamps. Most sign-ins occur
on weekdays, with a morning peak, later sessions and a small amount of weekend
engineering and IT activity. Resource operations occur 30–5,400 seconds after
ordinary successful sign-in. Scenario accesses use explicit relative times.

All IPs come from the reserved documentation ranges `192.0.2.0/24`,
`198.51.100.0/24`, or `203.0.113.0/24`. They have **no real geolocation**.
Locations are synthetic sensor reports that describe apparent network egress,
not proof of a person's physical whereabouts. Home egresses are stable per account;
office and domestic VPN egresses are shared. An international VPN provides legitimate
internationally reported activity. The approved catalog also includes EU VPN and
cloud endpoints and a retired VPN, some of which need not appear in these logs.
Approval uses exact string matching, with `status = ACTIVE` required. No CIDR lookup
is performed. An approved IP is context, not a determination that the user is safe.

CSV uses a header row, comma separators and standard CSV quoting. Empty cells
represent missing/not-applicable values. Multi-value cells contain non-empty JSON
arrays of strings, not comma-split text or Python literals. After parsing CSV,
parse those individual cells with `json.loads`. For example,
`["Finance","Human Resources"]` is a department array; embedded double quotes are
escaped by the CSV writer. Identifiers are case-sensitive strings, not integers.

## Dataset schemas

`data/users.csv` — 25 rows. `user_id` is the unique stable account key (`U001`–`U025`).
`display_name` is an invented employee name. `department` is one of Engineering,
Finance, Sales, Human Resources, or IT / Security. `role` describes responsibilities.
`employment_status` is `ACTIVE` for every represented employee. `primary_location`
is the employee's nominal UK base. `usual_work_start` and `usual_work_end` are
UTC times with an eight-hour work window, varying across employees.

`data/resources.csv` — 15 rows. `resource_id` is the unique key (`R001`–`R015`).
`resource_name` identifies the application or dataset. `resource_type` is
`APPLICATION`, `DATABASE`, `REPOSITORY`, `INFRASTRUCTURE`, or `STORAGE`.
`owner_department` is the responsible department. `sensitivity` is `PUBLIC`,
`INTERNAL`, `CONFIDENTIAL`, or `RESTRICTED`. `allowed_departments` is a JSON array
of department names with access. Membership allows the simplified resource-level
permissions used by this fixture; it does not establish a business purpose.
Resources include CRM, financial reporting, payroll, HR records, source code,
development infrastructure, document storage, security monitoring and client delivery.

`data/approved_networks.csv` — 10 rows. `network_id` is the unique key.
`ip_address` is the exact egress IP. `network_type` is `OFFICE`, `CORPORATE_VPN`,
or `CLOUD_INFRASTRUCTURE`. `description` gives its purpose. `advertised_location`
is its synthetic apparent location. `status` is `ACTIVE` or `INACTIVE`; a retired
endpoint must not be treated as currently approved merely because it is present.
The US corporate VPN is `203.0.113.10`, advertised as New York.

`data/authentication_logs.csv` — 1,000 rows. `event_id` is a unique `AUTH` identifier.
`timestamp` is the attempt time. `user_id` references users. `source_ip` and
`reported_location` describe observed origin. `device_id` is a synthetic device
identifier. `session_id` is a unique `SES` identifier on success and empty on failure.
`authentication_method` is `PASSWORD`, `PASSWORD_MFA`, or `SSO`. `outcome` is
`SUCCESS` or `FAILURE`. `failure_reason` is empty on success, otherwise
`INVALID_PASSWORD`, `MFA_TIMEOUT`, or `SSO_SESSION_EXPIRED`. Authentication
attempts are distinct from accepted sessions: failed attempts grant no access.

Initial home sign-ins establish a device/source history for each employee.
`DEV-Uxxx-01` denotes the employee's usual device in the generator; a small number
of `-02` secondary devices provide benign changes. `DEV-REMOTE-...` identifiers
are other observed devices. These conventions are generation aids, not trust
assertions: investigators should establish recognition by comparing prior records.

`data/resource_access_logs.csv` — 2,000 rows. `event_id` is a unique `ACCESS`
identifier. `timestamp` is the operation time. `user_id`, `session_id` and
`resource_id` reference an employee, successful authentication session, and resource.
`action` is `READ`, `WRITE`, `EXPORT`, `DELETE`, or `ADMIN`. `outcome` is
`SUCCESS` or `DENIED`. `source_ip` agrees with the session. `bytes_transferred`
is a nonnegative integer number of payload bytes. Denied actions transfer zero;
successful exports transfer positive bytes. Delete has zero payload and admin
operations have small payloads. It is possible for a successful zero-byte delete
and a denied operation both to report zero, so interpret bytes with outcome/action.

Ordinary reads range from about 2 KB to 4 MB, writes from 1 KB to 1.5 MB,
exports from 0.5 MB to 18 MB, and admin payloads from 500 B to 20 KB, skewed
toward smaller transfers. Sensitive scenario exports are 950 MB and 2.4 GB
(decimal units). All successful accesses comply with department membership;
denials represent blocked out-of-department requests, including ordinary mistakes.
Only Engineering/IT generate routine deletes, and IT generates admin activity.

`data/security_alerts.csv` — 20 rows. `alert_id` is a unique `ALT` identifier.
`timestamp` is detection time, never before its cited evidence. `alert_type` is
`IMPOSSIBLE_TRAVEL`, `UNUSUAL_LOGIN`, `MULTIPLE_FAILED_LOGINS`, `PASSWORD_SPRAYING`,
`UNUSUAL_DATA_EXPORT`, or `UNUSUAL_RESOURCE_ACCESS`. `severity` uses `LOW`,
`MEDIUM`, `HIGH` (the schema also accepts `CRITICAL`). `user_id` references a user,
or is empty for a multi-user alert. `source_ip` gives the source associated with
the detection, normally the most recent cited event's source. `description` states
what the monitor observed, not a verdict. `related_event_ids` is a JSON array of
authentication/access event IDs. `status` is `OPEN`, `ACKNOWLEDGED`, or `CLOSED`,
describing alert workflow only. Neither severity nor status establishes maliciousness.

Fourteen routine alerts accompany background activity: seven password retry/recovery
sequences, four denied requests and three device/context changes. Six alerts relate
to the designed investigations. No separate scenario marker is added to the CSVs.

## Relationships and import

Join authentication and resource access to users using `user_id`. Join access
to authentication using `session_id`, filtering authentication to `SUCCESS`.
Every access has the same user and source IP as that successful session, and a
strictly later timestamp. Join access to resource metadata with `resource_id`.
Compare the user's department against the parsed `allowed_departments` array.
Left join observed source IPs to `approved_networks.ip_address`, considering status.
An absent match is an unknown network, not automatically an attacker.

Parse alert `related_event_ids`, then resolve each identifier against the union
of authentication and access events. `AUTH` and `ACCESS` namespaces are disjoint.
Multi-user alerts require following these references rather than filtering by an
empty alert `user_id`. Baseline device/source history is obtained from earlier
authentication rows for the same employee; there is no separate device inventory.

The six CSVs are self-contained import artifacts. Configure a future Zetaris
import with identifiers as strings, bytes as a 64-bit integer (the 2.4 GB export
exceeds signed 32-bit range), and timestamps as UTC. Retain JSON cells as strings
until parsed by the query layer. Treat empty optional cells as null if required
by the destination. No Zetaris connector or automated import is implemented here.

## Designed investigations and evaluation separation

The following describes fixture intent, not information to supply as operational
evidence to ARGUS. Keep this documentation's scenario section, generator source,
tests and the evaluation file outside the investigator's evidence context.

1. **Suspected account compromise — HIGH CONCERN.** On 15 September, a Sales
   account has three password failures and a password-only success from an unfamiliar
   Bucharest source and device. Sensitive CRM/warehouse reads precede a 2.4 GB
   successful warehouse export. Several alerts cite the sequence. In the fixture's
   fictional explanation a password was stolen; telemetry strongly supports
   suspicion without identifying the person operating the account.
2. **Corporate VPN false positive — LOW CONCERN.** On 10 September, a recognised
   Engineering device appears in London and New York six minutes apart. The New York
   IP exactly matches an active corporate VPN. MFA and subsequent small, authorised
   engineering reads/writes support the benign explanation. The impossible-travel
   alert remains an automated detection, resolved by correlating the network catalog.
3. **Ambiguous data export — NEEDS FURTHER INVESTIGATION.** On 17 September, a
   Finance analyst uses a familiar home source and device with MFA after normal
   hours, then successfully exports 950 MB from the confidential financial database.
   The employee has permission; the volume/time merit an alert. The business purpose
   is deliberately unknown, even in ground truth. Human confirmation is required.
4. **Password spraying — HIGH CONCERN — NO CONFIRMED COMPROMISE.** On 18 September,
   one unfamiliar Singapore source makes 36 failed password attempts across 12
   employees in under five minutes. The multi-user alert has an empty `user_id`.
   All attempts fail, no sessions exist for the source, and no subsequent resource
   accesses originate there. Attempted attack and account compromise are distinct.

`evaluation/scenario_ground_truth.json` is an array of four objects. Each includes
`scenario_id`, `scenario_name`, `alert_ids`, `actual_cause`, `expected_assessment`,
chronologically ordered `key_evidence_ids`, and `explanation`. Additional
`window_start` and `window_end` fields specify inclusive bounds of the cited
operational sequence, rather than alert delivery time. Every cited alert and event
exists. This file is exclusively for testing/scoring and **must not be imported as
an operational evidence table or provided to the eventual investigator**.

No operational column contains `is_malicious`, `scenario_id`, `ground_truth`, or
`expected_assessment`. Operational evidence contains observations only. Store
evaluation material under separate permissions when connecting future tooling;
directory separation alone does not enforce access control.

## Assumptions and limits

- This is a curated, modest-volume fixture: 3,020 telemetry rows plus 50 catalog
  rows. It does not model all 80 employees or a production event rate.
- Permissions are department/resource level, with no per-action entitlements,
  service accounts, temporary exceptions or administrator bypass. The broad
  client-warehouse entitlement lets a compromised authorised account misuse access
  without introducing an unexplained permissions violation.
- A session lasts only for its observed operations. No expiry, token refresh,
  logout, concurrency or session IP roaming model is supplied. Each successful
  authentication has a new session; sessions are never inferred from failures.
- Authentication methods are observations rather than a complete IAM policy.
  The password-only compromise assumes a legacy path without enforced MFA; no
  claim about bypassing MFA is made. SSO does not imply fresh MFA in this schema.
- No packets, endpoint process telemetry, files, export destinations, travel
  itineraries, approval tickets or real geolocation feeds are available. Missing
  context intentionally limits attribution and leaves the finance export uncertain.
- Alerts are curated monitoring outputs, not the results of implementing detection
  rules. False positives and denied mistakes are deliberate. `CLOSED` status is
  not a labelled verdict, and an approved VPN is not evidence of intent by itself.
- The generator uses ordinary activity followed by modular scenario embedding,
  then sorts event tables. It is reproducible simulation, not sampled production
  telemetry or a statistically validated security benchmark.
- This phase implements no LLM, agent orchestration, Nightshift, NVIDIA, Meterless
  Engines, Zetaris connectivity, or user interface.
