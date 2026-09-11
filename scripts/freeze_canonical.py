"""Gate, freeze, package, and independently verify the canonical artifact."""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True

import canonical_audit as a
import canonical_build as build
import calibrate_support as calibration


def require_gates(validation, calibrated, tests, syntax):
    a.require(validation.get("status") == "passed" and validation.get("studies") == 4407
              and validation.get("target_cells") == 52884, "Full annotation audit gate failed")
    a.require(validation.get("duplicates_after", {}).get("state_support_conflict_groups") == 0, "Unresolved duplicate conflicts")
    a.require(calibrated.get("status") == "passed" and calibrated.get("cohort_studies") == 58, "Gold calibration gate failed")
    a.require(tests.get("returncode") == 0 and tests.get("tests_run", 0) > 0, "Offline test gate failed")
    a.require(syntax.get("returncode") == 0, "Syntax gate failed")


def run_tests():
    import unittest
    suite = unittest.defaultTestLoader.discover(str(a.ROOT / "tests"))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    tests = {"command": "python -m unittest discover -s tests -v", "tests_run": result.testsRun,
             "failures": len(result.failures), "errors": len(result.errors),
             "skipped": len(result.skipped), "returncode": 0 if result.wasSuccessful() else 1,
             "log": stream.getvalue()}
    syntax_process = subprocess.run([sys.executable, "-m", "compileall", "-q", "scripts", "tests"],
                                    cwd=a.ROOT, capture_output=True, text=True)
    syntax = {"command": "python -m compileall -q scripts tests", "returncode": syntax_process.returncode,
              "stdout": syntax_process.stdout, "stderr": syntax_process.stderr}
    return tests, syntax


def verify_manifest(artifact):
    manifest = a.read_json(artifact / "manifest.json")
    frozen = a.read_json(artifact / "FROZEN")
    a.require(frozen["manifest_sha256"] == a.file_hash(artifact / "manifest.json"), "Frozen manifest hash mismatch")
    expected = manifest["payload"]
    actual = a.inventory([p for p in a.files_under(artifact) if p not in {artifact / "manifest.json", artifact / "FROZEN"}], artifact)
    a.require(actual == expected, "Frozen payload inventory mismatch")
    return manifest


def portable_validation(artifact):
    """Verify supplied source text, generation chains, decisions and calibration."""
    reports = a.reports_by_id(artifact / "source/reports.jsonl")
    selected, generation, _ = a.audit_sources([artifact / "generation_sources" / n for n in (*a.SOURCES, a.RERUN)], reports)
    annotations = {p.stem: a.read_json(p) for p in (artifact / "annotations").glob("*.json")}
    receipt_ids = {p.stem for p in (artifact / "receipts").glob("*.json")}
    a.require(len(reports) == 4407 and selected.keys() == annotations.keys() == reports.keys() == receipt_ids, "Portable cohort mismatch")
    decisions = build.decisions_by_study(artifact / "adjudication/decisions.jsonl")
    policy_hash = a.file_hash(artifact / "adjudication/policy.md")
    build.check_decision_scope(reports, selected, decisions, policy_hash)
    for sid, ann in annotations.items():
        item, rows = selected[sid], decisions.get(sid, [])
        expected = a.apply_decisions(item["annotation"], reports[sid], item["annotation_sha256"], rows)
        a.require(ann == expected, "Portable adjudication mismatch")
        receipt = build.derived_receipt(sid, item, artifact / "annotations" / f"{sid}.json", rows, policy_hash)
        a.require(a.read_json(artifact / "receipts" / f"{sid}.json") == receipt, "Portable derived receipt mismatch")
    _, conflicts = a.duplicate_conflicts(reports, annotations)
    a.require(not conflicts, "Portable duplicate conflicts")
    calibration.run(artifact, verify=True)
    return {"status": "passed", "studies": len(annotations), "accepted_generation_sessions": generation["accepted_sessions"],
            "checks": ["report_source", "generation_events_receipts_responses", "derived_annotations_receipts", "decision_replay", "duplicate_consistency", "calibration_reproduction"]}


