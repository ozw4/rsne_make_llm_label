"""Strict artifact verification; no medical classification or inference."""
from __future__ import annotations

import copy
import json
from collections import Counter, defaultdict
from pathlib import Path

import run_labels as reader

ROOT = reader.ROOT
SOURCES = ("pilot50_support_v2", "pilot50_support_v2_cont1", "astra_remaining_support_v2")
RERUN = "astra_support_v2_first_study_rerun_v1"
FIRST = "1.2.826.0.1.3680043.8.498.10004873229099053869093324292195817260"
WORK = ROOT / "outputs/astra_support_v2_finalize_work"
CANON = ROOT / "outputs/astra_support_v2_canonical_v1"
TASK = ROOT / "prompts/codex_task_finalize_calibrate_freeze_astra_v2.md"
CONTRACTS = [reader.PROMPT, reader.SCHEMA, reader.CONFIG,
             ROOT / "scripts/run_labels.py", ROOT / "scripts/setup_worker.py",
             ROOT / "config/codex-version.txt",
             ROOT / "prompts/codex_report_labeling_instructions_v1.md", TASK]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return reader.decode(path.read_text(encoding="utf-8"))


def file_hash(path):
    return reader.sha256(path.read_bytes())


def report_hash(report):
    return reader.sha256(report["report"].encode("utf-8"))


def labels():
    return list(read_json(reader.SCHEMA)["properties"]["labels"]["properties"])


def reports_by_id(path=None):
    return {r["study_id"]: r for r in reader.read_reports(path or ROOT / "data/reports.jsonl")}


def files_under(root):
    require(root.is_dir() and not root.is_symlink(), "Invalid artifact root")
    result = []
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "Symlink in artifact")
        if path.is_file():
            require(path.name not in {"auth.json", "credentials.json"}, "Credential file in artifact")
            result.append(path)
    return result


def inventory(paths, relative_to=ROOT):
    return {str(p.relative_to(relative_to)): {"bytes": p.stat().st_size, "sha256": file_hash(p)}
            for p in sorted(paths)}


def save_once(path, value):
    if path.exists():
        require(read_json(path) == value, f"Existing checkpoint differs: {path.name}")
    else:
        reader.atomic_json(path, value)


