"""Deterministic, standard-library telemetry for Meridian Analytics Ltd.

Operational records deliberately contain no scenario labels. Evaluation answers
are written separately and must not be supplied to an investigator.
"""

from __future__ import annotations

import argparse
import csv
import ipaddress
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_SEED = 2026
START = datetime(2026, 9, 7, tzinfo=timezone.utc)
END = START + timedelta(days=14)  # exclusive
ROOT = Path(__file__).resolve().parents[1]
DEPARTMENTS = ("Engineering", "Finance", "Sales", "Human Resources", "IT / Security")
SCHEMAS = {
    "users": ("user_id", "display_name", "department", "role", "employment_status",
              "primary_location", "usual_work_start", "usual_work_end"),
    "resources": ("resource_id", "resource_name", "resource_type", "owner_department",
                  "sensitivity", "allowed_departments"),
    "approved_networks": ("network_id", "ip_address", "network_type", "description",
                          "advertised_location", "status"),
    "authentication_logs": ("event_id", "timestamp", "user_id", "source_ip",
                            "reported_location", "device_id", "session_id",
                            "authentication_method", "outcome", "failure_reason"),
    "resource_access_logs": ("event_id", "timestamp", "user_id", "session_id",
                             "resource_id", "action", "outcome", "source_ip",
                             "bytes_transferred"),
    "security_alerts": ("alert_id", "timestamp", "alert_type", "severity", "user_id",
                        "source_ip", "description", "related_event_ids", "status"),
}
Row = dict[str, Any]


