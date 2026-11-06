"""Pure relationships over an admitted reasoning view; no retrieval or decoding.

Network membership comes from the collector's exact-match checks. This module
resolves their existing IDs and checks consistency; it does not rematch IP ranges.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
from typing import Any

if __package__:
    from .evidence_collector import canonical
    from .evidence_queries import instant, utc
else:
    from evidence_collector import canonical
    from evidence_queries import instant, utc


AUTH = "ARGUS_SECURITY.authentication_logs_compat_test"
ACCESS = "ARGUS_SECURITY.resource_access_logs_compat_test"
NETWORK = "ARGUS_SECURITY.approved_networks_compat_test"
RESOURCE = "ARGUS_SECURITY.resources_compat_test"


def source_key(source: dict) -> str:
    return canonical(source)


def verified_time(record: dict, issues: list[dict]) -> Any:
    """Use the collector's interpretation only; conflicting projections veto it."""
    if any(i.get("source_id") == record["source"]["id"] and "timestamp" in str(i.get("code", "")) for i in issues):
        return None
    if "timestamp_observations" in record:
        return None
    context = record.get("timestamp_interpretation", {})
    if context.get("basis") not in ("explicit_source_offset", "verified_same_query_utc_projection"):
        return None
    try:
        return instant(context.get("utc"))
    except (ValueError, TypeError):
        return None