def events(path):
    return [reader.decode(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def safe_relative(root, relative):
    p = Path(relative)
    require(not p.is_absolute() and ".." not in p.parts, "Unsafe provenance path")
    result = root / p
    require(result.resolve().is_relative_to(root.resolve()), "Provenance path escapes root")
    return result


def accepted(root, report, target_names):
    sid = report["study_id"]
    require(reader.completed(root, report, target_names), "Missing accepted generation")
    annotation_path = root / "annotations" / f"{sid}.json"
    receipt_path = root / "receipts" / f"{sid}.json"
    value, receipt = read_json(annotation_path), read_json(receipt_path)
    attempt = safe_relative(root, receipt["attempt_dir"])
    require(attempt.parent == root / "attempts" / sid, "Wrong study attempt reference")
    metadata = reader.summarize_events(attempt / "events.jsonl")
    require(not (attempt / "failure.json").exists(), "Accepted attempt marked failed")
    require(all(receipt[k] == metadata[k] for k in ("thread_id", "usage")), "Receipt/event mismatch")
    messages = [e["item"]["text"] for e in events(attempt / "events.jsonl")
                if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "agent_message"]
    require(len(messages) == 1, "Expected one final agent message")
    require(value == read_json(attempt / "response.json") == reader.decode(messages[0]),
            "Annotation/response/final message disagreement")
    return {"annotation": value, "receipt": receipt, "source_root": root.name,
            "annotation_sha256": file_hash(annotation_path), "receipt_sha256": file_hash(receipt_path)}


def audit_sources(roots, reports):
    selected, sessions, attempt_sessions, attempt_rows = {}, set(), set(), []
    counts, usage = {}, Counter()
    target_names = labels()
    for root in roots:
        ids = {p.stem for p in (root / "annotations").glob("*.json")}
        require(ids == {p.stem for p in (root / "receipts").glob("*.json")}, "Annotation/receipt ID mismatch")
        require(ids <= reports.keys(), "Generation IDs absent from source")
        counts[root.name] = len(ids)
        accepted_attempts = {}
        for sid in sorted(ids):
            item = accepted(root, reports[sid], target_names)
            thread = item["receipt"]["thread_id"]
            require(thread not in sessions, "Duplicate accepted session")
            sessions.add(thread)
            accepted_attempts[item["receipt"]["attempt_dir"]] = item["receipt"]
            if sid in selected:
                require(root.name == RERUN and sid == FIRST, "Unexplained duplicate generation ID")
            selected[sid] = item
        for attempt in sorted((root / "attempts").glob("*/attempt-*")):
            ev = events(attempt / "events.jsonl") if (attempt / "events.jsonl").exists() else []
            threads = [e["thread_id"] for e in ev if e.get("type") == "thread.started"]
            require(len(threads) <= 1, "Multiple threads in attempt")
            for thread in threads:
                require(isinstance(thread, str) and thread and thread not in attempt_sessions,
                        "Invalid or reused attempt session")
                attempt_sessions.add(thread)
            turns = [e for e in ev if e.get("type") == "turn.completed"]
            require(len(turns) <= 1, "Multiple completed turns")
            u = turns[0].get("usage") if turns else None
            if u is not None:
                require(all(type(u.get(k)) is int and u[k] >= 0 for k in reader.TOKEN_KEYS), "Invalid attempt usage")
                require(u["cached_input_tokens"] <= u["input_tokens"], "Invalid cached usage")
                usage.update({k: u[k] for k in reader.TOKEN_KEYS})
            relative = str(attempt.relative_to(root))
            receipt = accepted_attempts.get(relative)
            failure = read_json(attempt / "failure.json") if (attempt / "failure.json").exists() else None
            state = "accepted" if receipt else "failed" if failure else "interrupted"
            elapsed = receipt["elapsed_seconds"] if receipt else failure.get("elapsed_seconds") if failure else None
            attempt_rows.append({"run": root.name, "attempt_dir": relative, "status": state,
                                 "thread_id": threads[0] if threads else None, "usage": u,
                                 "elapsed_seconds": elapsed})
    require(sessions <= attempt_sessions, "Accepted session missing from attempts")
    usage["uncached_input_tokens"] = usage["input_tokens"] - usage["cached_input_tokens"]
    summary = {"accepted_by_run": counts, "selected_studies": len(selected),
               "accepted_sessions": len(sessions), "recorded_attempt_sessions": len(attempt_sessions),
               "attempts": len(attempt_rows), "attempt_statuses": dict(Counter(a["status"] for a in attempt_rows)),
               "known_usage": dict(usage), "usage_accounting_complete": all(a["usage"] is not None for a in attempt_rows),
               "summed_recorded_attempt_seconds": sum(a["elapsed_seconds"] or 0 for a in attempt_rows),
               "elapsed_accounting_complete": all(a["elapsed_seconds"] is not None for a in attempt_rows)}
    return selected, summary, attempt_rows


def duplicate_conflicts(reports, annotations):
    groups = defaultdict(list)
    for sid, report in reports.items():
        groups[report_hash(report)].append(sid)
    duplicates = {h: ids for h, ids in groups.items() if len(ids) > 1}
    conflicts, any_groups = [], 0
    for h, ids in sorted(duplicates.items()):
        require(len({reports[s]["report"] for s in ids}) == 1, "Report hash collision")
        cells = [annotations[s]["labels"] for s in ids]
        any_groups += any(v != cells[0] for v in cells[1:])
        different = [t for t in labels() if len({(v[t]["state"], v[t]["support_level"]) for v in cells}) > 1]
        if different:
            conflicts.append({"report_sha256": h, "study_ids": ids, "targets": different,
                              "report": reports[ids[0]]["report"],
                              "cells": {s: {t: annotations[s]["labels"][t] for t in different} for s in ids}})
    return {"duplicate_groups": len(duplicates), "duplicate_studies": sum(map(len, duplicates.values())),
            "any_annotation_difference_groups": any_groups, "state_support_conflict_groups": len(conflicts),
            "conflict_studies": sum(len(c["study_ids"]) for c in conflicts),
            "conflicting_hash_target_pairs": sum(len(c["targets"]) for c in conflicts)}, conflicts


def apply_decisions(original, report, original_hash, decisions):
    value = copy.deepcopy(original)
    seen = set()
    for decision in decisions:
        target = decision["target"]
        require(target not in seen, "Duplicate decision for target")
        seen.add(target)
        require(decision["study_id"] == report["study_id"] and decision["report_sha256"] == report_hash(report),
                "Decision source report mismatch")
        require(decision["source_annotation_sha256"] == original_hash, "Decision annotation hash mismatch")
        require(value["labels"][target] == decision["before"], "Decision before-value mismatch")
        require(decision["before"] != decision["after"], "No-op decision")
        value["labels"][target] = copy.deepcopy(decision["after"])
    reader.validate_annotation(value, report, labels())
    return value
