"""Assessment schema, strict parsing and deterministic citation/coverage checks.

Validation establishes structure and citation membership, not factual correctness.
"""

from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from typing import Any

if __package__:
    from .reasoning_view import COLLECTION_STATES, ReasoningError
    from .evidence_relationships import derive_relationships
    from .evidence_collector import canonical
else:
    from reasoning_view import COLLECTION_STATES, ReasoningError
    from evidence_relationships import derive_relationships
    from evidence_collector import canonical


class JSONValidationError(ReasoningError):
    def __init__(self, check: str, *, line: int | None = None, column: int | None = None,
                 parser_category: str | None = None) -> None:
        self.details = {"check": check, "line": line, "column": column, "parser_category": parser_category or check}
        super().__init__("Invalid JSON input or response: " + check)


def strict_json(text: str) -> Any:
    """Reject duplicate keys, non-JSON constants and unbounded output safely."""
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            if key in result:
                raise JSONValidationError("DUPLICATE_PROPERTIES")
            result[key] = value
        return result

    def constant(_: str) -> Any:
        raise JSONValidationError("NON_JSON_CONSTANT")

    try:
        if not isinstance(text, str):
            raise JSONValidationError("INPUT_TYPE")
        if len(text.encode("utf-8")) > 8_388_608:
            raise JSONValidationError("BYTE_LIMIT")
        return json.loads(text, object_pairs_hook=pairs, parse_float=Decimal, parse_constant=constant)
    except JSONValidationError:
        raise
    except json.JSONDecodeError as error:
        categories = {"Expecting value": "EXPECTED_VALUE", "Expecting property name enclosed in double quotes": "EXPECTED_PROPERTY_NAME",
                      "Expecting ':' delimiter": "EXPECTED_COLON", "Expecting ',' delimiter": "EXPECTED_COMMA", "Extra data": "EXTRA_DATA"}
        category = categories.get(error.msg, "SYNTAX")
        if error.msg.startswith("Unterminated string"):
            category = "UNTERMINATED_STRING"
        elif error.msg.startswith("Invalid "):
            category = "INVALID_ESCAPE_OR_CONTROL"
        raise JSONValidationError("SYNTAX", line=error.lineno, column=error.colno, parser_category=category) from None
    except UnicodeError:
        raise JSONValidationError("ENCODING") from None
    except RecursionError:
        raise JSONValidationError("NESTING_LIMIT") from None
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise JSONValidationError("INVALID_JSON") from None


def obj(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def array(items: dict) -> dict:
    return {"type": "array", "items": items}


STRING = {"type": "string"}
CITATION = obj({"table": STRING, "id": STRING, "variant_id": STRING})
FINDING = obj({"kind": {"type": "string", "enum": ["OBSERVED_FACT", "INTERPRETATION"]},
               "statement": STRING, "evidence": array(CITATION)})
LEGACY_ASSESSMENT_SCHEMA = obj({
    "schema_version": {"type": "string", "enum": ["1.0"]},
    "alert_id": STRING,
    "classification": {"type": "string", "enum": ["LIKELY_TRUE_POSITIVE", "LIKELY_FALSE_POSITIVE", "INCONCLUSIVE"]},
    "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
    "executive_summary": STRING,
    "key_findings": array(FINDING),
    "supporting_evidence": array(CITATION),
    "counterevidence": array(FINDING),
    "alternative_explanations": array(obj({"explanation": STRING, "evidence": array(CITATION), "verification_needed": STRING})),
    "unknowns": array(obj({"gap_ids": array(STRING), "description": STRING})),
    "recommended_next_steps": array(STRING),
    "collection_status": {"type": "string", "enum": list(COLLECTION_STATES)},
    "evidence_complete": {"type": "boolean", "enum": [False]},
})

FACT_KINDS = ["NETWORK_MATCH_STATUS", "SHARED_REPORTED_DEVICE_ID", "AUTHENTICATION_OUTCOME",
              "AUTHENTICATION_TIMESTAMP", "AUTHENTICATION_UTC", "AUTHENTICATION_DELTA_MICROSECONDS"]
CLAIM = obj({"fact_id": STRING, "kind": {"type": "string", "enum": FACT_KINDS},
             "value": {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]},
             "evidence": array(CITATION)})
ASSESSMENT_SCHEMA = deepcopy(LEGACY_ASSESSMENT_SCHEMA)
ASSESSMENT_SCHEMA["properties"].update({"schema_version": {"type": "string", "enum": ["2.0"]},
                                         "assessed_hypothesis": STRING, "factual_claims": array(CLAIM)})
ASSESSMENT_SCHEMA["required"] = list(ASSESSMENT_SCHEMA["properties"])


class FactualConsistencyError(ReasoningError):
    def __init__(self, code: str, *, claim_index: int | None = None, fact_id: str | None = None) -> None:
        # No model-supplied IDs, values, statements or credentials in diagnostics.
        self.details = {"check": code, "claim_index": claim_index, "fact_id": fact_id}
        super().__init__("Factual consistency check failed: " + code)


class SchemaValidationError(ReasoningError):
    def __init__(self, check: str, path: str, expected: Any, value: Any) -> None:
        self.details = {"check": check, "path": path, "expected": expected, "actual_type": type(value).__name__}
        super().__init__(f"Assessment schema check failed: {check} at {path}")


