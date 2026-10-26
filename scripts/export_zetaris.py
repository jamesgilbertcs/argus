"""Export the six canonical evidence CSVs without CSV-quoted cells.

JSON string arrays become percent-escaped, pipe-framed lists. Selected narrative
fields use the same reversible text escaping. Other scalar values stay identical.
No generator execution or evaluation data is needed. See docs/zetaris_exports.md.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import unicodedata
from pathlib import Path
from urllib.parse import unquote

if __package__:
    from .generate_data import ROOT, SCHEMAS
else:
    from generate_data import ROOT, SCHEMAS


ARRAY_FIELDS = {
    "resources": frozenset({"allowed_departments"}),
    "security_alerts": frozenset({"related_event_ids"}),
}
# An explicit allowlist: no escaping of identifiers, numbers, classifications,
# timestamps or department names. Safe text in these columns is also unchanged.
TEXT_FIELDS = {
    "users": frozenset({"display_name", "role", "primary_location"}),
    "resources": frozenset({"resource_name"}),
    "approved_networks": frozenset({"description", "advertised_location"}),
    "authentication_logs": frozenset({"reported_location"}),
    "security_alerts": frozenset({"description"}),
}
DEFAULT_INPUT_DIR = ROOT / "data"
Row = dict[str, str]


class ExportError(ValueError):
    """Malformed canonical input, unsupported content or an unsafe destination."""


def needs_escape(character: str) -> bool:
    return character in '%,"|\\' or unicodedata.category(character) in {"Cc", "Zl", "Zp"}


def encode_text(value: str) -> str:
    """Escape only reserved characters as uppercase percent-encoded UTF-8 bytes."""
    return "".join("".join(f"%{byte:02X}" for byte in character.encode("utf-8"))
                   if needs_escape(character) else character for character in value)


def decode_text(value: str) -> str:
    """Decode exactly once, rejecting malformed or non-canonical escape sequences."""
    try:
        decoded = unquote(value, encoding="utf-8", errors="strict")
    except UnicodeError as exc:
        raise ExportError("Invalid UTF-8 percent escape") from exc
    if encode_text(decoded) != value:
        raise ExportError(f"Invalid text encoding: {value!r}")
    return decoded


def encode_array(value: str) -> str:
    """Encode a JSON array of strings; a blank canonical cell means missing."""
    if value == "":
        return ""
    try:
        values = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ExportError(f"Invalid JSON array: {exc.msg}") from exc
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise ExportError("Expected a JSON array containing only strings")
    # One framing pipe represents []; two represent [""]. Missing stays blank.
    return "|" if not values else "|" + "|".join(encode_text(item) for item in values) + "|"


def decode_array(value: str) -> list[str] | None:
    """Decode a framed list, preserving order, duplicates and empty elements."""
    if value == "":
        return None
    if value == "|":
        return []
    if not value.startswith("|") or not value.endswith("|"):
        raise ExportError("Array must start and end with a framing pipe")
    return [decode_text(item) for item in value[1:-1].split("|")]


def encode_row(table: str, row: Row) -> Row:
    result = {}
    for column, value in row.items():
        if column in ARRAY_FIELDS.get(table, ()):
            result[column] = encode_array(value)
        elif column in TEXT_FIELDS.get(table, ()):
            result[column] = encode_text(value)
        else:
            if any(needs_escape(character) for character in value):
                raise ExportError(f"Unsupported reserved character in unchanged scalar {column}")
            result[column] = value
    return result


def decode_row(table: str, row: Row) -> Row:
    """Restore canonical field values; JSON array whitespace is normalised."""
    result = dict(row)
    for column in ARRAY_FIELDS.get(table, ()):
        values = decode_array(row[column])
        result[column] = "" if values is None else json.dumps(values, separators=(",", ":"), ensure_ascii=False)
    for column in TEXT_FIELDS.get(table, ()):
        result[column] = decode_text(row[column])
    return result


def read_canonical_csv(path: Path, schema: tuple[str, ...]) -> list[Row]:
    """Strict CSV parsing: reject wrong headers, ragged rows and broken quoting."""
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle, strict=True)
            if next(reader, None) != list(schema):
                raise ExportError(f"{path}: missing or incorrect header; expected {','.join(schema)}")
            rows = []
            for fields in reader:
                if len(fields) != len(schema):
                    raise ExportError(f"{path}: line {reader.line_num}: expected {len(schema)} cells, got {len(fields)}")
                rows.append(dict(zip(schema, fields)))
            return rows
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ExportError(f"Cannot read {path}: {exc}") from exc


def build_export(table: str, rows: list[Row]) -> bytes:
    buffer = io.StringIO(newline="")
    # No fallback quoting or escaping: an overlooked comma/quote must fail.
    writer = csv.DictWriter(buffer, fieldnames=SCHEMAS[table], quoting=csv.QUOTE_NONE,
                            escapechar=None, lineterminator="\n")
    writer.writeheader()
    for number, row in enumerate(rows, 2):
        try:
            encoded = encode_row(table, row)
            writer.writerow(encoded)
            decoded = decode_row(table, encoded)
            for column, original in row.items():
                if column in ARRAY_FIELDS.get(table, ()):
                    expected = None if original == "" else json.loads(original)
                    if decode_array(encoded[column]) != expected:
                        raise ExportError(f"Array round trip failed: {column}")
                elif decoded[column] != original:
                    raise ExportError(f"Scalar round trip failed: {column}")
        except (ValueError, UnicodeError, csv.Error) as exc:
            raise ExportError(f"{table}.csv: record {number}: {exc}") from exc
    try:
        return buffer.getvalue().encode("utf-8")
    except UnicodeError as exc:
        raise ExportError(f"{table}.csv: invalid Unicode text: {exc}") from exc


def check_destination(input_dir: Path, output_dir: Path) -> None:
    """Protect both selected inputs and repository canonical files, including aliases."""
    protected = [(directory / f"{table}.csv").resolve()
                 for directory in (input_dir, DEFAULT_INPUT_DIR) for table in SCHEMAS]
    for table in SCHEMAS:
        destination = (output_dir / f"{table}.csv").resolve()
        for source in protected:
            if destination == source or (destination.exists() and source.exists() and destination.samefile(source)):
                raise ExportError(f"Refusing to overwrite canonical input: {destination}")
    if output_dir.exists():
        if not output_dir.is_dir():
            raise ExportError(f"Output is not a directory: {output_dir}")
        expected = {f"{table}.csv" for table in SCHEMAS}
        unexpected = sorted(path.name for path in output_dir.iterdir() if path.name not in expected)
        if unexpected:
            raise ExportError(f"Output directory must contain only the six evidence CSVs; unexpected: {unexpected}")


def export_datasets(input_dir: Path = DEFAULT_INPUT_DIR, output_dir: Path | None = None) -> dict[str, int]:
    input_dir = input_dir.resolve()
    output_dir = (output_dir if output_dir is not None else input_dir / "zetaris").resolve()
    check_destination(input_dir, output_dir)
    rows = {table: read_canonical_csv(input_dir / f"{table}.csv", schema) for table, schema in SCHEMAS.items()}
    # Parse, transform and verify every file before creating/writing output files.
    payloads = {table: build_export(table, records) for table, records in rows.items()}
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        for table, payload in payloads.items():
            (output_dir / f"{table}.csv").write_bytes(payload)
    except OSError as exc:
        raise ExportError(f"Cannot write exports to {output_dir}: {exc}") from exc
    return {table: len(records) for table, records in rows.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR,
                        help="Directory containing the six canonical CSVs (default: repository data/)")
    parser.add_argument("--output-dir", type=Path,
                        help="Export directory (default: <input-dir>/zetaris/); canonical inputs are protected")
    args = parser.parse_args()
    try:
        counts = export_datasets(args.input_dir, args.output_dir)
    except (ExportError, OSError) as exc:
        parser.exit(1, f"Export failed: {exc}\n")
    destination = (args.output_dir if args.output_dir is not None else args.input_dir / "zetaris").resolve()
    print(f"Exported six evidence CSVs to {destination}")
    for table, count in counts.items():
        payload = (destination / f"{table}.csv").read_bytes()
        print(f"{table}.csv: rows={count}, bytes={len(payload)}, sha256={hashlib.sha256(payload).hexdigest()}")
    print("Local CSV and round-trip checks passed. Manual Zetaris ingestion verification is still required.")


if __name__ == "__main__":
    main()
