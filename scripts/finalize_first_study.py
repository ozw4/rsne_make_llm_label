"""One explicitly authorized fresh inference, guarded against repeat launches."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import canonical_audit as a
import run_labels as reader


def reserve_launch(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation is durable before any launch; never erase on failure.
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(record, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-once", action="store_true")
    args = parser.parse_args()
    reports = a.reports_by_id()
    roots = [a.ROOT / "outputs" / n for n in a.SOURCES]
    before = a.inventory([p for r in roots for p in a.files_under(r)] + a.CONTRACTS + [a.ROOT / "data/reports.jsonl"])
    a.save_once(a.WORK / "source_inventory.json", before)
    selected, summary, attempts = a.audit_sources(roots, reports)
    a.require(len(reports) == 4407 and selected.keys() == reports.keys(), "Incomplete source coverage")
    a.require(list(summary["accepted_by_run"].values()) == [1, 49, 4357], "Unexpected source counts")
    for n in a.SOURCES[1:]:
        a.require(a.read_json(a.ROOT / "outputs" / n / "manifest.json") == a.read_json(roots[-1] / "manifest.json"),
                  "Final run identities differ")
    worker, home = reader.default_paths()
    reader.check_worker(worker, home)
    input_path = a.WORK / "first_study.jsonl"
    if not input_path.exists():
        input_path.write_text(json.dumps(reports[a.FIRST], ensure_ascii=False) + "\n", encoding="utf-8")
    a.require(reader.read_reports(input_path) == [reports[a.FIRST]], "One-study input differs")
    run_args = argparse.Namespace(input=input_path, output_dir=a.ROOT / "outputs" / a.RERUN,
                                  model="gpt-6-astra", reasoning_effort="high", attempts=1,
                                  limit=1, timeout=300, dry_run=True)
    identity = reader.run_identity(run_args, worker, home)
    a.require(identity == a.read_json(roots[-1] / "manifest.json"), "Current generation identity differs")
    reader.run(run_args)
    a.save_once(a.WORK / "preflight.json", {"status": "passed", "source_audit": summary,
                                           "identity": identity, "timeout_seconds": 300})
    a.save_once(a.WORK / "original_attempts.json", attempts)
    if not args.execute_once:
        print(json.dumps({"preflight": "passed", "reports": len(reports), "inference_launched": False}))
        return
    marker = a.WORK / "first_study_launch.json"
    if marker.exists():
        a.require(a.read_json(marker)["identity"] == identity, "Prior launch settings differ")
        a.accepted(run_args.output_dir, reports[a.FIRST], a.labels())
        print(json.dumps({"first_study": "reused_validated_success", "new_inference_calls": 0}))
        return
    a.require(not run_args.output_dir.exists(), "Unrecorded prior rerun directory; no launch permitted")
    reserve_launch(marker, {"study_id": a.FIRST, "report_sha256": a.report_hash(reports[a.FIRST]),
                           "identity": identity, "max_inference_calls": 1,
                           "reserved_at": datetime.now(timezone.utc).isoformat()})
    run_args.dry_run = False
    try:
        reader.run(run_args)
        item = a.accepted(run_args.output_dir, reports[a.FIRST], a.labels())
        a.require(item["receipt"]["thread_id"] not in {v["receipt"]["thread_id"] for v in selected.values()},
                  "Replacement reused a session")
    except BaseException as exc:
        reader.atomic_json(a.WORK / "first_study_outcome.json", {"status": "failed_stop_no_retry", "error_type": type(exc).__name__})
        raise
    reader.atomic_json(a.WORK / "first_study_outcome.json", {"status": "passed", "receipt": item["receipt"]})
    print(json.dumps({"first_study": "passed", "new_inference_calls": 1, "usage": item["receipt"]["usage"]}))


if __name__ == "__main__":
    main()
