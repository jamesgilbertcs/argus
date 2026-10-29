"""Assessment schema, strict parsing and deterministic citation/coverage checks.

Validation establishes structure and citation membership, not factual correctness.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

if __package__:
    from .reasoning_view import COLLECTION_STATES, ReasoningError
else:
    from reasoning_view import COLLECTION_STATES, ReasoningError


def strict_json(text: str) -> Any:
    """Reject duplicate keys, non-JSON constants and unbounded output safely."""
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def constant(_: str) -> Any:
        raise ValueError

    try:
        if not isinstance(text, str) or len(text.encode("utf-8")) > 8_388_608:
            raise ValueError
        return json.loads(text, object_pairs_hook=pairs, parse_float=Decimal, parse_constant=constant)
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ReasoningError("Invalid JSON input or response") from None


def obj(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def array(items: dict) -> dict:
    return {"type": "array", "items": items}


STRING = {"type": "string"}
CITATION = obj({"table": STRING, "id": STRING, "variant_id": STRING})
FINDING = obj({"kind": {"type": "string", "enum": ["OBSERVED_FACT", "INTERPRETATION"]},
               "statement": STRING, "evidence": array(CITATION)})
ASSESSMENT_SCHEMA = obj({
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


def _structure(value: Any, schema: dict, *, depth: int = 0) -> None:
    """Validate precisely the small schema subset above; no external dependency."""
    if depth > 20:
        raise ReasoningError("Assessment nesting exceeds the limit")
    kind = schema["type"]
    valid = {"object": type(value) is dict, "array": type(value) is list,
             "string": type(value) is str, "boolean": type(value) is bool}[kind]
    if not valid or ("enum" in schema and value not in schema["enum"]):
        raise ReasoningError("Assessment has an invalid type or enum value")
    if kind == "object":
        if set(value) != set(schema["properties"]):
            raise ReasoningError("Assessment has missing or unexpected fields")
        for key, child in schema["properties"].items():
            _structure(value[key], child, depth=depth + 1)
    elif kind == "array":
        if len(value) > 1000:
            raise ReasoningError("Assessment array exceeds the limit")
        for item in value:
            _structure(item, schema["items"], depth=depth + 1)
    elif kind == "string" and (not value.strip() or len(value) > 6000):
        raise ReasoningError("Assessment text is empty or exceeds the limit")


def validate_assessment(assessment: dict, view: dict) -> dict:
    _structure(assessment, ASSESSMENT_SCHEMA)
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
    return assessment


def parse_assessment(text: str, view: dict) -> dict:
    return validate_assessment(strict_json(text), view)
