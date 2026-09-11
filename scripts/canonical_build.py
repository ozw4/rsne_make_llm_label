"""Assemble and audit a derived canonical artifact from reviewed decisions."""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import canonical_audit as a
import run_labels as reader


def put_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        a.require(path.read_bytes() == data, f"Refusing to overwrite differing file: {path}")
    else:
        with path.open("xb") as stream:
            stream.write(data)


def put_json(path, value):
    put_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode())


def copy_file(source, destination):
    a.require(source.is_file() and not source.is_symlink(), "Unsafe source copy")
    put_bytes(destination, source.read_bytes())


def source_immutability():
    baseline = a.read_json(a.WORK / "source_inventory.json")
    roots = [a.ROOT / "outputs" / n for n in a.SOURCES]
    current = a.inventory([p for r in roots for p in a.files_under(r)] + a.CONTRACTS + [a.ROOT / "data/reports.jsonl"])
    a.require(current == baseline, "Original source inventory changed")
    return len(baseline)


def decisions_by_study(path):
    rows = a.events(path)
    a.require(len({r["decision_id"] for r in rows}) == len(rows), "Duplicate decision ID")
    result = defaultdict(list)
    for row in rows:
        result[row["study_id"]].append(row)
    return result


def derived_receipt(sid, item, final_path, decisions, policy_hash):
    source = Path("generation_sources") / item["source_root"]
    return {"receipt_type": "derived_canonical_validation_v1", "status": "validated",
            "study_id": sid, "report_sha256": item["receipt"]["report_sha256"],
            "annotation_sha256": a.file_hash(final_path),
            "derivation": "adjudicated" if decisions else "copied",
            "source_annotation": str(source / "annotations" / f"{sid}.json"),
            "source_annotation_sha256": item["annotation_sha256"],
            "source_receipt": str(source / "receipts" / f"{sid}.json"),
            "source_receipt_sha256": item["receipt_sha256"],
            "source_manifest": str(source / "manifest.json"),
            "source_generation_thread_id": item["receipt"]["thread_id"],
            "decision_ids": [d["decision_id"] for d in decisions],
            "adjudication_policy_sha256": policy_hash if decisions else None}


def check_decision_scope(reports, selected, decisions, policy_hash):
    _, conflicts = a.duplicate_conflicts(reports, {sid: v["annotation"] for sid, v in selected.items()})
    allowed = {(sid, target) for c in conflicts for sid in c["study_ids"] for target in c["targets"]}
    for sid, rows in decisions.items():
        for row in rows:
            a.require((sid, row["target"]) in allowed, "Decision outside duplicate conflict scope")
            a.require(row["policy_sha256"] == policy_hash and row["gold_used"] is False, "Invalid decision policy/provenance")


