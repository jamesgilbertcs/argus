"""Deterministic final contract 3.0; no inference, retrieval or conclusion repair."""

from __future__ import annotations

from copy import deepcopy

if __package__:
    from .evidence_relationships import derive_relationships
    from .reasoning_assessment import validate_model_content
    from .reasoning_diagnostics import inference_metadata
    from .reasoning_view import digest
else:
    from evidence_relationships import derive_relationships
    from reasoning_assessment import validate_model_content
    from reasoning_diagnostics import inference_metadata
    from reasoning_view import digest


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
        "validation": "Model structure, citations, collection-gap preservation and supported structured facts checked; prose and conclusions need human review.",
    }