def utc(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    """Accept the documented canonical UTC representation only."""
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if utc(parsed) != value:
        raise ValueError(f"Non-canonical UTC timestamp: {value}")
    return parsed


def json_cell(values: list[str]) -> str:
    return json.dumps(values, separators=(",", ":"), ensure_ascii=False)


def home_ip(user_id: str) -> str:
    return f"198.51.100.{50 + int(user_id[1:])}"


def usual_device(user_id: str) -> str:
    return f"DEV-{user_id}-01"


def create_users() -> list[Row]:
    # Invented identities; these are not sampled from a directory or public data.
    groups = [
        ("Engineering", [
            ("Ada Fenwick", "Engineering Manager"), ("Ravi Ashcombe", "Platform Engineer"),
            ("Maya Bellcroft", "Data Engineer"), ("Leo Wrenford", "Software Engineer"),
            ("Zara Hatherley", "Software Engineer"), ("Owen Merewood", "Data Engineer"),
            ("Nina Alderwick", "QA Engineer")]),
        ("Finance", [
            ("Clara Eastmere", "Financial Analyst"), ("Idris Thornwell", "Finance Manager"),
            ("Freya Dalesford", "Payroll Specialist"), ("Hugo Penwick", "Accounts Analyst"),
            ("Elena Foxmere", "Financial Controller")]),
        ("Sales", [
            ("Theo Brambleton", "Account Manager"), ("Asha Lindenford", "Sales Manager"),
            ("Felix Norcroft", "Account Executive"), ("Iris Hawkridge", "Sales Analyst"),
            ("Sam Willowden", "Customer Success Manager"), ("Lila Mossfield", "Account Executive")]),
        ("Human Resources", [
            ("Esme Brookvale", "HR Manager"), ("Noah Elmstead", "People Operations Analyst"),
            ("Talia Reedwick", "Recruitment Coordinator"), ("Arun Birchford", "HR Advisor")]),
        ("IT / Security", [
            ("Priya Stonefell", "Security Analyst"), ("Jude Fairbourne", "Systems Administrator"),
            ("Mila Oakridge", "IT Support Engineer")]),
    ]
    users = []
    hours = [("08:00", "16:00"), ("08:30", "16:30"), ("09:00", "17:00"), ("09:30", "17:30")]
    locations = ["London, UK", "Manchester, UK", "Bristol, UK", "Leeds, UK", "Edinburgh, UK"]
    for department, employees in groups:
        for name, role in employees:
            index = len(users)
            start, end = hours[index % len(hours)]
            users.append(dict(zip(SCHEMAS["users"], [
                f"U{index + 1:03d}", name, department, role, "ACTIVE",
                locations[index % len(locations)], start, end])))
    return users


def create_resources() -> list[Row]:
    eng, fin, sales, hr, sec = DEPARTMENTS
    entries = [
        ("Customer CRM", "APPLICATION", sales, "CONFIDENTIAL", [sales]),
        ("Financial Reporting Database", "DATABASE", fin, "CONFIDENTIAL", [fin]),
        ("Payroll Database", "DATABASE", fin, "RESTRICTED", [fin, hr]),
        ("HR Employee Records", "DATABASE", hr, "RESTRICTED", [hr]),
        ("Source Code Repository", "REPOSITORY", eng, "CONFIDENTIAL", [eng]),
        ("Development Infrastructure", "INFRASTRUCTURE", eng, "INTERNAL", [eng, sec]),
        ("Internal Document Storage", "STORAGE", sec, "INTERNAL", list(DEPARTMENTS)),
        ("Security Monitoring Dashboard", "APPLICATION", sec, "RESTRICTED", [sec]),
        ("Corporate Intranet", "APPLICATION", sec, "INTERNAL", list(DEPARTMENTS)),
        ("Public Product Documentation", "STORAGE", eng, "PUBLIC", list(DEPARTMENTS)),
        ("Sales Proposal Library", "STORAGE", sales, "CONFIDENTIAL", [sales]),
        ("Client Delivery Warehouse", "DATABASE", eng, "RESTRICTED", [eng, sales, fin]),
        ("Recruitment Portal", "APPLICATION", hr, "CONFIDENTIAL", [hr]),
        ("IT Asset Inventory", "DATABASE", sec, "INTERNAL", [sec]),
        ("Finance Planning Workspace", "APPLICATION", fin, "CONFIDENTIAL", [fin]),
    ]
    return [dict(zip(SCHEMAS["resources"], [f"R{i:03d}", name, kind, owner, sensitivity,
                                           json_cell(allowed)]))
            for i, (name, kind, owner, sensitivity, allowed) in enumerate(entries, 1)]


def create_networks() -> list[Row]:
    entries = [
        ("192.0.2.10", "OFFICE", "London headquarters egress", "London, UK", "ACTIVE"),
        ("192.0.2.11", "OFFICE", "Manchester shared office egress", "Manchester, UK", "ACTIVE"),
        ("198.51.100.10", "CORPORATE_VPN", "UK primary VPN egress", "London, UK", "ACTIVE"),
        ("198.51.100.11", "CORPORATE_VPN", "UK secondary VPN egress", "Manchester, UK", "ACTIVE"),
        ("203.0.113.10", "CORPORATE_VPN", "US regional VPN egress", "New York, US", "ACTIVE"),
        ("203.0.113.11", "CORPORATE_VPN", "EU regional VPN egress", "Frankfurt, DE", "ACTIVE"),
        ("203.0.113.30", "CLOUD_INFRASTRUCTURE", "Application service egress", "London, UK", "ACTIVE"),
        ("203.0.113.31", "CLOUD_INFRASTRUCTURE", "Database service egress", "Dublin, IE", "ACTIVE"),
        ("203.0.113.32", "CLOUD_INFRASTRUCTURE", "Monitoring service egress", "London, UK", "ACTIVE"),
        ("192.0.2.99", "CORPORATE_VPN", "Retired VPN endpoint", "London, UK", "INACTIVE"),
    ]
    return [dict(zip(SCHEMAS["approved_networks"], [f"N{i:03d}", *entry]))
            for i, entry in enumerate(entries, 1)]


@dataclass
class Environment:
    rng: random.Random
    tables: dict[str, list[Row]] = field(default_factory=lambda: {name: [] for name in SCHEMAS})
    scenarios: list[Row] = field(default_factory=list)

    def authenticate(self, when: datetime, user: Row, ip: str, location: str,
                     *, outcome: str = "SUCCESS", reason: str = "", device: str | None = None,
                     method: str = "PASSWORD_MFA") -> Row:
        index = len(self.tables["authentication_logs"]) + 1
        event = dict(zip(SCHEMAS["authentication_logs"], [
            f"AUTH{index:06d}", utc(when), user["user_id"], ip, location,
            device or usual_device(user["user_id"]), f"SES{index:06d}" if outcome == "SUCCESS" else "",
            method, outcome, reason]))
        self.tables["authentication_logs"].append(event)
        return event

    def access(self, session: Row, when: datetime, resource_id: str, action: str,
               size: int, outcome: str = "SUCCESS") -> Row:
        event = dict(zip(SCHEMAS["resource_access_logs"], [
            f"ACCESS{len(self.tables['resource_access_logs']) + 1:06d}", utc(when),
            session["user_id"], session["session_id"], resource_id, action, outcome,
            session["source_ip"], size if outcome == "SUCCESS" else 0]))
        self.tables["resource_access_logs"].append(event)
        return event

    def alert(self, when: datetime, kind: str, severity: str, events: list[Row],
              description: str, *, multi_user: bool = False, status: str = "OPEN") -> Row:
        event = dict(zip(SCHEMAS["security_alerts"], [
            f"ALT{len(self.tables['security_alerts']) + 1:04d}", utc(when), kind, severity,
            "" if multi_user else events[0]["user_id"], events[-1]["source_ip"], description,
            json_cell([row["event_id"] for row in events]), status]))
        self.tables["security_alerts"].append(event)
        return event

    def truth(self, scenario_id: str, name: str, cause: str, assessment: str,
              events: list[Row], alerts: list[Row], explanation: str) -> None:
        ordered = sorted(events, key=lambda row: (row["timestamp"], row["event_id"]))
        self.scenarios.append({
            "scenario_id": scenario_id, "scenario_name": name,
            "alert_ids": [row["alert_id"] for row in alerts], "actual_cause": cause,
            "expected_assessment": assessment,
            "key_evidence_ids": [row["event_id"] for row in ordered],
            "window_start": ordered[0]["timestamp"], "window_end": ordered[-1]["timestamp"],
            "explanation": explanation,
        })


def build_background_authentication(env: Environment) -> None:
    rng = env.rng
    users = env.tables["users"]
    # Every employee has an early, familiar home-device observation.
    for i, user in enumerate(users):
        hour, minute = map(int, user["usual_work_start"].split(":"))
        env.authenticate(START + timedelta(hours=hour, minutes=minute, seconds=rng.randrange(50)),
                         user, home_ip(user["user_id"]), user["primary_location"], method="SSO")
    # Routine password typos and recovery: paired failures followed by MFA success.
    for i in range(7):
        user = users[(i * 3) % len(users)]
        when = START + timedelta(days=[1, 2, 3, 4, 7, 8, 9][i], hours=9, minutes=rng.randrange(15, 50))
        attempts = [env.authenticate(when + timedelta(seconds=25 * j), user,
                                    home_ip(user["user_id"]), user["primary_location"],
                                    outcome="FAILURE", reason="INVALID_PASSWORD", method="PASSWORD")
                    for j in range(2)]
        recovered = env.authenticate(when + timedelta(seconds=75), user,
                                     home_ip(user["user_id"]), user["primary_location"])
        env.alert(when + timedelta(seconds=90), "MULTIPLE_FAILED_LOGINS", "LOW",
                  attempts + [recovered], "Repeated failures followed by an MFA login on the same device.",
                  status="CLOSED")
    weekdays = [day for day in range(14) if (START + timedelta(days=day)).weekday() < 5]
    weights = [1.7 if user["department"] in ("Engineering", "IT / Security") else 1.0 for user in users]
    while len(env.tables["authentication_logs"]) < 957:
        user = rng.choices(users, weights=weights)[0]
        weekend = rng.random() < 0.025 and user["department"] in ("Engineering", "IT / Security")
        day = rng.choice([5, 6, 12, 13] if weekend else weekdays)
        hour, minute = map(int, user["usual_work_start"].split(":"))
        # Concentrate sign-ins near start of work, with additional sessions later.
        offset = int(rng.triangular(-15, 380, 20))
        when = START + timedelta(days=day, hours=hour, minutes=minute + offset,
                                 seconds=rng.randrange(60))
        source = rng.choices(["home", "office", "vpn"], [55, 28, 17])[0]
        ip, location = {
            "home": (home_ip(user["user_id"]), user["primary_location"]),
            "office": ("192.0.2.10", "London, UK"),
            "vpn": ("198.51.100.10", "London, UK"),
        }[source]
        failure = rng.random() < 0.075
        reason = rng.choices(["INVALID_PASSWORD", "MFA_TIMEOUT", "SSO_SESSION_EXPIRED"], [6, 3, 1])[0] if failure else ""
        method = {"INVALID_PASSWORD": "PASSWORD", "MFA_TIMEOUT": "PASSWORD_MFA",
                  "SSO_SESSION_EXPIRED": "SSO"}.get(reason, rng.choice(["PASSWORD_MFA", "SSO"]))
        # A small number of secondary devices are benign but initially unfamiliar.
        new_device = len(env.tables["authentication_logs"]) < 49 or rng.random() < 0.018
        device = f"DEV-{user['user_id']}-02" if new_device else usual_device(user["user_id"])
        env.authenticate(when, user, ip, location, outcome="FAILURE" if failure else "SUCCESS",
                         reason=reason, device=device, method=method)


def transfer_size(rng: random.Random, action: str) -> int:
    bounds = {"READ": (2_000, 4_000_000), "WRITE": (1_000, 1_500_000),
              "EXPORT": (500_000, 18_000_000), "DELETE": (0, 0), "ADMIN": (500, 20_000)}
    low, high = bounds[action]
    # A skewed distribution gives many small transfers and occasional larger ones.
    return int(low + (high - low) * rng.random() ** 3)


def build_background_access(env: Environment) -> None:
    rng = env.rng
    users = {row["user_id"]: row for row in env.tables["users"]}
    resources = env.tables["resources"]
    sessions = [row for row in env.tables["authentication_logs"] if row["outcome"] == "SUCCESS"]
    for i in range(1991):
        session = rng.choice(sessions)
        user = users[session["user_id"]]
        allowed = [row for row in resources if user["department"] in json.loads(row["allowed_departments"])]
        denied = rng.random() < 0.035 or i == 0
        forbidden = [row for row in resources if row not in allowed]
        resource = rng.choice(forbidden if denied else allowed)
        # Favour a department's own systems over common company applications.
        if not denied and rng.random() < 0.65:
            resource = rng.choice([row for row in allowed if row["owner_department"] == user["department"]])
        actions = ["READ", "WRITE", "EXPORT"]
        weights = [78, 19, 3]
        if user["department"] in ("Engineering", "IT / Security"):
            actions.append("DELETE")
            weights.append(1)
        if user["department"] == "IT / Security":
            actions.append("ADMIN")
            weights.append(5)
        action = rng.choices(actions, weights=weights)[0]
        if i == 0:
            action = "EXPORT"
        when = parse_utc(session["timestamp"]) + timedelta(seconds=rng.randint(30, 90 * 60))
        env.access(session, when, resource["resource_id"], action, transfer_size(rng, action),
                   "DENIED" if denied else "SUCCESS")
    # Seven more routine detections: access denials and newly observed devices.
    denials = [row for row in env.tables["resource_access_logs"] if row["outcome"] == "DENIED"]
    for event in rng.sample(denials, 4):
        env.alert(parse_utc(event["timestamp"]) + timedelta(seconds=40), "UNUSUAL_RESOURCE_ACCESS",
                  "LOW", [event], "Department policy denied a request to a restricted workspace.",
                  status="CLOSED")
    secondary = [row for row in sessions if row["device_id"].endswith("-02")]
    # Ensure any seed still yields enough benign new-device observations.
    candidates = secondary + [row for row in sessions if row not in secondary]
    for event in candidates[:3]:
        env.alert(parse_utc(event["timestamp"]) + timedelta(seconds=35), "UNUSUAL_LOGIN", "MEDIUM",
                  [event], "Device or sign-in context differs from the monitoring baseline.",
                  status="ACKNOWLEDGED")


def embed_account_compromise(env: Environment) -> None:
    user = env.tables["users"][12]
    when = START + timedelta(days=8, hours=1, minutes=12)
    events = [env.authenticate(when + timedelta(minutes=i), user, "203.0.113.201", "Bucharest, RO",
                               outcome="FAILURE", reason="INVALID_PASSWORD", method="PASSWORD",
                               device="DEV-REMOTE-041") for i in range(3)]
    session = env.authenticate(when + timedelta(minutes=4), user, "203.0.113.201", "Bucharest, RO",
                               method="PASSWORD", device="DEV-REMOTE-041")
    events.append(session)
    events.extend([
        env.access(session, when + timedelta(minutes=6), "R001", "READ", 1_400_000),
        env.access(session, when + timedelta(minutes=8), "R012", "READ", 3_200_000),
        env.access(session, when + timedelta(minutes=15), "R012", "EXPORT", 2_400_000_000),
    ])
    alerts = [
        env.alert(when + timedelta(minutes=3), "MULTIPLE_FAILED_LOGINS", "MEDIUM", events[:3],
                  "Three failed password attempts from a source absent from recent user history."),
        env.alert(when + timedelta(minutes=5), "UNUSUAL_LOGIN", "HIGH", [session],
                  "New device and foreign source observed outside the user's usual working hours."),
        env.alert(when + timedelta(minutes=16), "UNUSUAL_DATA_EXPORT", "HIGH", [events[-1]],
                  "2.4 GB export from the client delivery warehouse exceeds the normal volume baseline."),
    ]
    env.truth("S01", "Suspected Account Compromise", "Stolen password used to access and export client data.",
              "HIGH CONCERN", events, alerts,
              "The fictional cause is credential theft. New source and device, repeated failures, password-only "
              "success, sensitive access and a large export support strong suspicion. Telemetry does not identify "
              "the operator. The account's Sales permissions allow access; approval of access is not proof of intent.")


def embed_vpn_false_positive(env: Environment) -> None:
    user = env.tables["users"][0]
    when = START + timedelta(days=3, hours=10, minutes=5)
    first = env.authenticate(when, user, home_ip(user["user_id"]), "London, UK")
    second = env.authenticate(when + timedelta(minutes=6), user, "203.0.113.10", "New York, US")
    events = [first, second]
    for minutes, resource, action, size in [(8, "R005", "READ", 180_000), (12, "R006", "WRITE", 32_000),
                                           (17, "R005", "READ", 72_000), (24, "R007", "READ", 18_000)]:
        events.append(env.access(second, when + timedelta(minutes=minutes), resource, action, size))
    alert = env.alert(when + timedelta(minutes=7), "IMPOSSIBLE_TRAVEL", "HIGH", [first, second],
                      "London and New York sign-ins observed six minutes apart.")
    env.truth("S02", "Corporate VPN False Positive", "Legitimate use of the corporate US VPN egress.",
              "LOW CONCERN", events, [alert],
              "The second IP exactly matches an active CORPORATE_VPN record. Both sessions use the user's "
              "established device and MFA. Engineering activity is small, authorised and routine with no export. "
              "The location is the observed egress location, not the employee's physical position. An approved "
              "network is supporting evidence, not a blanket guarantee of legitimacy.")


def embed_ambiguous_export(env: Environment) -> None:
    user = env.tables["users"][7]
    when = START + timedelta(days=10, hours=22, minutes=42)
    session = env.authenticate(when, user, home_ip(user["user_id"]), user["primary_location"])
    events = [session, env.access(session, when + timedelta(minutes=3), "R002", "READ", 420_000),
              env.access(session, when + timedelta(minutes=9), "R002", "EXPORT", 950_000_000)]
    alert = env.alert(when + timedelta(minutes=10), "UNUSUAL_DATA_EXPORT", "HIGH", [events[-1]],
                      "950 MB financial reporting export observed outside usual working hours.")
    env.truth("S03", "Ambiguous Data Export", "Authorised late-night export; business purpose is unknown.",
              "NEEDS FURTHER INVESTIGATION", events, [alert],
              "A finance employee uses an established home source and device with MFA to export an unusually "
              "large confidential dataset. Department access is permitted. No approval ticket, destination or "
              "business justification is available. Human verification is needed; neither malicious intent nor "
              "a legitimate reporting task is established, including by this evaluation record.")


def embed_password_spray(env: Environment) -> None:
    when = START + timedelta(days=11, hours=4, minutes=12)
    events = []
    for round_index in range(3):
        for i, user in enumerate(env.tables["users"][:12]):
            offset = (round_index * 12 + i) * 8 + env.rng.randrange(4)
            events.append(env.authenticate(when + timedelta(seconds=offset), user,
                                           "203.0.113.240", "Singapore, SG", outcome="FAILURE",
                                           reason="INVALID_PASSWORD", method="PASSWORD", device="DEV-REMOTE-088"))
    alert = env.alert(when + timedelta(minutes=6), "PASSWORD_SPRAYING", "HIGH", events,
                      "One source generated 36 failed password attempts across 12 accounts within five minutes.",
                      multi_user=True)
    env.truth("S04", "Password Spraying", "External password-spraying attempt; all observed attempts failed.",
              "HIGH CONCERN — NO CONFIRMED COMPROMISE", events, [alert],
              "One unfamiliar source targets twelve accounts in repeated rounds. Every attempt fails and "
              "creates no session. No success from that IP exists in the scenario window or elsewhere in this "
              "dataset. These records establish an attempted attack, not successful account compromise.")


def build_environment(seed: int = DEFAULT_SEED) -> Environment:
    env = Environment(random.Random(seed))
    env.tables["users"] = create_users()
    env.tables["resources"] = create_resources()
    env.tables["approved_networks"] = create_networks()
    build_background_authentication(env)
    build_background_access(env)
    for embed in (embed_account_compromise, embed_vpn_false_positive,
                  embed_ambiguous_export, embed_password_spray):
        embed(env)
    for name in ("authentication_logs", "resource_access_logs", "security_alerts"):
        identifier = "alert_id" if name == "security_alerts" else "event_id"
        env.tables[name].sort(key=lambda row: (row["timestamp"], row[identifier]))
    return env


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def string_array(value: str) -> list[str]:
    result = json.loads(value)
    require(isinstance(result, list) and bool(result) and all(isinstance(item, str) and item for item in result),
            "Expected a non-empty JSON string array")
    require(len(result) == len(set(result)), "Duplicate JSON array members")
    return result


def validate_dataset(tables: dict[str, list[Row]], scenarios: list[Row]) -> None:
    """Validate parsed CSVs as well as in-memory rows; raises ValueError on corruption."""
    optional = {"authentication_logs": {"session_id", "failure_reason"}, "security_alerts": {"user_id"}}
    for name, schema in SCHEMAS.items():
        require(bool(tables.get(name)), f"Missing/empty table: {name}")
        for row in tables[name]:
            require(set(row) == set(schema), f"Invalid columns: {name}")
            for key, value in row.items():
                require(value is not None and (str(value).strip() != "" or key in optional.get(name, set())),
                        f"Missing {name}.{key}")

    def index(name: str, key: str) -> dict[str, Row]:
        rows = tables[name]
        result = {row[key]: row for row in rows}
        require(len(result) == len(rows), f"Duplicate {key} in {name}")
        return result

    users = index("users", "user_id")
    resources = index("resources", "resource_id")
    index("approved_networks", "network_id")
    auth = index("authentication_logs", "event_id")
    accesses = index("resource_access_logs", "event_id")
    alerts = index("security_alerts", "alert_id")
    require(not (auth.keys() & accesses.keys()), "Event IDs overlap across tables")
    events = {**auth, **accesses}
    sessions: dict[str, Row] = {}
    for user in users.values():
        require(user["department"] in DEPARTMENTS and user["employment_status"] == "ACTIVE", "Invalid user category")
        start = datetime.strptime(user["usual_work_start"], "%H:%M")
        end = datetime.strptime(user["usual_work_end"], "%H:%M")
        require(start < end, "Invalid working hours")
    for resource in resources.values():
        require(resource["owner_department"] in DEPARTMENTS, "Unknown resource owner")
        require(resource["sensitivity"] in {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"}, "Invalid sensitivity")
        require(resource["resource_type"] in {"APPLICATION", "DATABASE", "REPOSITORY", "INFRASTRUCTURE", "STORAGE"}, "Invalid resource type")
        require(set(string_array(resource["allowed_departments"])) <= set(DEPARTMENTS), "Unknown allowed department")
    for network in tables["approved_networks"]:
        require(network["network_type"] in {"OFFICE", "CORPORATE_VPN", "CLOUD_INFRASTRUCTURE"}, "Invalid network type")
        require(network["status"] in {"ACTIVE", "INACTIVE"}, "Invalid network status")
    ips = [row["ip_address"] for row in tables["approved_networks"]]
    require(len(ips) == len(set(ips)), "Duplicate approved IP")
    ranges = [ipaddress.ip_network(cidr) for cidr in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]
    for name in SCHEMAS:
        for row in tables[name]:
            if "timestamp" in row:
                require(START <= parse_utc(row["timestamp"]) < END, "Timestamp outside simulation")
            ip = row.get("source_ip", row.get("ip_address"))
            if ip:
                require(any(ipaddress.ip_address(ip) in block for block in ranges), "Non-documentation IP")
    for row in auth.values():
        require(row["user_id"] in users, "Unknown authentication user")
        require(row["authentication_method"] in {"PASSWORD", "PASSWORD_MFA", "SSO"}, "Invalid authentication method")
        require(row["outcome"] in {"SUCCESS", "FAILURE"}, "Invalid authentication outcome")
        if row["outcome"] == "SUCCESS":
            require(bool(row["session_id"]) and not row["failure_reason"], "Invalid successful authentication")
            require(row["session_id"] not in sessions, "Duplicate successful session")
            sessions[row["session_id"]] = row
        else:
            require(not row["session_id"] and row["failure_reason"] in {
                "INVALID_PASSWORD", "MFA_TIMEOUT", "SSO_SESSION_EXPIRED"}, "Invalid failed authentication")
    for row in accesses.values():
        require(row["user_id"] in users and row["resource_id"] in resources, "Unknown access user/resource")
        require(row["session_id"] in sessions, "Access references an invalid session")
        session = sessions[row["session_id"]]
        require(row["user_id"] == session["user_id"] and row["source_ip"] == session["source_ip"], "Session context mismatch")
        require(parse_utc(row["timestamp"]) > parse_utc(session["timestamp"]), "Access precedes authentication")
        require(row["action"] in {"READ", "WRITE", "EXPORT", "DELETE", "ADMIN"}, "Invalid access action")
        require(row["outcome"] in {"SUCCESS", "DENIED"}, "Invalid access outcome")
        size = str(row["bytes_transferred"])
        require(size.isascii() and size.isdecimal(), "Bytes must be a nonnegative integer")
        require(row["outcome"] != "DENIED" or int(size) == 0, "Denied access transferred data")
        if row["outcome"] == "SUCCESS":
            require(users[row["user_id"]]["department"] in string_array(resources[row["resource_id"]]["allowed_departments"]),
                    "Successful access violates department policy")
    for row in alerts.values():
        require(not row["user_id"] or row["user_id"] in users, "Unknown alert user")
        require(row["severity"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}, "Invalid alert severity")
        require(row["status"] in {"OPEN", "ACKNOWLEDGED", "CLOSED"}, "Invalid alert status")
        require(row["alert_type"] in {"IMPOSSIBLE_TRAVEL", "UNUSUAL_LOGIN", "MULTIPLE_FAILED_LOGINS",
                                      "PASSWORD_SPRAYING", "UNUSUAL_DATA_EXPORT", "UNUSUAL_RESOURCE_ACCESS"}, "Invalid alert type")
        for event_id in string_array(row["related_event_ids"]):
            require(event_id in events, "Unknown alert evidence")
            event = events[event_id]
            require(parse_utc(event["timestamp"]) <= parse_utc(row["timestamp"]), "Alert precedes evidence")
            require(not row["user_id"] or row["user_id"] == event["user_id"], "Alert user mismatch")
    require(len({row["scenario_id"] for row in scenarios}) == len(scenarios) == 4, "Expected four unique scenarios")
    for scenario in scenarios:
        required = {"scenario_id", "scenario_name", "alert_ids", "actual_cause", "expected_assessment",
                    "key_evidence_ids", "explanation", "window_start", "window_end"}
        require(set(scenario) == required and all(scenario.values()), "Incomplete ground truth")
        require(set(scenario["alert_ids"]) <= alerts.keys(), "Unknown ground-truth alert")
        require(set(scenario["key_evidence_ids"]) <= events.keys(), "Unknown ground-truth evidence")
        evidence = [events[key] for key in scenario["key_evidence_ids"]]
        times = [parse_utc(row["timestamp"]) for row in evidence]
        require(times == sorted(times), "Scenario evidence is not chronological")
        require(parse_utc(scenario["window_start"]) == min(times) and
                parse_utc(scenario["window_end"]) == max(times), "Invalid scenario window")
        related = {event_id for key in scenario["alert_ids"] for event_id in string_array(alerts[key]["related_event_ids"])}
        require(related <= set(scenario["key_evidence_ids"]), "Scenario alert evidence missing from ground truth")


def read_output(output_dir: Path) -> tuple[dict[str, list[Row]], list[Row]]:
    tables = {}
    for name, schema in SCHEMAS.items():
        with (output_dir / "data" / f"{name}.csv").open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            require(reader.fieldnames == list(schema), f"Invalid CSV header: {name}")
            tables[name] = list(reader)
    with (output_dir / "evaluation" / "scenario_ground_truth.json").open(encoding="utf-8") as handle:
        scenarios = json.load(handle)
    return tables, scenarios


def generate(output_dir: Path = ROOT, seed: int = DEFAULT_SEED) -> dict[str, int]:
    env = build_environment(seed)
    validate_dataset(env.tables, env.scenarios)
    (output_dir / "data").mkdir(parents=True, exist_ok=True)
    (output_dir / "evaluation").mkdir(parents=True, exist_ok=True)
    for name, schema in SCHEMAS.items():
        with (output_dir / "data" / f"{name}.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=schema, lineterminator="\n")
            writer.writeheader()
            writer.writerows(env.tables[name])
    with (output_dir / "evaluation" / "scenario_ground_truth.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(env.scenarios, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    validate_dataset(*read_output(output_dir))
    return {name: len(rows) for name, rows in env.tables.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Deterministic random seed (default: 2026)")
    parser.add_argument("--output-dir", type=Path, default=ROOT, help="Root containing data/ and evaluation/")
    args = parser.parse_args()
    counts = generate(args.output_dir, args.seed)
    print(f"Generated Meridian Analytics telemetry: seed={args.seed}; {utc(START)} <= time < {utc(END)}")
    print(", ".join(f"{name}={count}" for name, count in counts.items()))
    print(f"Validated CSVs and 4 evaluation scenarios in {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