def assemble():
    a.require(not (a.CANON / "FROZEN").exists(), "Artifact already frozen")
    source_immutability()
    reports = a.reports_by_id()
    roots = [a.ROOT / "outputs" / n for n in (*a.SOURCES, a.RERUN)]
    selected, summary, attempts = a.audit_sources(roots, reports)
    a.require(selected.keys() == reports.keys() and len(selected) == 4407, "Incomplete selected cohort")
    policy_hash = a.file_hash(a.WORK / "policy.md")
    decisions = decisions_by_study(a.WORK / "decisions.jsonl")
    check_decision_scope(reports, selected, decisions, policy_hash)
    identity = {"version": "astra_support_v2_canonical_v1", "source_inventory_sha256": a.file_hash(a.WORK / "source_inventory.json"),
                "replacement_manifest_sha256": a.file_hash(roots[-1] / "manifest.json"),
                "replacement_annotation_sha256": selected[a.FIRST]["annotation_sha256"],
                "policy_sha256": policy_hash, "decisions_sha256": a.file_hash(a.WORK / "decisions.jsonl")}
    if a.CANON.exists():
        a.require((a.CANON / "build_identity.json").exists(), "Unrecognized canonical directory")
    put_json(a.CANON / "build_identity.json", identity)
    for root in roots:
        for path in a.files_under(root):
            copy_file(path, a.CANON / "generation_sources" / root.name / path.relative_to(root))
    copy_file(a.ROOT / "data/reports.jsonl", a.CANON / "source/reports.jsonl")
    for path in a.CONTRACTS:
        copy_file(path, a.CANON / "contracts" / path.relative_to(a.ROOT))
    for name in ("policy.md", "decisions.jsonl", "resolutions.json"):
        copy_file(a.WORK / name, a.CANON / "adjudication" / name)
    for name in ("source_inventory.json", "preflight.json", "first_study_launch.json", "first_study_outcome.json", "duplicate_conflicts.json"):
        copy_file(a.WORK / name, a.CANON / "audit" / name)
    changes = []
    for sid, item in selected.items():
        rows = decisions.get(sid, [])
        value = a.apply_decisions(item["annotation"], reports[sid], item["annotation_sha256"], rows)
        final_path = a.CANON / "annotations" / f"{sid}.json"
        if rows:
            put_json(final_path, value)
        else:
            copy_file(a.ROOT / "outputs" / item["source_root"] / "annotations" / f"{sid}.json", final_path)
        put_json(a.CANON / "receipts" / f"{sid}.json", derived_receipt(sid, item, final_path, rows, policy_hash))
        changes.extend(dict(d, canonical_annotation_sha256=a.file_hash(final_path)) for d in rows)
    put_bytes(a.CANON / "adjudication/changes.jsonl", "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in changes).encode())
    old = a.read_json(roots[0] / "annotations" / f"{a.FIRST}.json")
    replacement_changes = [{"target": t, "before": old["labels"][t], "after": selected[a.FIRST]["annotation"]["labels"][t]}
                           for t in a.labels() if old["labels"][t] != selected[a.FIRST]["annotation"]["labels"][t]]
    manifests = {r.name: a.read_json(r / "manifest.json") for r in roots}
    a.require(manifests[a.SOURCES[1]] == manifests[a.SOURCES[2]] == manifests[a.RERUN], "Selected generation settings differ")
    put_json(a.CANON / "provenance.json", {"artifact_type": "report_derived_with_explicit_adjudication",
             "source_manifests": manifests, "historical_configuration_homogeneous": False,
             "selected_generation_configuration_homogeneous": True,
             "superseded": {"study_id": a.FIRST, "old_run": a.SOURCES[0], "selected_run": a.RERUN,
                            "reason": "Explicitly disabled plugins/apps in final reader configuration",
                            "annotation_cell_changes": replacement_changes},
             "adjudication_policy": "adjudication/policy.md", "changed_cells": len(changes),
             "changed_studies": len(decisions), "gold_used_for_adjudication": False,
             "medical_accuracy_certified": False})
    put_json(a.CANON / "audit/generation_audit.json", summary)
    put_json(a.CANON / "audit/attempts.json", attempts)
    return validate()


def evidence_candidates(annotations):
    # Equality screening only; this does not classify or correct medical content.
    groups = defaultdict(list)
    for sid, ann in annotations.items():
        for target, cell in ann["labels"].items():
            for span in set(cell["evidence"]):
                groups[(target, span)].append({"study_id": sid, "state": cell["state"], "support_level": cell["support_level"]})
    return [{"target": target, "evidence": span, "members": rows,
             "status": "context_dependent_screening_candidate_not_adjudicated"}
            for (target, span), rows in sorted(groups.items())
            if len({(r["state"], r["support_level"]) for r in rows}) > 1]


def validate():
    reports = a.reports_by_id(a.CANON / "source/reports.jsonl")
    a.require(a.file_hash(a.CANON / "source/reports.jsonl") == a.file_hash(a.ROOT / "data/reports.jsonl"), "Source copy differs")
    original_files = source_immutability()
    roots = [a.CANON / "generation_sources" / n for n in (*a.SOURCES, a.RERUN)]
    for root in roots:
        source = a.ROOT / "outputs" / root.name
        a.require(a.inventory(a.files_under(root), root) == a.inventory(a.files_under(source), source), "Generation copy differs")
    selected, generation, _ = a.audit_sources(roots, reports)
    annotations = {p.stem: a.read_json(p) for p in (a.CANON / "annotations").glob("*.json")}
    receipts = {p.stem: a.read_json(p) for p in (a.CANON / "receipts").glob("*.json")}
    a.require(len(reports) == 4407 and annotations.keys() == receipts.keys() == selected.keys() == reports.keys(), "Canonical coverage mismatch")
    decisions = decisions_by_study(a.CANON / "adjudication/decisions.jsonl")
    policy_hash = a.file_hash(a.CANON / "adjudication/policy.md")
    check_decision_scope(reports, selected, decisions, policy_hash)
    spans, state_counts, levels, by_target = 0, Counter(), Counter(), {}
    expected_changes = []
    for sid, ann in annotations.items():
        item, rows = selected[sid], decisions.get(sid, [])
        reader.validate_annotation(ann, reports[sid], a.labels())
        expected = a.apply_decisions(item["annotation"], reports[sid], item["annotation_sha256"], rows)
        a.require(ann == expected, "Unlisted annotation change")
        final = a.CANON / "annotations" / f"{sid}.json"
        a.require(receipts[sid] == derived_receipt(sid, item, final, rows, policy_hash), "Derived receipt mismatch")
        if not rows:
            a.require(a.file_hash(final) == item["annotation_sha256"], "Unchanged annotation bytes differ")
        expected_changes.extend(dict(d, canonical_annotation_sha256=a.file_hash(final)) for d in rows)
        for target, cell in ann["labels"].items():
            spans += len(cell["evidence"])
            state_counts[cell["state"]] += 1
            levels[str(cell["support_level"])] += 1
            by_target.setdefault(target, Counter())[cell["state"]] += 1
    actual_changes = a.events(a.CANON / "adjudication/changes.jsonl")
    a.require(sorted(actual_changes, key=lambda d: d["decision_id"]) == sorted(expected_changes, key=lambda d: d["decision_id"]), "Change ledger mismatch")
    duplicate_summary, conflicts = a.duplicate_conflicts(reports, annotations)
    a.require(not conflicts, "Unresolved duplicate state/support conflict")
    candidates = evidence_candidates(annotations)
    summary = {"status": "passed", "studies": len(annotations), "target_cells": len(annotations) * 12,
               "verbatim_spans_checked": spans, "original_files_unchanged": original_files,
               "annotation_response_event_agreement": "passed_for_all_accepted_generation_results",
               "source_and_generation_copy_hashes": "passed", "derived_receipts_and_decisions": "passed",
               "duplicates_after": duplicate_summary, "changed_cells": len(actual_changes),
               "changed_studies": len(decisions), "same_evidence_screening_groups": len(candidates),
               "generation": generation, "state_counts": dict(state_counts), "support_level_counts": dict(levels),
               "states_by_target": {t: dict(c) for t, c in by_target.items()},
               "limitations": ["Medical accuracy is not certified.", "Same-evidence candidates can reflect different report contexts and were not adjudicated.",
                               "Historical interrupted-attempt usage and elapsed time are incomplete.",
                               "Superseded first-run context cannot be established retroactively; retained in provenance."]}
    if not (a.CANON / "FROZEN").exists():
        put_json(a.CANON / "audit/full_validation.json", summary)
        put_json(a.CANON / "audit/same_evidence_candidates.json", candidates)
        put_json(a.CANON / "labeling_summary.json", summary)
        text = ("# Canonical artifact validation\n\n"
                f"PASS: {len(annotations):,} studies, {len(annotations)*12:,} cells, {spans:,} verbatim evidence spans.\n\n"
                f"All original files ({original_files:,}) remain unchanged. All generation copies, original receipts, final responses, events, report hashes, and derived receipts validate.\n\n"
                f"Duplicate conflicts: 17 hash-target pairs across 12 studies reviewed; {len(actual_changes)} cells in {len(decisions)} studies changed. No state/support conflicts remain. "
                f"All-source duplicates: {duplicate_summary['duplicate_groups']} groups / {duplicate_summary['duplicate_studies']} studies; the older 45/174 count covered only the remaining-run archive.\n\n"
                f"First study: one fresh final-configuration inference succeeded; its annotation matches the superseded annotation. Selected generation settings are homogeneous. Original settings remain documented.\n\n"
                f"{len(candidates)} same-evidence screening groups are retained for contextual review, without automatic correction. This audit is structural and provenance validation, not a medical-accuracy certification.\n\n"
                "Known usage includes failed/superseded attempts once each. Historical interrupted usage/time are unavailable, so known totals are lower bounds.\n")
        put_bytes(a.CANON / "audit/report.md", text.encode())
    return summary


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["assemble", "validate"])
    args = parser.parse_args()
    result = assemble() if args.action == "assemble" else validate()
    print(json.dumps({k: result[k] for k in ("status", "studies", "target_cells", "changed_cells", "changed_studies", "duplicates_after")}))
