"""Collect an alert's evidence through Zetaris. No classifications or conclusions."""

from __future__ import annotations

import argparse
from pathlib import Path

if __package__:
    from .evidence_collector import (CollectionOptions, EvidenceCollector, ZetarisEvidenceSource,
                                    build_bundle, build_trace, serialise)
    from .evidence_queries import identifier
    from .zetaris_client import ROOT, ZetarisClient, ZetarisConfig, ZetarisError
else:
    from evidence_collector import (CollectionOptions, EvidenceCollector, ZetarisEvidenceSource,
                                   build_bundle, build_trace, serialise)
    from evidence_queries import identifier
    from zetaris_client import ROOT, ZetarisClient, ZetarisConfig, ZetarisError


def output_path(path: Path) -> Path:
    target = path.resolve()
    if (target.suffix != ".json" or any(target.is_relative_to(ROOT / directory)
                                      for directory in ("data", "evaluation", ".git"))):
        raise ValueError("Evidence output must be a JSON artifact outside data/, evaluation/ and .git/")
    if path.is_symlink() or (target.exists() and target.stat().st_nlink > 1):
        raise ValueError("Evidence output must not be a symlink or hardlink")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("alert_id")
    parser.add_argument("--output", type=Path, help="Default: artifacts/evidence/<alert_id>.json")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.local")
    parser.add_argument("--before-hours", type=int, default=24)
    parser.add_argument("--after-hours", type=int, default=1)
    parser.add_argument("--row-limit", type=int, default=200)
    parser.add_argument("--max-queries", type=int, default=80)
    args = parser.parse_args()
    try:
        identifier(args.alert_id)
        options = CollectionOptions(args.before_hours, args.after_hours, args.row_limit, args.max_queries)
        target = output_path(args.output or ROOT / "artifacts" / "evidence" / f"{args.alert_id}.json")
        trace_target = output_path(target.with_suffix(".trace.json"))
        config = ZetarisConfig.from_environment(args.env_file)
        snapshot = EvidenceCollector(ZetarisEvidenceSource(ZetarisClient(config)), options).collect(args.alert_id)
        bundle = build_bundle(snapshot)
        payload, trace = serialise(bundle), serialise(build_trace(snapshot))
        if config.api_key in payload or config.api_key in trace:
            raise ValueError("Output refused because it contains a configured credential")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8", newline="\n")
        trace_target.write_text(trace, encoding="utf-8", newline="\n")
    except ZetarisError as error:
        print(f"Collection failed: {type(error).__name__}")
        return 1
    except ValueError as error:
        print(f"Collection failed: {error}")
        return 1
    except OSError:
        print("Cannot write evidence output")
        return 1
    counts = ", ".join(f"{table}={len(rows)}" for table, rows in bundle["records"].items())
    print(f"Evidence collection: {bundle['collection_status']}; {len(bundle['queries'])} queries; {counts}")
    print(f"Bundle: {target}\nWire trace: {trace_target}")
    print("Evidence collected only; no incident assessment or conclusion was generated.")
    return 0 if bundle["collection_status"] == "collected" else 2


if __name__ == "__main__":
    raise SystemExit(main())
