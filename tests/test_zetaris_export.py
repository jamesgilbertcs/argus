"""Local compatibility/round-trip checks; these do not execute Zetaris queries."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.export_zetaris import (
    ARRAY_FIELDS, DEFAULT_INPUT_DIR, TEXT_FIELDS, ExportError, decode_array,
    decode_row, decode_text, encode_array, encode_text, export_datasets,
    read_canonical_csv,
)
from scripts.generate_data import ROOT, SCHEMAS, read_output, validate_dataset


def hashes(directory: Path) -> dict[str, str]:
    return {f"{table}.csv": hashlib.sha256((directory / f"{table}.csv").read_bytes()).hexdigest()
            for table in SCHEMAS}


def write_fixture(directory: Path, table: str, rows: list[dict[str, str]]) -> None:
    with (directory / f"{table}.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SCHEMAS[table], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class CodecTests(unittest.TestCase):
    def test_observed_department_regressions(self) -> None:
        for canonical, encoded, expected in [
            ('["Finance", "IT / Security"]', "|Finance|IT / Security|", ["Finance", "IT / Security"]),
            ('["Sales"]', "|Sales|", ["Sales"]),
        ]:
            with self.subTest(canonical=canonical):
                self.assertEqual(encode_array(canonical), encoded)
                self.assertEqual(decode_array(encoded), expected)

    def test_missing_empty_list_and_empty_elements_are_distinct(self) -> None:
        cases = [("", "", None), ("[]", "|", []), ('[""]', "||", [""]),
                 ('["", ""]', "|||", ["", ""])]
        for canonical, encoded, expected in cases:
            self.assertEqual(encode_array(canonical), encoded)
            self.assertEqual(decode_array(encoded), expected)
        self.assertEqual(len({encoded for _, encoded, _ in cases}), len(cases))

    def test_collisions_unicode_control_characters_order_and_duplicates(self) -> None:
        values = ["Finance", "IT / Security", "Sales|Engineering", "comma,here", 'a"quote',
                  "100%", "%2C", "", "[]", "Sales", "Sales", "a\nb\rc\td\x00", "\\n", "café",
                  "雪", "a+b", "\u0085", "\u2028", "\u2029", " leading and trailing "]
        encoded = encode_array(json.dumps(values))
        self.assertEqual(decode_array(encoded), values)
        self.assertIn("Sales%7CEngineering", encoded)
        self.assertIn("%252C", encoded)
        self.assertEqual(encoded.count("|"), len(values) + 1)
        self.assertFalse(any(character in encoded for character in ',"\r\n\t\x00\\'))

    def test_scalar_text_round_trips_and_safe_text_is_unchanged(self) -> None:
        for value in ["London, UK", 'A "quoted" comment\nsecond line', "100%|ready\\ok", "%2C", ""]:
            self.assertEqual(decode_text(encode_text(value)), value)
        self.assertEqual(encode_text("London, UK"), "London%2C UK")
        for safe in ["Finance", "IT / Security", "café", "a+b", "apostrophe's", "ordinary text"]:
            self.assertEqual(encode_text(safe), safe)

    def test_rejects_malformed_arrays_and_non_string_elements(self) -> None:
        for invalid in ['["Sales"', "not json", "null", '{}', '"Sales"', '[12]', '[null]', '[true]', '[["Sales"]]']:
            with self.subTest(invalid=invalid), self.assertRaises(ExportError):
                encode_array(invalid)

    def test_rejects_malformed_encoded_values(self) -> None:
        for invalid in ["Sales", "|Sales", "Sales|", "|bad%|", "|%FF|", '|a,b|', '|a"b|']:
            with self.subTest(invalid=invalid), self.assertRaises(ExportError):
                decode_array(invalid)
        for invalid in ["%", "%2", "%ZZ", "%FF", "%2c", "%41", "a,b", 'a"b', "x|y"]:
            with self.subTest(invalid=invalid), self.assertRaises(ExportError):
                decode_text(invalid)


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.output = Path(cls.temporary.name) / "exports"
        cls.canonical, cls.truth = read_output(ROOT)
        cls.before = hashes(DEFAULT_INPUT_DIR)
        cls.truth_before = (ROOT / "evaluation" / "scenario_ground_truth.json").read_bytes()
        cls.counts = export_datasets(output_dir=cls.output)
        cls.exported = {table: read_canonical_csv(cls.output / f"{table}.csv", schema)
                        for table, schema in SCHEMAS.items()}

    def test_six_files_headers_counts_and_order(self) -> None:
        self.assertEqual({path.name for path in self.output.iterdir()}, {f"{table}.csv" for table in SCHEMAS})
        expected_counts = dict(users=25, resources=15, approved_networks=10,
                               authentication_logs=1000, resource_access_logs=2000, security_alerts=20)
        self.assertEqual(self.counts, expected_counts)
        for table, schema in SCHEMAS.items():
            self.assertEqual(len(self.canonical[table]), len(self.exported[table]))
            with (self.output / f"{table}.csv").open(encoding="utf-8", newline="") as handle:
                self.assertEqual(next(csv.reader(handle)), list(schema))
            primary_key = schema[0]
            self.assertEqual([row[primary_key] for row in self.canonical[table]],
                             [row[primary_key] for row in self.exported[table]])

    def test_every_field_is_reversible_and_other_scalars_are_identical(self) -> None:
        for table, original_rows in self.canonical.items():
            for original, exported in zip(original_rows, self.exported[table]):
                restored = decode_row(table, exported)
                for column, value in original.items():
                    if column in ARRAY_FIELDS.get(table, ()):
                        self.assertEqual(decode_array(exported[column]), json.loads(value))
                        self.assertEqual(json.loads(restored[column]), json.loads(value))
                    else:
                        self.assertEqual(restored[column], value)
                        if column not in TEXT_FIELDS.get(table, ()) or not any(character in value for character in ',"%|\\\n\r\t'):
                            self.assertEqual(exported[column], value)

    def test_unchanged_relationships_and_phase_one_validator(self) -> None:
        # Decode only representation changes; the original integrity validator
        # verifies the same evidence and ground truth without exporting answers.
        restored = {table: [decode_row(table, row) for row in rows] for table, rows in self.exported.items()}
        validate_dataset(restored, self.truth)
        sessions = {row["session_id"]: row for row in self.exported["authentication_logs"] if row["outcome"] == "SUCCESS"}
        events = {row["event_id"] for table in ("authentication_logs", "resource_access_logs") for row in self.exported[table]}
        for access in self.exported["resource_access_logs"]:
            auth = sessions[access["session_id"]]
            self.assertEqual(access["user_id"], auth["user_id"])
            self.assertEqual(access["source_ip"], auth["source_ip"])
        for alert in self.exported["security_alerts"]:
            self.assertTrue(set(decode_array(alert["related_event_ids"])) <= events)

    def test_large_integers_timestamps_and_empty_scalar_cells_are_exact(self) -> None:
        for table in SCHEMAS:
            for original, exported in zip(self.canonical[table], self.exported[table]):
                for column in ("timestamp", "bytes_transferred", "session_id", "failure_reason", "user_id"):
                    if column in original:
                        self.assertEqual(exported[column], original[column])
        sizes = [row["bytes_transferred"] for row in self.exported["resource_access_logs"]]
        self.assertIn("2400000000", sizes)
        self.assertIn("950000000", sizes)

    def test_no_quoted_cells_embedded_delimiters_or_multiline_records(self) -> None:
        for table, schema in SCHEMAS.items():
            payload = (self.output / f"{table}.csv").read_bytes()
            self.assertFalse(payload.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\r", payload)
            self.assertNotIn(b'"', payload)
            self.assertTrue(payload.endswith(b"\n"))
            # Deliberately emulate a naive reader only to check export compatibility.
            # Canonical inputs are always parsed with Python's csv module.
            lines = payload.decode("utf-8").splitlines()
            self.assertEqual(len(lines), len(self.canonical[table]) + 1)
            for line in lines:
                self.assertEqual(len(line.split(",")), len(schema))
            for row in self.exported[table]:
                for column in ARRAY_FIELDS.get(table, ()):
                    self.assertNotIn(",", row[column])
                    self.assertNotIn('"', row[column])

    def test_repeat_exports_have_identical_bytes_and_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            second = Path(temporary) / "exports"
            export_datasets(output_dir=second)
            self.assertEqual(hashes(self.output), hashes(second))
            for table in SCHEMAS:
                self.assertEqual((self.output / f"{table}.csv").read_bytes(), (second / f"{table}.csv").read_bytes())
            export_datasets(output_dir=second)
            self.assertEqual(hashes(self.output), hashes(second))
            self.assertEqual(hashes(self.output), hashes(DEFAULT_INPUT_DIR / "zetaris"))

    def test_canonical_data_and_ground_truth_are_unchanged(self) -> None:
        self.assertEqual(hashes(DEFAULT_INPUT_DIR), self.before)
        self.assertEqual((ROOT / "evaluation" / "scenario_ground_truth.json").read_bytes(), self.truth_before)
        forbidden = {"is_malicious", "scenario_id", "ground_truth", "expected_assessment"}
        for schema in SCHEMAS.values():
            self.assertFalse(forbidden & set(schema))
        self.assertFalse((self.output / "evaluation").exists())
        self.assertFalse((self.output / "scenario_ground_truth.json").exists())

    def test_cli_accepts_custom_directories_without_evaluation_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            for table in SCHEMAS:
                shutil.copyfile(DEFAULT_INPUT_DIR / f"{table}.csv", source / f"{table}.csv")
            (source / "scenario_ground_truth.json").write_text("DO NOT IMPORT", encoding="utf-8")
            (source / "test.csv").write_text("unrelated,experiment\n", encoding="utf-8")
            target = Path(temporary) / "target"
            result = subprocess.run([sys.executable, str(ROOT / "scripts" / "export_zetaris.py"),
                                     "--input-dir", str(source), "--output-dir", str(target)],
                                    capture_output=True, text=True, check=True, cwd=temporary)
            self.assertIn("authentication_logs.csv: rows=1000", result.stdout)
            self.assertIn("Manual Zetaris ingestion verification is still required", result.stdout)
            self.assertEqual(hashes(target), hashes(self.output))
            self.assertEqual(len(list(target.iterdir())), 6)


class ExportFailureAndFutureContentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / "source"
        self.source.mkdir()
        for table in SCHEMAS:
            shutil.copyfile(DEFAULT_INPUT_DIR / f"{table}.csv", self.source / f"{table}.csv")
        self.output = self.base / "exports"

    def test_missing_input_fails_before_any_outputs_are_created(self) -> None:
        (self.source / "security_alerts.csv").unlink()
        with self.assertRaisesRegex(ExportError, "security_alerts.csv"):
            export_datasets(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_bad_header_ragged_rows_broken_quoting_and_encoding_fail(self) -> None:
        path = self.source / "users.csv"
        header = ",".join(SCHEMAS["users"]) + "\n"
        for payload in [b"wrong,header\n", header.encode() + b"U001\n", header.encode() + b'"unterminated\n',
                        header.encode() + b"\xff\n", header.encode() + b"\n"]:
            with self.subTest(payload=payload):
                path.write_bytes(payload)
                with self.assertRaisesRegex(ExportError, "users.csv"):
                    export_datasets(self.source, self.output)
                self.assertFalse(self.output.exists())

    def test_invalid_structured_value_does_not_overwrite_existing_export(self) -> None:
        export_datasets(self.source, self.output)
        before = hashes(self.output)
        rows = read_canonical_csv(self.source / "resources.csv", SCHEMAS["resources"])
        rows[-1]["allowed_departments"] = '["Finance", null]'
        write_fixture(self.source, "resources", rows)
        with self.assertRaisesRegex(ExportError, "resources.csv: record 16"):
            export_datasets(self.source, self.output)
        self.assertEqual(hashes(self.output), before)

    def test_refuses_identical_canonical_output_and_repository_canonical_output(self) -> None:
        for target in [self.source, self.source / ".." / "source", DEFAULT_INPUT_DIR]:
            with self.subTest(target=target):
                before = hashes(target)
                with self.assertRaisesRegex(ExportError, "Refusing to overwrite canonical"):
                    export_datasets(self.source, target)
                self.assertEqual(hashes(target), before)

    def test_refuses_output_hardlink_to_canonical_file(self) -> None:
        self.output.mkdir()
        os.link(self.source / "users.csv", self.output / "users.csv")
        before = hashes(self.source)
        with self.assertRaisesRegex(ExportError, "Refusing to overwrite canonical"):
            export_datasets(self.source, self.output)
        self.assertEqual(hashes(self.source), before)

    def test_refuses_mixed_output_directory_including_evaluation_files(self) -> None:
        self.output.mkdir()
        marker = self.output / "scenario_ground_truth.json"
        marker.write_text("do not change", encoding="utf-8")
        with self.assertRaisesRegex(ExportError, "only the six evidence CSVs"):
            export_datasets(self.source, self.output)
        self.assertEqual(marker.read_text(encoding="utf-8"), "do not change")
        self.assertEqual(len(list(self.output.iterdir())), 1)

    def test_unsafe_identifier_fails_with_field_context_instead_of_changing_it(self) -> None:
        rows = read_canonical_csv(self.source / "users.csv", SCHEMAS["users"])
        rows[0]["user_id"] = "U,001"
        write_fixture(self.source, "users", rows)
        with self.assertRaisesRegex(ExportError, "unchanged scalar user_id"):
            export_datasets(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_future_narrative_text_is_escaped_and_completely_reversible(self) -> None:
        rows = read_canonical_csv(self.source / "security_alerts.csv", SCHEMAS["security_alerts"])
        text = 'New, "unfamiliar" source|device\nreview 100% complete\\pending\r\t雪'
        rows[0]["description"] = text
        rows[0]["related_event_ids"] = json.dumps(["event|one", 'event,"two', "", "100%", "event|one"])
        write_fixture(self.source, "security_alerts", rows)
        export_datasets(self.source, self.output)
        exported = read_canonical_csv(self.output / "security_alerts.csv", SCHEMAS["security_alerts"])
        self.assertEqual(decode_text(exported[0]["description"]), text)
        self.assertEqual(decode_array(exported[0]["related_event_ids"]), json.loads(rows[0]["related_event_ids"]))
        self.assertEqual(len(exported), 20)
        self.assertNotIn(b'"', (self.output / "security_alerts.csv").read_bytes())

    def test_empty_and_missing_arrays_survive_file_export(self) -> None:
        rows = read_canonical_csv(self.source / "resources.csv", SCHEMAS["resources"])
        for row, value in zip(rows, ["[]", "", '[""]']):
            row["allowed_departments"] = value
        write_fixture(self.source, "resources", rows)
        counts = export_datasets(self.source)  # default output is inside the selected input directory
        exported = read_canonical_csv(self.source / "zetaris" / "resources.csv", SCHEMAS["resources"])
        self.assertEqual(counts["resources"], 15)
        self.assertEqual([decode_array(row["allowed_departments"]) for row in exported[:3]], [[], None, [""]])

    def test_cli_failure_has_nonzero_status_and_clear_message(self) -> None:
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "export_zetaris.py"),
                                 "--input-dir", str(self.base / "missing"), "--output-dir", str(self.output)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Export failed:", result.stderr)
        self.assertIn("users.csv", result.stderr)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
