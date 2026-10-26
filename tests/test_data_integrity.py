"""Validate the committed import files and independently inspect all four scenarios.

Run from the repository root: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.generate_data import (
    DEFAULT_SEED, END, ROOT, SCHEMAS, START, generate, parse_utc, read_output,
    validate_dataset,
)


class DataIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tables, cls.truth = read_output(ROOT)
        cls.users = {row["user_id"]: row for row in cls.tables["users"]}
        cls.resources = {row["resource_id"]: row for row in cls.tables["resources"]}
        cls.auth = {row["event_id"]: row for row in cls.tables["authentication_logs"]}
        cls.access = {row["event_id"]: row for row in cls.tables["resource_access_logs"]}
        cls.events = {**cls.auth, **cls.access}
        cls.alerts = {row["alert_id"]: row for row in cls.tables["security_alerts"]}
        cls.sessions = {row["session_id"]: row for row in cls.auth.values() if row["outcome"] == "SUCCESS"}
        cls.scenarios = {row["scenario_id"]: row for row in cls.truth}

    def evidence(self, scenario_id: str) -> list[dict[str, str]]:
        return [self.events[key] for key in self.scenarios[scenario_id]["key_evidence_ids"]]

    def familiar_history(self, event: dict[str, str]) -> list[dict[str, str]]:
        return [row for row in self.auth.values()
                if row["user_id"] == event["user_id"] and row["timestamp"] < event["timestamp"]
                and row["outcome"] == "SUCCESS"]

    def test_csv_counts_headers_required_fields_and_no_answer_columns(self) -> None:
        expected = dict(users=25, resources=15, approved_networks=10,
                        authentication_logs=1000, resource_access_logs=2000, security_alerts=20)
        self.assertEqual({key: len(rows) for key, rows in self.tables.items()}, expected)
        forbidden = {"is_malicious", "scenario_id", "ground_truth", "expected_assessment"}
        optional = {"authentication_logs": {"session_id", "failure_reason"}, "security_alerts": {"user_id"}}
        for name, rows in self.tables.items():
            for row in rows:
                self.assertEqual(set(row), set(SCHEMAS[name]))
                self.assertFalse(forbidden.intersection(row))
                for key, value in row.items():
                    self.assertIsInstance(value, str)
                    if key not in optional.get(name, set()):
                        self.assertTrue(value.strip(), (name, key))

    def test_all_identifiers_are_unique(self) -> None:
        for name, key in [("users", "user_id"), ("resources", "resource_id"),
                          ("approved_networks", "network_id"), ("security_alerts", "alert_id")]:
            self.assertEqual(len(self.tables[name]), len({row[key] for row in self.tables[name]}))
        ids = [row["event_id"] for name in ("authentication_logs", "resource_access_logs") for row in self.tables[name]]
        self.assertEqual(len(ids), len(set(ids)))
        successes = [row for row in self.auth.values() if row["outcome"] == "SUCCESS"]
        self.assertEqual(len(successes), len(self.sessions))
        self.assertNotIn("", self.sessions)

    def test_authentication_sessions_and_referential_integrity(self) -> None:
        for event in self.auth.values():
            self.assertIn(event["user_id"], self.users)
            if event["outcome"] == "SUCCESS":
                self.assertTrue(event["session_id"])
                self.assertEqual(event["failure_reason"], "")
            else:
                self.assertEqual(event["session_id"], "")
                self.assertTrue(event["failure_reason"])
        for event in self.access.values():
            self.assertIn(event["user_id"], self.users)
            self.assertIn(event["resource_id"], self.resources)
            self.assertIn(event["session_id"], self.sessions)
            auth = self.sessions[event["session_id"]]
            self.assertEqual(event["user_id"], auth["user_id"])
            self.assertEqual(event["source_ip"], auth["source_ip"])
            self.assertGreater(parse_utc(event["timestamp"]), parse_utc(auth["timestamp"]))

    def test_alert_and_evaluation_references(self) -> None:
        for alert in self.alerts.values():
            if alert["user_id"]:
                self.assertIn(alert["user_id"], self.users)
            related = json.loads(alert["related_event_ids"])
            self.assertIsInstance(related, list)
            self.assertTrue(related)
            for event_id in related:
                self.assertIn(event_id, self.events)
                event = self.events[event_id]
                self.assertLessEqual(event["timestamp"], alert["timestamp"])
                if alert["user_id"]:
                    self.assertEqual(event["user_id"], alert["user_id"])
        self.assertEqual(set(self.scenarios), {"S01", "S02", "S03", "S04"})
        for scenario in self.truth:
            self.assertTrue(scenario["actual_cause"])
            self.assertTrue(scenario["explanation"])
            self.assertTrue(scenario["alert_ids"])
            for alert_id in scenario["alert_ids"]:
                self.assertIn(alert_id, self.alerts)
            for event_id in scenario["key_evidence_ids"]:
                self.assertIn(event_id, self.events)

    def test_timestamps_and_documented_period(self) -> None:
        # Independent of generator constants: catch accidental period changes.
        self.assertEqual(START, datetime(2026, 9, 7, tzinfo=timezone.utc))
        self.assertEqual(END - START, timedelta(days=14))
        for name in ("authentication_logs", "resource_access_logs", "security_alerts"):
            timestamps = []
            for row in self.tables[name]:
                self.assertRegex(row["timestamp"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
                parsed = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                self.assertEqual(parsed.utcoffset(), timedelta(0))
                self.assertLessEqual(START, parsed)
                self.assertLess(parsed, END)
                timestamps.append(parsed)
            self.assertEqual(timestamps, sorted(timestamps))
        for scenario in self.truth:
            times = [self.events[key]["timestamp"] for key in scenario["key_evidence_ids"]]
            self.assertEqual(times, sorted(times))
            self.assertEqual(times[0], scenario["window_start"])
            self.assertEqual(times[-1], scenario["window_end"])

    def test_categories_and_documentation_ip_ranges(self) -> None:
        categories = {
            "users": {"department": {"Engineering", "Finance", "Sales", "Human Resources", "IT / Security"},
                      "employment_status": {"ACTIVE"}},
            "resources": {"sensitivity": {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"},
                          "resource_type": {"APPLICATION", "DATABASE", "REPOSITORY", "INFRASTRUCTURE", "STORAGE"}},
            "approved_networks": {"network_type": {"OFFICE", "CORPORATE_VPN", "CLOUD_INFRASTRUCTURE"},
                                  "status": {"ACTIVE", "INACTIVE"}},
            "authentication_logs": {"outcome": {"SUCCESS", "FAILURE"},
                                    "authentication_method": {"PASSWORD", "PASSWORD_MFA", "SSO"},
                                    "failure_reason": {"", "INVALID_PASSWORD", "MFA_TIMEOUT", "SSO_SESSION_EXPIRED"}},
            "resource_access_logs": {"action": {"READ", "WRITE", "EXPORT", "DELETE", "ADMIN"},
                                     "outcome": {"SUCCESS", "DENIED"}},
            "security_alerts": {"severity": {"LOW", "MEDIUM", "HIGH", "CRITICAL"},
                                "status": {"OPEN", "ACKNOWLEDGED", "CLOSED"},
                                "alert_type": {"IMPOSSIBLE_TRAVEL", "UNUSUAL_LOGIN", "MULTIPLE_FAILED_LOGINS",
                                               "PASSWORD_SPRAYING", "UNUSUAL_DATA_EXPORT", "UNUSUAL_RESOURCE_ACCESS"}},
        }
        ranges = [ipaddress.ip_network(value) for value in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]
        for name, rows in self.tables.items():
            for row in rows:
                for key, valid in categories[name].items():
                    self.assertIn(row[key], valid)
                ip = row.get("source_ip", row.get("ip_address"))
                if ip:
                    self.assertTrue(any(ipaddress.ip_address(ip) in network for network in ranges))
        for resource in self.resources.values():
            self.assertTrue(set(json.loads(resource["allowed_departments"])) <= categories["users"]["department"])

    def test_permissions_transfer_sizes_and_background_variation(self) -> None:
        denials = []
        exports = []
        for event in self.access.values():
            self.assertRegex(event["bytes_transferred"], r"^[0-9]+$")
            if event["outcome"] == "DENIED":
                self.assertEqual(int(event["bytes_transferred"]), 0)
                denials.append(event)
            else:
                department = self.users[event["user_id"]]["department"]
                self.assertIn(department, json.loads(self.resources[event["resource_id"]]["allowed_departments"]))
                if event["action"] == "EXPORT":
                    self.assertGreater(int(event["bytes_transferred"]), 0)
                    exports.append(event)
        self.assertTrue(any(row["action"] == "EXPORT" for row in denials))
        self.assertTrue(exports)
        self.assertGreater(len({row["bytes_transferred"] for row in self.access.values()}), 500)
        scenario_ids = {key for scenario in self.truth for key in scenario["key_evidence_ids"]}
        background = [row for row in self.auth.values() if row["event_id"] not in scenario_ids]
        self.assertTrue(any(row["outcome"] == "FAILURE" for row in background))
        self.assertGreater(len({row["source_ip"] for row in background}), 20)
        self.assertEqual({row["user_id"] for row in background}, set(self.users))
        self.assertTrue(any(row["device_id"].endswith("-02") for row in background))
        self.assertGreater(len({row["timestamp"][:10] for row in background}), 10)

    def test_account_compromise_evidence(self) -> None:
        evidence = self.evidence("S01")
        logins = [row for row in evidence if row["event_id"] in self.auth]
        failures = [row for row in logins if row["outcome"] == "FAILURE"]
        successes = [row for row in logins if row["outcome"] == "SUCCESS"]
        self.assertGreaterEqual(len(failures), 3)
        self.assertEqual(len(successes), 1)
        login = successes[0]
        self.assertTrue(all(row["timestamp"] < login["timestamp"] for row in failures))
        history = self.familiar_history(logins[0])
        self.assertTrue(history)
        self.assertNotIn(login["source_ip"], {row["source_ip"] for row in history})
        self.assertNotIn(login["device_id"], {row["device_id"] for row in history})
        self.assertNotIn(login["source_ip"], {row["ip_address"] for row in self.tables["approved_networks"]})
        access = [row for row in evidence if row["event_id"] in self.access]
        self.assertTrue(all(row["session_id"] == login["session_id"] for row in access))
        sensitive = [row for row in access if self.resources[row["resource_id"]]["sensitivity"] in {"CONFIDENTIAL", "RESTRICTED"}]
        self.assertGreaterEqual(len(sensitive), 2)
        self.assertTrue(any(row["action"] == "EXPORT" and row["outcome"] == "SUCCESS"
                            and int(row["bytes_transferred"]) > 1_000_000_000 for row in sensitive))
        self.assertEqual(self.scenarios["S01"]["expected_assessment"], "HIGH CONCERN")
        types = {self.alerts[key]["alert_type"] for key in self.scenarios["S01"]["alert_ids"]}
        self.assertTrue({"UNUSUAL_LOGIN", "UNUSUAL_DATA_EXPORT"} <= types)

    def test_vpn_false_positive_evidence(self) -> None:
        evidence = self.evidence("S02")
        first, second = [row for row in evidence if row["event_id"] in self.auth]
        self.assertNotEqual(first["reported_location"], second["reported_location"])
        self.assertLess(parse_utc(second["timestamp"]) - parse_utc(first["timestamp"]), timedelta(minutes=10))
        network = next(row for row in self.tables["approved_networks"] if row["ip_address"] == second["source_ip"])
        self.assertEqual(network["network_type"], "CORPORATE_VPN")
        self.assertEqual(network["status"], "ACTIVE")
        self.assertEqual(network["advertised_location"], second["reported_location"])
        self.assertEqual(first["device_id"], second["device_id"])
        self.assertIn(first["device_id"], {row["device_id"] for row in self.familiar_history(first)})
        # Examine the whole VPN session, not just evidence selected for evaluation.
        access = [row for row in self.access.values() if row["session_id"] == second["session_id"]]
        self.assertTrue(access)
        for row in access:
            self.assertEqual(row["outcome"], "SUCCESS")
            self.assertIn(row["action"], {"READ", "WRITE"})
            self.assertLess(int(row["bytes_transferred"]), 1_000_000)
            self.assertIn(self.users[row["user_id"]]["department"], json.loads(self.resources[row["resource_id"]]["allowed_departments"]))
        self.assertEqual(self.scenarios["S02"]["expected_assessment"], "LOW CONCERN")

    def test_ambiguous_export_evidence(self) -> None:
        evidence = self.evidence("S03")
        login = next(row for row in evidence if row["event_id"] in self.auth)
        export = next(row for row in evidence if row.get("action") == "EXPORT")
        user = self.users[login["user_id"]]
        resource = self.resources[export["resource_id"]]
        self.assertEqual(user["department"], "Finance")
        self.assertIn("Finance", json.loads(resource["allowed_departments"]))
        self.assertEqual(resource["sensitivity"], "CONFIDENTIAL")
        history = self.familiar_history(login)
        self.assertTrue(any(row["source_ip"] == login["source_ip"] and row["device_id"] == login["device_id"] for row in history))
        self.assertGreater(login["timestamp"][11:16], user["usual_work_end"])
        self.assertEqual(login["outcome"], "SUCCESS")
        self.assertEqual(export["session_id"], login["session_id"])
        self.assertEqual(export["outcome"], "SUCCESS")
        scenario_ids = {key for row in self.truth for key in row["key_evidence_ids"]}
        ordinary_exports = [int(row["bytes_transferred"]) for row in self.access.values()
                            if row["action"] == "EXPORT" and row["outcome"] == "SUCCESS" and row["event_id"] not in scenario_ids]
        self.assertGreater(int(export["bytes_transferred"]), 20 * max(ordinary_exports))
        self.assertEqual(self.scenarios["S03"]["expected_assessment"], "NEEDS FURTHER INVESTIGATION")
        self.assertTrue(any(self.alerts[key]["alert_type"] == "UNUSUAL_DATA_EXPORT" for key in self.scenarios["S03"]["alert_ids"]))

    def test_password_spraying_evidence(self) -> None:
        scenario = self.scenarios["S04"]
        evidence = self.evidence("S04")
        self.assertGreaterEqual(len({row["user_id"] for row in evidence}), 10)
        sources = {row["source_ip"] for row in evidence}
        self.assertEqual(len(sources), 1)
        source = next(iter(sources))
        self.assertTrue(all(row["outcome"] == "FAILURE" and not row["session_id"] for row in evidence))
        self.assertLess(parse_utc(scenario["window_end"]) - parse_utc(scenario["window_start"]), timedelta(minutes=6))
        window = [row for row in self.auth.values() if row["source_ip"] == source and
                  scenario["window_start"] <= row["timestamp"] <= scenario["window_end"]]
        self.assertGreaterEqual(len(window), 30)
        self.assertFalse(any(row["outcome"] == "SUCCESS" for row in window))
        self.assertFalse(any(row["source_ip"] == source and row["outcome"] == "SUCCESS" for row in self.auth.values()))
        self.assertTrue(all(row["source_ip"] != source for row in self.access.values()))
        alert = self.alerts[scenario["alert_ids"][0]]
        self.assertEqual(alert["alert_type"], "PASSWORD_SPRAYING")
        self.assertEqual(alert["user_id"], "")
        self.assertEqual(set(json.loads(alert["related_event_ids"])), {row["event_id"] for row in evidence})
        self.assertEqual(scenario["expected_assessment"], "HIGH CONCERN — NO CONFIRMED COMPROMISE")

    def test_generator_validator_accepts_import_files(self) -> None:
        validate_dataset(self.tables, self.truth)

    def test_generator_validator_rejects_corrupted_session_context(self) -> None:
        for field, value in [("session_id", "NONEXISTENT"), ("user_id", "U999"),
                             ("source_ip", "192.0.2.254"), ("timestamp", "2026-09-06T00:00:00Z")]:
            with self.subTest(field=field):
                tables = copy.deepcopy(self.tables)
                tables["resource_access_logs"][0][field] = value
                with self.assertRaises(ValueError):
                    validate_dataset(tables, self.truth)


class ReproducibilityTests(unittest.TestCase):
    @staticmethod
    def hashes(root: Path) -> dict[str, str]:
        paths = [root / "data" / f"{name}.csv" for name in SCHEMAS]
        paths.append(root / "evaluation" / "scenario_ground_truth.json")
        return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}

    def test_same_seed_is_identical_and_matches_committed_data(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            generate(Path(first), DEFAULT_SEED)
            generate(Path(second), DEFAULT_SEED)
            self.assertEqual(self.hashes(Path(first)), self.hashes(Path(second)))
            self.assertEqual(self.hashes(Path(first)), self.hashes(ROOT))

    def test_different_seed_changes_background_and_preserves_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generate(root, DEFAULT_SEED + 1)
            tables, truth = read_output(root)
            validate_dataset(tables, truth)
            self.assertNotEqual(self.hashes(root)["data/authentication_logs.csv"], self.hashes(ROOT)["data/authentication_logs.csv"])
            self.assertEqual(len(tables["authentication_logs"]), 1000)
            self.assertEqual(len(tables["resource_access_logs"]), 2000)

    def test_cli_accepts_seed_and_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_data.py"),
                                     "--seed", str(DEFAULT_SEED), "--output-dir", temporary],
                                    capture_output=True, text=True, check=True)
            self.assertIn("authentication_logs=1000", result.stdout)
            self.assertIn("Validated CSVs and 4 evaluation scenarios", result.stdout)
            self.assertEqual(self.hashes(Path(temporary)), self.hashes(ROOT))


if __name__ == "__main__":
    unittest.main()