def prepare_freeze(artifact):
    a.require(artifact == a.CANON, "Only the designated canonical destination can be frozen")
    a.require(not (artifact / "FROZEN").exists(), "Already frozen")
    validation = build.validate()
    calibrated = calibration.run(artifact, verify=True)
    tests, syntax = run_tests()
    require_gates(validation, calibrated, tests, syntax)
    build.put_json(artifact / "audit/offline_tests.json", tests)
    build.put_json(artifact / "audit/syntax_check.json", syntax)
    snapshots = sorted((a.ROOT / "scripts").glob("*.py")) + sorted((a.ROOT / "tests").glob("*.py")) + a.CONTRACTS
    # Include devcontainer files for offline test reproduction, without credentials.
    snapshots += [p for p in (a.ROOT / ".devcontainer").rglob("*") if p.is_file()]
    for source in snapshots:
        build.copy_file(source, artifact / "reproduction" / source.relative_to(a.ROOT))
    reproduction = ("# Offline artifact reproduction\n\n"
                    "Use Python 3.12+ and the bundled standard-library scripts. No model calls, authentication, original training repository, or raw train.csv are required for verification.\n\n"
                    "From the extracted artifact root:\n\n```bash\n"
                    "python -B reproduction/scripts/freeze_canonical.py verify --artifact .\n"
                    "python -B reproduction/scripts/calibrate_support.py --verify --artifact .\n```\n\n"
                    "Use -B to avoid adding bytecode caches to the frozen directory. Verification reads source/reports.jsonl and calibration/gold_cohort.json, checks generation events and derived receipts, replays the explicit decision ledger, and recomputes every calibration output.\n\n"
                    "Do not run generation scripts from this artifact. Supplied run manifests record historical absolute worker paths as provenance; portable verification does not read those paths.\n")
    build.put_bytes(artifact / "reproduction/README.md", reproduction.encode())
    verification = portable_validation(artifact)
    build.put_json(artifact / "audit/portable_verification.json", verification)
    generation = validation["generation"]
    final_summary = {"phases_1_to_5": "passed", "freeze_gates": "passed", "studies": 4407, "cells": 52884,
                     "additional_inference_calls": 1, "adjudicated_cells": validation["changed_cells"],
                     "adjudicated_studies": validation["changed_studies"], "gold_studies": 58,
                     "selected_mapping": calibrated["selected_mapping"], "tests_run": tests["tests_run"],
                     "usage": generation["known_usage"], "usage_accounting_complete": generation["usage_accounting_complete"],
                     "limitations": validation["limitations"] + calibrated["limitations"],
                     "gold_report_overlap_eligible_studies": calibrated["eligible_studies_with_gold_report_hash_overlap"]}
    build.put_json(artifact / "audit/final_summary.json", final_summary)
    build.source_immutability()
    payload = a.inventory([p for p in a.files_under(artifact) if p not in {artifact / "manifest.json", artifact / "FROZEN"}], artifact)
    manifest = {"artifact_version": "astra_support_v2_canonical_v1", "artifact_type": "derived_adjudicated_report_labels",
                "counts": {"studies": validation["studies"], "target_cells": validation["target_cells"],
                           "gold_studies": calibrated["cohort_studies"], "adjudicated_cells": validation["changed_cells"]},
                "generation_settings": a.read_json(artifact / "generation_sources" / a.RERUN / "manifest.json"),
                "build_identity": a.read_json(artifact / "build_identity.json"),
                "calibration_specification": calibration.SPEC, "selected_mapping": calibrated["selected_mapping"],
                "tests_run": tests["tests_run"], "all_required_gates": "passed",
                "limitations": final_summary["limitations"],
                "payload_hash_exclusions": ["manifest.json", "FROZEN"], "payload": payload}
    build.put_json(artifact / "manifest.json", manifest)
    a.require(a.inventory([artifact / p for p in payload], artifact) == payload, "Payload changed before freeze")
    build.put_json(artifact / "FROZEN", {"artifact_version": manifest["artifact_version"],
                   "manifest_sha256": a.file_hash(artifact / "manifest.json"),
                   "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
                   "passed_gates": ["audit/full_validation.json", "calibration/summary.json", "audit/offline_tests.json",
                                    "audit/syntax_check.json", "audit/portable_verification.json"]})
    verify_manifest(artifact)
    return final_summary


def verify_zip(path, artifact):
    expected = {str(p.relative_to(artifact)): a.file_hash(p) for p in a.files_under(artifact)}
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        a.require(len(names) == len(set(names)), "Duplicate ZIP paths")
        a.require(archive.testzip() is None, "ZIP CRC failure")
        members = {}
        for name in names:
            p = PurePosixPath(name)
            a.require(not p.is_absolute() and ".." not in p.parts and "\\" not in name and p.parts[0] == artifact.name,
                      "Unsafe ZIP member")
            relative = str(PurePosixPath(*p.parts[1:]))
            a.require(relative in expected, "Unexpected ZIP member")
            members[relative] = a.reader.sha256(archive.read(name))
        a.require(members == expected, "ZIP content differs from frozen payload")
    return {"status": "passed", "files": len(expected), "zip_sha256": a.file_hash(path), "zip_bytes": path.stat().st_size}


def package(artifact):
    verify_manifest(artifact)
    zip_path = artifact.with_suffix(".zip")
    if not zip_path.exists():
        with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for p in a.files_under(artifact):
                archive.write(p, arcname=str(Path(artifact.name) / p.relative_to(artifact)))
    verification = verify_zip(zip_path, artifact)
    checksum = zip_path.with_suffix(".zip.sha256")
    build.put_bytes(checksum, f"{verification['zip_sha256']}  {zip_path.name}\n".encode())
    a.require(checksum.read_text().split()[0] == a.file_hash(zip_path), "ZIP checksum mismatch")
    verify_manifest(artifact)
    if a.WORK.is_dir():
        build.put_json(a.WORK / "packaging_verification.json", verification)
    return verification


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["freeze", "verify", "package"])
    parser.add_argument("--artifact", type=Path, default=a.CANON)
    args = parser.parse_args()
    artifact = args.artifact.resolve()
    if args.action == "freeze":
        print(json.dumps(prepare_freeze(artifact)))
    elif args.action == "verify":
        verify_manifest(artifact)
        print(json.dumps(portable_validation(artifact)))
    else:
        print(json.dumps(package(artifact)))