def _structure(value: Any, schema: dict, *, depth: int = 0, path: str = "assessment") -> None:
    """Validate precisely the small schema subset above; no external dependency."""
    if depth > 20:
        raise ReasoningError("Assessment nesting exceeds the limit")
    if "anyOf" in schema:
        for alternative in schema["anyOf"]:
            try:
                _structure(value, alternative, depth=depth + 1, path=path)
                return
            except ReasoningError:
                continue
        raise SchemaValidationError("VALUE_TYPE", path, [s["type"] for s in schema["anyOf"]], value)
    kind = schema["type"]
    valid = {"object": type(value) is dict, "array": type(value) is list,
             "string": type(value) is str, "boolean": type(value) is bool,
             "integer": type(value) is int, "null": value is None}[kind]
    if not valid:
        raise SchemaValidationError("TYPE", path, kind, value)
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError("ENUM", path, schema["enum"], value)
    if kind == "object":
        if set(value) != set(schema["properties"]):
            raise SchemaValidationError("FIELDS", path, {"required": list(schema["properties"])}, value)
        for key, child in schema["properties"].items():
            _structure(value[key], child, depth=depth + 1, path=path + "." + key)
    elif kind == "array":
        if len(value) > 1000:
            raise ReasoningError("Assessment array exceeds the limit")
        for index, item in enumerate(value):
            _structure(item, schema["items"], depth=depth + 1, path=f"{path}[{index}]")
    elif kind == "string" and (not value.strip() or len(value) > 6000):
        raise ReasoningError("Assessment text is empty or exceeds the limit")


def validate_structure(assessment: dict, *, allow_legacy: bool = False) -> None:
    schema = LEGACY_ASSESSMENT_SCHEMA if allow_legacy and type(assessment) is dict and assessment.get("schema_version") == "1.0" else ASSESSMENT_SCHEMA
    _structure(assessment, schema)


def validate_factual_claims(assessment: dict, view: dict) -> None:
    # Recompute from admitted source records/checks, not model assertions or a
    # possibly edited fact catalog supplied by a caller.
    expected = derive_relationships(view)
    facts = {f["fact_id"]: f for f in expected["facts"]}
    seen = set()
    for index, claim in enumerate(assessment["factual_claims"]):
        fact = facts.get(claim["fact_id"])
        if fact is None:
            raise FactualConsistencyError("UNKNOWN_FACT", claim_index=index)
        safe_id = fact["fact_id"]
        if safe_id in seen:
            raise FactualConsistencyError("DUPLICATE_FACT_CLAIM", claim_index=index, fact_id=safe_id)
        seen.add(safe_id)
        if claim["kind"] != fact["kind"]:
            raise FactualConsistencyError("FACT_KIND_MISMATCH", claim_index=index, fact_id=safe_id)
        if type(claim["value"]) is not type(fact["value"]) or claim["value"] != fact["value"]:
            raise FactualConsistencyError("FACT_VALUE_MISMATCH", claim_index=index, fact_id=safe_id)
        if sorted(map(canonical, claim["evidence"])) != sorted(map(canonical, fact["evidence"])):
            raise FactualConsistencyError("FACT_EVIDENCE_MISMATCH", claim_index=index, fact_id=safe_id)
    if not set(expected["required_fact_ids"]) <= seen:
        raise FactualConsistencyError("REQUIRED_FACT_CLAIM_MISSING")


def validate_assessment(assessment: dict, view: dict, *, allow_legacy: bool = False, check_facts: bool = True) -> dict:
    validate_structure(assessment, allow_legacy=allow_legacy)
    if assessment["alert_id"] != view["alert_id"]:
        raise ReasoningError("Assessment alert ID does not match the evidence")
    if assessment["collection_status"] != view["collection_status"]:
        raise ReasoningError("Assessment misrepresents collection status")
    if len(assessment["executive_summary"]) > 1000:
        raise ReasoningError("Assessment executive summary exceeds the limit")
    allowed = {(r["source"]["table"], r["source"]["id"], r["source"]["variant_id"]) for r in view["records"]}

    def citations(items: list[dict]) -> None:
        for item in items:
            if (item["table"], item["id"], item["variant_id"]) not in allowed:
                raise ReasoningError("Assessment cites an absent source record or variant")

    findings = assessment["key_findings"] + assessment["counterevidence"]
    if allowed and not assessment["key_findings"]:
        raise ReasoningError("Assessment requires cited findings for supplied records")
    for finding in findings:
        if not finding["evidence"]:
            raise ReasoningError("Every factual finding and interpretation requires citations")
        citations(finding["evidence"])
    citations(assessment["supporting_evidence"])
    if findings and not assessment["supporting_evidence"]:
        raise ReasoningError("Assessment supporting evidence is empty")
    for alternative in assessment["alternative_explanations"]:
        citations(alternative["evidence"])
    gap_ids = {g["gap_id"] for g in view["gaps"]}
    acknowledged = {gap for item in assessment["unknowns"] for gap in item["gap_ids"]}
    if acknowledged != gap_ids:
        raise ReasoningError("Assessment must acknowledge every supplied gap and no invented gap IDs")
    if not assessment["alternative_explanations"] or not assessment["recommended_next_steps"]:
        raise ReasoningError("Assessment requires alternatives and proposed next steps")
    has_alert = any(r["source"]["table"] == "ARGUS_SECURITY.security_alerts_compat_test" for r in view["records"])
    if not has_alert and (assessment["classification"] != "INCONCLUSIVE" or assessment["confidence"] != "LOW"):
        raise ReasoningError("An absent alert requires an inconclusive, low-confidence assessment")
    if check_facts and assessment["schema_version"] == "2.0":
        validate_factual_claims(assessment, view)
    return assessment


def parse_assessment(text: str, view: dict) -> dict:
    return validate_assessment(strict_json(text), view)
