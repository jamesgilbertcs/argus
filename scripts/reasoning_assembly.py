"""Deterministic final contract 3.0; no inference, retrieval or conclusion repair."""

from __future__ import annotations

from copy import deepcopy

if __package__:
    from .evidence_collector import canonical
    from .evidence_relationships import derive_relationships
    from .reasoning_assessment import MODEL_OUTPUT_SCHEMA, STRING, _structure, obj, validate_model_content
    from .reasoning_diagnostics import inference_metadata
    from .reasoning_view import COLLECTION_STATES, ReasoningError, digest
else:
    from evidence_collector import canonical
    from evidence_relationships import derive_relationships
    from reasoning_assessment import MODEL_OUTPUT_SCHEMA, STRING, _structure, obj, validate_model_content
    from reasoning_diagnostics import inference_metadata
    from reasoning_view import COLLECTION_STATES, ReasoningError, digest


FINAL_ASSESSMENT_SCHEMA = obj({**deepcopy(MODEL_OUTPUT_SCHEMA["properties"]),
    "schema_version": {"type": "string", "enum": ["3.0"]}, "alert_id": STRING,
    "collection_status": {"type": "string", "enum": list(COLLECTION_STATES)},
    "evidence_complete": {"anyOf": [{"type": "boolean"}, {"type": "null"}]}})
VALIDATION_STAGES = ("json_parsing", "schema", "citations_and_gaps", "factual_consistency", "final_assembly")
VALIDATION_NOTICE = "Model structure, citations, collection-gap preservation and supported structured facts checked; prose and conclusions need human review."


def collection_metadata(view: dict) -> dict:
    """Completeness of the REPORTED bounded plan, never investigation sufficiency.

    A completed plan with recorded limitations is incomplete. Without any query
    receipts or a declared history window, completeness cannot be established.
    Bounded scope itself limits inference but is not a failure within that scope.
    """
    limitations = [g for g in view["gaps"] if g["kind"] != "bounded_scope"]
    queries = view["query_completeness"]
    basis = []
    if view["collection_status"] != "collected" or limitations:
        completeness = "INCOMPLETE"
        basis.append("recorded_collection_limitations")
    elif not queries or not view["scope"].get("history_window"):
        completeness = "UNKNOWN"
        if not queries:
            basis.append("query_receipts_unavailable")
        if not view["scope"].get("history_window"):
            basis.append("declared_history_window_unavailable")
    else:
        completeness = "COMPLETE"
        basis.append("reported_plan_complete_without_within_scope_gaps")
    return {
        "policy": "reported_bounded_plan_v1",
        "collection_succeeded": view["collection_status"] == "collected",
        "within_scope_completeness": completeness,
        "completeness_basis": basis,
        "investigation_sufficiency": "NOT_DETERMINED",
        "scope": deepcopy(view["scope"]),
        "limitations": deepcopy(view["gaps"]),
        "issues": deepcopy(view["issues"]),
        "references": deepcopy(view["references"]),
        "network_checks": deepcopy(view["network_checks"]),
        "query_completeness": deepcopy(queries),
    }


def assemble_assessment(content: dict, view: dict, response) -> dict:
    """Revalidate before assembly; contradictory model assertions are never fixed.

    No timestamps/random IDs are generated, so identical inputs are byte stable
    under the existing serializer. Provider metadata may vary between live calls.
    """
    validate_model_content(content, view)
    collection = collection_metadata(view)
    complete = {"COMPLETE": True, "INCOMPLETE": False, "UNKNOWN": None}[collection["within_scope_completeness"]]
    metadata = deepcopy(response.metadata)
    inference = inference_metadata(metadata, response.model)
    return {
        "schema_version": "3.0",
        "model_output_contract": "interpretation_v1",
        "bundle_sha256": view["bundle_sha256"], "reasoning_view_sha256": digest(view),
        "provider": {"name": response.provider, "model": response.model,
                     "mock_generated": response.mock_generated, "metadata": metadata},
        "inference": inference,
        "provenance": {"reasoning_view_schema_version": view["schema_version"],
                       "selection_policy": view["selection_policy"],
                       "sources": [deepcopy(r["source"]) for r in view["records"]]},
        "relationships": derive_relationships(view),
        "collection": collection,
        "assessment": {"schema_version": "3.0", "alert_id": view["alert_id"],
                       "collection_status": view["collection_status"], "evidence_complete": complete,
                       **deepcopy(content)},
        "validation": VALIDATION_NOTICE,
    }


def validate_final_assessment(result: dict, view: dict) -> None:
    """Check final schema and exact deterministic metadata, before artifact writes.

    Also accepts saved v3 artifacts with all five passed stage markers. Historical
    v1/v2 validators remain separate and unchanged. No field is filled or repaired.
    """
    def require(condition: bool, code: str) -> None:
        if not condition:
            raise ReasoningError("Final assessment check failed: " + code)

    def same(a, b) -> bool:
        # Python's True == 1 must not authorise altered deterministic metadata.
        try:
            return canonical(a) == canonical(b)
        except (ValueError, TypeError, RecursionError):
            return False

    keys = {"schema_version", "model_output_contract", "bundle_sha256", "reasoning_view_sha256", "provider",
            "inference", "provenance", "relationships", "collection", "assessment", "validation"}
    require(type(result) is dict and set(result) in (keys, keys | {"validation_stages"}), "WRAPPER_FIELDS")
    _structure(result["assessment"], FINAL_ASSESSMENT_SCHEMA)
    content = {key: result["assessment"][key] for key in MODEL_OUTPUT_SCHEMA["properties"]}
    validate_model_content(content, view)
    require(result["schema_version"] == "3.0" and result["model_output_contract"] == "interpretation_v1", "VERSION")
    require(result["assessment"]["alert_id"] == view["alert_id"] and
            result["assessment"]["collection_status"] == view["collection_status"], "ALERT_OR_COLLECTION_STATUS")
    expected = collection_metadata(view)
    require(same(result["collection"], expected), "COLLECTION_METADATA")
    complete = {"COMPLETE": True, "INCOMPLETE": False, "UNKNOWN": None}[expected["within_scope_completeness"]]
    require(result["assessment"]["evidence_complete"] is complete, "COMPLETENESS")
    require(result["bundle_sha256"] == view["bundle_sha256"] and result["reasoning_view_sha256"] == digest(view), "HASHES")
    require(same(result["relationships"], derive_relationships(view)), "RELATIONSHIPS")
    provenance = {"reasoning_view_schema_version": view["schema_version"], "selection_policy": view["selection_policy"],
                  "sources": [r["source"] for r in view["records"]]}
    require(same(result["provenance"], provenance), "PROVENANCE")
    provider = result["provider"]
    require(type(provider) is dict and set(provider) == {"name", "model", "mock_generated", "metadata"}, "PROVIDER_FIELDS")
    require(provider["name"] in ("mock", "openai", "meterless") and type(provider["mock_generated"]) is bool and
            type(provider["model"]) is str and bool(provider["model"].strip()) and type(provider["metadata"]) is dict, "PROVIDER_TYPES")
    require(provider["mock_generated"] == (provider["name"] == "mock"), "MOCK_LABEL")
    require(same(result["inference"], inference_metadata(provider["metadata"], provider["model"])), "INFERENCE_METADATA")
    require(result["validation"] == VALIDATION_NOTICE, "VALIDATION_NOTICE")
    if "validation_stages" in result:
        require(result["validation_stages"] == {key: "passed" for key in VALIDATION_STAGES}, "VALIDATION_STAGES")
