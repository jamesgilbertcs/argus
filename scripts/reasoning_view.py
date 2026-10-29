"""Pure, lossless-in-scope projection of Phase 3A bundles for interpretation."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

if __package__:
    from .evidence_collector import canonical
    from .evidence_queries import KEYS, identifier
    from .export_zetaris import ARRAY_FIELDS, TEXT_FIELDS, decode_array, decode_text
else:
    from evidence_collector import canonical
    from evidence_queries import KEYS, identifier
    from export_zetaris import ARRAY_FIELDS, TEXT_FIELDS, decode_array, decode_text


DEFAULT_VIEW_BYTES = 200_000
COLLECTION_STATES = ("collected", "partial", "alert_not_found", "alert_unavailable")


class ReasoningError(ValueError):
    """Only static, credential-free messages are exposed by this pipeline."""


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _require(condition: bool) -> None:
    if not condition:
        raise ReasoningError("Invalid evidence-bundle structure or provenance")


def build_reasoning_view(bundle: dict[str, Any], *, max_bytes: int = DEFAULT_VIEW_BYTES) -> dict[str, Any]:
    """Keep every record/variant and source field. Refuse oversize; never select/slice.

    Variant hashes identify ORIGINAL encoded records, not their normalised values.
    Query SQL, repeated membership/headers and wire timestamp projections are
    omitted. Record timestamp interpretations and all query availability survive.
    """
    _require(type(bundle) is dict and bundle.get("schema_version") == "1.0")
    _require(set(bundle) == {"schema_version", "alert_id", "collection_status", "scope", "records",
                             "references", "network_checks", "queries", "issues"})
    _require(bundle["collection_status"] in COLLECTION_STATES)
    try:
        identifier(bundle["alert_id"])
    except (ValueError, TypeError):
        raise ReasoningError("Invalid evidence alert identifier") from None
    _require(type(bundle["scope"]) is dict and type(bundle["records"]) is dict)
    _require(set(bundle["records"]) == set(KEYS))
    for field in ("references", "network_checks", "queries", "issues"):
        _require(type(bundle[field]) is list and all(type(item) is dict for item in bundle[field]))
    view = {"schema_version": "1.0", "alert_id": bundle["alert_id"], "bundle_sha256": digest(bundle),
            "collection_status": bundle["collection_status"], "scope": deepcopy(bundle["scope"]),
            "selection_policy": "all_records_all_variants_no_truncation",
            "records": [], "references": deepcopy(bundle["references"]),
            "network_checks": deepcopy(bundle["network_checks"]), "issues": deepcopy(bundle["issues"]),
            "query_completeness": [], "gaps": []}

    def gap(kind: str, details: Any) -> None:
        item = {"kind": kind, "details": deepcopy(details)}
        view["gaps"].append({"gap_id": "G" + digest(item), **item})

    gap("bounded_scope", bundle["scope"])
    if bundle["collection_status"] != "collected":
        gap("collection_status", bundle["collection_status"])
    for issue in bundle["issues"]:
        gap("collection_issue", issue)
    for reference in bundle["references"]:
        if reference.get("status") != "resolved":
            gap("unresolved_reference", reference)
    for check in bundle["network_checks"]:
        if check.get("status") not in ("matched", "no_exact_match"):
            gap("incomplete_network_check", check)
    for query in bundle["queries"]:
        _require(all(key in query for key in ("query_id", "scope", "status", "limit", "returned_rows")))
        # Explicit exclusions: unknown metadata, unusable rows and future summary
        # fields remain visible rather than being silently lost by a whitelist.
        summary = {key: deepcopy(value) for key, value in query.items()
                   if key not in {"sql", "headers", "timestamp_context"}}
        view["query_completeness"].append(summary)
        if query["status"] != "complete":
            gap("query_incomplete", summary)
        if query.get("unusable_rows") or query.get("additional_response_metadata"):
            gap("query_unreviewed_data", summary)
    projections: dict[str, list[dict]] = {}
    for query in bundle["queries"]:
        _require(type(query.get("timestamp_context", [])) is list)
        for context in query.get("timestamp_context", []):
            _require(type(context) is dict and type(context.get("record_fingerprint")) is str)
            projections.setdefault(context["record_fingerprint"], []).append(
                {key: deepcopy(value) for key, value in context.items() if key != "record_fingerprint"})
    seen = set()
    used_fingerprints = set()
    sources: dict[tuple[str, str], set[str]] = {}
    for table in sorted(KEYS):
        _require(type(bundle["records"][table]) is list)
        for envelope in bundle["records"][table]:
            _require(type(envelope) is dict and {"source", "variant_id", "record", "roles", "query_ids"} <= set(envelope))
            _require(set(envelope) <= {"source", "variant_id", "record", "roles", "query_ids", "timestamp_interpretation"})
            source, raw = envelope["source"], envelope["record"]
            _require(type(source) is dict and set(source) == {"table", "id_column", "id"} and type(raw) is dict)
            _require(source["table"] == f"ARGUS_SECURITY.{table}_compat_test" and source["id_column"] == KEYS[table])
            _require(type(source["id"]) is str and source["id"] != "" and raw.get(KEYS[table]) == source["id"])
            _require(envelope["variant_id"] == digest(raw))
            _require(type(envelope["roles"]) is list and all(type(role) is str for role in envelope["roles"]))
            citation = {"table": source["table"], "id": source["id"], "variant_id": envelope["variant_id"]}
            key = canonical(citation)
            _require(key not in seen)
            seen.add(key)
            sources.setdefault((source["table"], source["id"]), set()).add(envelope["variant_id"])
            values = deepcopy(raw)
            for field in sorted(ARRAY_FIELDS.get(table, frozenset()) | TEXT_FIELDS.get(table, frozenset())):
                if field not in raw or raw[field] is None:
                    continue
                try:
                    if type(raw[field]) is not str:
                        raise ValueError
                    values[field] = decode_array(raw[field]) if field in ARRAY_FIELDS.get(table, ()) else decode_text(raw[field])
                except ValueError:
                    gap("normalisation_failed_raw_value_retained", {"source": citation, "field": field})
            record = {"source": citation, "roles": sorted(set(envelope["roles"])), "values": values}
            if "timestamp_interpretation" in envelope:
                record["timestamp_interpretation"] = deepcopy(envelope["timestamp_interpretation"])
            fingerprint = envelope["variant_id"]
            used_fingerprints.add(fingerprint)
            observations = sorted({canonical(p) for p in projections.get(fingerprint, [])})
            time_issue = any(issue.get("source_id") == source["id"] and "timestamp" in str(issue.get("code", ""))
                             for issue in bundle["issues"])
            if observations and (len(observations) > 1 or time_issue or "timestamp_interpretation" not in record):
                # Routine identical verified projections are redundant. Conflicting
                # or unverified projections are evidence and MUST remain visible.
                record["timestamp_observations"] = [json.loads(p) for p in observations]
                gap("timestamp_observations_require_review", {"source": citation, "observations": record["timestamp_observations"]})
            view["records"].append(record)
    for fingerprint in sorted(set(projections) - used_fingerprints):
        gap("unmatched_timestamp_projection", {"record_fingerprint": fingerprint, "observations": projections[fingerprint]})
    for (table, source_id), variants in sorted(sources.items()):
        if len(variants) > 1:
            gap("conflicting_variants", {"table": table, "id": source_id, "variant_ids": sorted(variants)})
    for reference in bundle["references"]:
        _require(type(reference.get("records")) is list)
        for citation in reference["records"]:
            _require(canonical(citation) in seen)
    alerts = [r for r in view["records"] if r["source"]["table"] == "ARGUS_SECURITY.security_alerts_compat_test"]
    _require(all(r["source"]["id"] == bundle["alert_id"] for r in alerts))
    if not alerts:
        gap("alert_record_absent", {"alert_id": bundle["alert_id"]})
    view["records"].sort(key=lambda r: canonical(r["source"]))
    view["query_completeness"].sort(key=lambda q: canonical(q))
    view["gaps"] = sorted({g["gap_id"]: g for g in view["gaps"]}.values(), key=lambda g: g["gap_id"])
    if type(max_bytes) is not int or max_bytes < 1 or len(canonical(view).encode("utf-8")) > max_bytes:
        raise ReasoningError("Reasoning view exceeds the input budget; no evidence was truncated")
    # Detach all objects and convert native Decimal values to the collector's
    # existing exact tagged representation, without converting integers/floats.
    return json.loads(canonical(view))