def derive_relationships(view: dict) -> dict:
    """All per-record facts, grouped devices, sessions/resources, bounded pairs.

    Pairs are adjacent within each reported user's verified chronology, plus
    adjacent explicit references for that user (source order when time unknown).
    No record/variant is dropped; this avoids quadratic all-pairs expansion.
    """
    records = sorted(view["records"], key=lambda r: source_key(r["source"]))
    auths = [r for r in records if r["source"]["table"] == AUTH]
    nets: dict[str, list] = defaultdict(list)
    resources: dict[str, list] = defaultdict(list)
    for r in records:
        if r["source"]["table"] == NETWORK:
            nets[r["source"]["id"]].append(r)
        if r["source"]["table"] == RESOURCE:
            resources[r["source"]["id"]].append(r)
    explicit = {canonical(s) for ref in view["references"] for s in ref["records"]}
    checks: dict[str, list] = defaultdict(list)
    for check in view["network_checks"]:
        if isinstance(check.get("source_ip"), str):
            checks[check["source_ip"]].append(check)
    output: dict[str, Any] = {"policy": "all_record_facts_and_groups; adjacent_per_user_and_adjacent_referenced_pairs",
                              "facts": [], "network_links": [], "device_groups": [], "authentication_pairs": [],
                              "session_access_links": [], "authentication_timeline": [], "issues": []}

    def fact(kind: str, value: Any, evidence: list[dict], details: dict, required: bool) -> str:
        item = {"kind": kind, "value": value, "evidence": sorted(evidence, key=canonical), "details": details}
        fact_id = "F" + hashlib.sha256(canonical(item).encode()).hexdigest()
        output["facts"].append({"fact_id": fact_id, **item, "required": required})
        return fact_id

    for r in records:
        ip = r["values"].get("source_ip")
        if r["source"]["table"] not in (AUTH, ACCESS, "ARGUS_SECURITY.security_alerts_compat_test"):
            continue
        observed = checks.get(ip, []) if isinstance(ip, str) else []
        check = observed[0] if len(observed) == 1 else None
        linked = [n for key in check.get("network_ids", []) for n in nets.get(key, [])] if check else []
        inconsistent = (len(observed) > 1 or bool(check and (
            check.get("status") == "matched" and (not linked or len(linked) != len(check.get("network_ids", [])))
            or check.get("status") != "matched" and check.get("network_ids")
            or any(n["values"].get("ip_address") != ip for n in linked))))
        status = ("CONFLICTING" if inconsistent else "NOT_CHECKED" if not check else
                  "MATCHED" if check.get("status") == "matched" else
                  "NO_EXACT_MATCH" if check.get("status") == "no_exact_match" else "INCOMPLETE")
        link = {"source": r["source"], "source_ip": ip, "status": status,
                "collector_checks": observed, "networks": [n["source"] for n in linked]}
        output["network_links"].append(link)
        if status in ("CONFLICTING", "NOT_CHECKED"):
            output["issues"].append({"code": "network_relationship_" + status.lower(), "source": r["source"]})
        if r["source"]["table"] == AUTH:
            required = source_key(r["source"]) in explicit
            fact("NETWORK_MATCH_STATUS", status, [r["source"]] + [n["source"] for n in linked],
                 {"source_ip": ip, "network_ids": check.get("network_ids", []) if check else [],
                  "meaning": "exact catalog membership only; inspect network status; no inference of legitimacy"}, required)

    groups: dict[str, list] = defaultdict(list)
    users: dict[str, list] = defaultdict(list)
    times = {}
    for r in auths:
        s, v = r["source"], r["values"]
        required = source_key(s) in explicit
        moment = verified_time(r, view["issues"])
        times[source_key(s)] = moment
        raw = v.get("timestamp")
        if not isinstance(raw, str) or not raw:
            raw = None
        fact("AUTHENTICATION_OUTCOME", v.get("outcome") if v.get("outcome") in ("SUCCESS", "FAILURE") else None,
             [s], {"meaning": "reported authentication outcome; null means unavailable or invalid"}, required)
        fact("AUTHENTICATION_TIMESTAMP", raw, [s], {"meaning": "exact original timestamp spelling"}, required)
        fact("AUTHENTICATION_UTC", utc(moment) if moment else None, [s],
             {"meaning": "collector-verified UTC; null means unverified or conflicting"}, required)
        output["authentication_timeline"].append({"source": s, "timestamp": raw, "verified_utc": utc(moment) if moment else None})
        device = v.get("device_id")
        if isinstance(device, str) and device:
            groups[device].append(r)
        user = v.get("user_id")
        if isinstance(user, str) and user:
            users[user].append(r)
    for device, members in sorted(groups.items()):
        if len({r["source"]["id"] for r in members}) >= 2:
            output["device_groups"].append({"reported_device_id": device, "sources": [r["source"] for r in members],
                                            "meaning": "equal reported identifiers; not proof of physical device or authorised operator"})

    pairs = {}
    for members in users.values():
        for candidates in ([r for r in members if times[source_key(r["source"])] is not None],
                           [r for r in members if source_key(r["source"]) in explicit]):
            ordered = sorted(candidates, key=lambda r: (times[source_key(r["source"])] is None,
                                                       times[source_key(r["source"])],
                                                       source_key(r["source"])))
            for a, b in zip(ordered, ordered[1:]):
                if a["source"]["id"] != b["source"]["id"]:
                    ordered_pair = sorted((a, b), key=lambda r: source_key(r["source"]))
                    pairs[canonical([r["source"] for r in ordered_pair])] = ordered_pair
    for a, b in pairs.values():
        sa, sb = a["source"], b["source"]
        da, db = a["values"].get("device_id"), b["values"].get("device_id")
        known = all(isinstance(d, str) and d for d in (da, db))
        same = known and da == db
        ta, tb = times[source_key(sa)], times[source_key(sb)]
        delta = tb - ta if ta is not None and tb is not None else None
        micros = ((delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds) if delta is not None else None
        required = source_key(sa) in explicit and source_key(sb) in explicit
        details = {"first": sa, "second": sb, "device_status": "SHARED" if same else "DIFFERENT" if known else "UNAVAILABLE",
                   "first_reported_device_id": da, "second_reported_device_id": db,
                   "time_status": "VERIFIED" if micros is not None else "UNVERIFIED",
                   "ordering": "BEFORE" if micros is not None and micros > 0 else "AFTER" if micros is not None and micros < 0 else "SAME_TIME" if micros == 0 else "UNKNOWN"}
        fact("SHARED_REPORTED_DEVICE_ID", da if same else None, [sa, sb], details, required)
        fact("AUTHENTICATION_DELTA_MICROSECONDS", micros, [sa, sb],
             {"first": sa, "second": sb, "meaning": "second minus first; null means time is unverified"}, required)
        output["authentication_pairs"].append(details | {"delta_microseconds": micros})

    sessions: dict[str, list] = defaultdict(list)
    def known_equal(a: Any, b: Any) -> bool | None:
        return a == b if all(isinstance(v, str) and v for v in (a, b)) else None

    for r in auths:
        sid = r["values"].get("session_id")
        if isinstance(sid, str) and sid and r["values"].get("outcome") == "SUCCESS":
            sessions[sid].append(r)
    for access in (r for r in records if r["source"]["table"] == ACCESS):
        v = access["values"]
        matches = sessions.get(v.get("session_id"), []) if isinstance(v.get("session_id"), str) else []
        comparisons = []
        for auth in matches:
            ta, tb = times[source_key(auth["source"])], verified_time(access, view["issues"])
            comparisons.append({"authentication": auth["source"],
                                "user_matches": known_equal(v.get("user_id"), auth["values"].get("user_id")),
                                "source_ip_matches": known_equal(v.get("source_ip"), auth["values"].get("source_ip")),
                                "access_after_authentication": tb > ta if ta is not None and tb is not None else None})
        rid = v.get("resource_id")
        output["session_access_links"].append({"access": access["source"], "session_id": v.get("session_id"),
            "status": "MATCHED" if len(matches) == 1 else "AMBIGUOUS" if matches else "NOT_IN_BUNDLE",
            "comparisons": comparisons, "resource_id": rid,
            "resources": [r["source"] for r in resources.get(rid, [])] if isinstance(rid, str) else [],
            "resource_context_status": "PRESENT" if isinstance(rid, str) and resources.get(rid) else "NOT_IN_BUNDLE"})
    output["facts"].sort(key=lambda f: f["fact_id"])
    output["authentication_pairs"].sort(key=canonical)
    output["authentication_timeline"].sort(key=lambda r: (r["verified_utc"] is None,
                                                         instant(r["verified_utc"]) if r["verified_utc"] else None,
                                                         canonical(r["source"])))
    output["required_fact_ids"] = [f["fact_id"] for f in output["facts"] if f["required"]]
    return output
