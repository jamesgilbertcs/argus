"""Bounded, content-safe diagnostics. Never repairs or accepts a model response."""

from __future__ import annotations

from typing import Any

if __package__:
    from .reasoning_assessment import ASSESSMENT_SCHEMA, _structure
    from .reasoning_view import ReasoningError
else:
    from reasoning_assessment import ASSESSMENT_SCHEMA, _structure
    from reasoning_view import ReasoningError


MAX_SCHEMA_ERRORS = 20
MAX_SYNTAX_SCAN_CHARS = 262_144
FINISH_REASONS = frozenset({"stop", "length", "content_filter", "tool_calls", "function_call", "error", "cancelled"})


def _enum_literals(schema: dict) -> set[str]:
    literals = {v for v in schema.get("enum", []) if isinstance(v, str)}
    for child in schema.get("properties", {}).values():
        literals.update(_enum_literals(child))
    if "items" in schema:
        literals.update(_enum_literals(schema["items"]))
    for child in schema.get("anyOf", []):
        literals.update(_enum_literals(child))
    return literals


SAFE_ENUM_LITERALS = frozenset(_enum_literals(ASSESSMENT_SCHEMA))


def schema_errors(value: Any, schema: dict = ASSESSMENT_SCHEMA) -> dict:
    """Collect up to 20 errors using the existing validator as the authority.

    Only schema-owned property names enter paths. Unexpected model-owned names
    are represented by a count at their parent, never echoed. Error traversal is
    deterministic and stops after discovering one additional error beyond the cap.
    """
    errors: list[dict] = []

    def add(check: str, path: str, expected: Any, actual: Any, *, missing: bool = False, **extra: Any) -> None:
        item = {"stage": "schema", "check": check, "path": path, "expected": expected,
                "actual_type": "missing" if missing else type(actual).__name__, **extra}
        if not missing and (actual is None or type(actual) is bool or
                            type(actual) is str and actual in SAFE_ENUM_LITERALS):
            item["actual_value"] = actual
        errors.append(item)

    def visit(actual: Any, schema: dict, path: str, depth: int) -> None:
        if len(errors) > MAX_SCHEMA_ERRORS:
            return
        try:
            _structure(actual, schema, depth=depth, path=path)
            return
        except ReasoningError as error:
            detail = getattr(error, "details", {})
        kind = schema.get("type")
        if depth <= 20 and kind == "object" and type(actual) is dict:
            properties = schema["properties"]
            for key, child in properties.items():
                if len(errors) > MAX_SCHEMA_ERRORS:
                    return
                if key not in actual:
                    expected = {name: child[name] for name in ("type", "enum", "anyOf") if name in child}
                    add("MISSING_FIELD", path + "." + key, expected, None, missing=True)
                else:
                    visit(actual[key], child, path + "." + key, depth + 1)
            extra_count = len(actual.keys() - properties.keys())
            if extra_count and len(errors) <= MAX_SCHEMA_ERRORS:
                add("UNEXPECTED_FIELDS", path, {"additionalProperties": False}, actual, unexpected_field_count=extra_count)
        elif depth <= 20 and kind == "array" and type(actual) is list:
            if len(actual) > 1000:
                add("ARRAY_LIMIT", path, {"max_items": 1000}, actual, actual_length=len(actual))
            for index, child in enumerate(actual[:1000]):
                if len(errors) > MAX_SCHEMA_ERRORS:
                    break
                visit(child, schema["items"], f"{path}[{index}]", depth + 1)
        else:
            check = detail.get("check")
            expected = detail.get("expected")
            if not check:
                check = "NESTING_LIMIT" if depth > 20 else "TEXT_EMPTY" if type(actual) is str and not actual.strip() else "TEXT_LIMIT"
                expected = {"max_depth": 20} if depth > 20 else {"nonempty": True, "max_characters": 6000}
            add(check, path, expected, actual)

    visit(value, schema, "assessment", 0)
    return {"errors": errors[:MAX_SCHEMA_ERRORS], "error_limit": MAX_SCHEMA_ERRORS,
            "errors_truncated": len(errors) > MAX_SCHEMA_ERRORS}


def termination_metadata(metadata: Any) -> dict:
    metadata = metadata if type(metadata) is dict else {}
    finish = metadata.get("finish_reason")
    safe_finish = finish if type(finish) is str and finish in FINISH_REASONS else None
    usage = metadata.get("usage") if type(metadata.get("usage")) is dict else {}
    count = usage.get("completion_tokens", usage.get("output_tokens"))
    safe_count = count if type(count) is int and 0 <= count <= 1_000_000_000 else None
    return {"finish_reason": safe_finish,
            "finish_reason_status": "available" if safe_finish is not None else "unavailable" if finish is None else "redacted_invalid",
            "completion_tokens": safe_count,
            "completion_tokens_status": "available" if safe_count is not None else "unavailable" if count is None else "redacted_invalid"}


def inference_metadata(metadata: dict, actual_model: str) -> dict:
    """Operational fields from provider-admitted metadata, never raw output."""
    result = termination_metadata(metadata)
    requested = metadata.get("requested_model")
    mode = metadata.get("output_mode")
    result.update({"requested_model": requested, "requested_model_status": "available" if requested else "unavailable",
                   "actual_model": actual_model or None, "actual_model_status": "available" if actual_model else "unavailable",
                   "output_mode": mode, "output_mode_status": "available" if mode else "unavailable"})
    limit = metadata.get("max_output_tokens")
    result["max_output_tokens"] = limit if type(limit) is int and 256 <= limit <= 32000 else None
    return result


def json_response_details(text: Any, termination: dict) -> dict:
    """Syntax indicators, not a claim that the transport actually cut off text."""
    known_text = type(text) is str
    empty = not text.strip() if known_text else None
    possible: bool | None = None
    reasons = []
    if termination["finish_reason"] == "length":
        possible = True
        reasons.append("reported_output_limit")
    if known_text and not empty and len(text) <= MAX_SYNTAX_SCAN_CHARS:
        stack = []
        quoted, escaped, mismatch = False, False, False
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "{[":
                stack.append(char)
            elif char in "}]":
                if not stack or (stack.pop(), char) not in (("{", "}"), ("[", "]")):
                    mismatch = True
                    break
        if not mismatch and (quoted or stack):
            possible = True
            reasons.append("unclosed_string_at_end" if quoted else "unclosed_container_at_end")
    scan_limited = known_text and len(text) > MAX_SYNTAX_SCAN_CHARS
    return {"response_empty": empty, "response_text_available": known_text,
            "appears_truncated": possible, "truncation_basis": reasons,
            "truncation_is_heuristic": possible is True and "reported_output_limit" not in reasons,
            "syntax_scan_limited": scan_limited, "syntax_scan_character_limit": MAX_SYNTAX_SCAN_CHARS}


def failure_details(stage: str, error: ReasoningError, text: Any, metadata: Any, assessment: Any,
                    *, schema: dict = ASSESSMENT_SCHEMA) -> tuple[dict, dict]:
    details = dict(getattr(error, "details", {}))
    details["stage"] = stage
    termination = termination_metadata(metadata)
    if stage == "schema":
        details.update(schema_errors(assessment, schema))
    if stage == "json_parsing":
        details.update(json_response_details(text, termination))
    return details, termination
