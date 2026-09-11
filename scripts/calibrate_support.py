"""Post-adjudication gold evaluation and pooled monotone calibration, offline."""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import Counter, defaultdict
from pathlib import Path

import canonical_audit as a
from canonical_build import put_bytes, put_json

FIXED = [0.05, 0.25, 0.50, 0.75, 0.95]
SPEC = {"version": "pooled-support-calibration-v1", "fixed_mapping": FIXED,
        "prior_strength": 10, "method": "weighted PAVA on prior-smoothed level rates",
        "clip_epsilon": 1e-6, "minimum_brier_improvement": 0.005,
        "require_log_loss_improvement": True, "cross_validation": "leave-one-study-out",
        "duplicate_gold_check": "also require leave-one-exact-report-group-out improvement when applicable",
        "weight_formula": "abs(2*p-1)*n/(n+10)", "gold_training_weight": 0.0,
        "null_soft_target": 0.5, "null_training_weight": 0.0,
        "weights_are_heuristic": True, "target_specific_models": False}


def load_gold(path, reports, targets, expected=58):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        header = reader.fieldnames
        a.require(header is not None and len(header) == len(set(header)), "Invalid gold CSV header")
        a.require(set(["StudyInstanceUID", *targets]) <= set(header), "Missing gold columns")
        rows = list(reader)
    seen, cohort, empty = set(), [], 0
    for row in rows:
        a.require(None not in row and all(v is not None for v in row.values()), "Malformed CSV row")
        sid = row["StudyInstanceUID"]
        a.require(sid and sid not in seen, "Duplicate/empty gold source ID")
        seen.add(sid)
        a.require(sid in reports, "Gold source ID missing from reports")
        if "Report" in row:
            a.require(row["Report"] == reports[sid]["report"], "CSV report differs from source")
        values = [row[t].strip() for t in targets]
        if not any(values):
            empty += 1
            continue
        a.require(all(v in {"0", "1", "0.0", "1.0"} for v in values), "Incomplete/nonbinary gold row")
        cohort.append({"study_id": sid, "report_sha256": a.report_hash(reports[sid]),
                       "labels": {t: int(float(row[t])) for t in targets}})
    a.require(len(cohort) == expected, "Cannot identify the designated complete 58-study gold cohort")
    a.require(seen == reports.keys(), "Gold source table does not cover source report IDs exactly")
    return sorted(cohort, key=lambda r: r["study_id"]), {
        "csv_sha256": a.file_hash(path), "source_rows": len(rows), "all_targets_blank_rows": empty,
        "cohort_studies": len(cohort), "id_column": "StudyInstanceUID",
        "target_mapping": {t: t for t in targets},
        "cohort_derivation": "Exactly 58 complete binary-label rows; every other row has all 12 labels blank; all IDs and supplied report text match source. No partially labeled rows."}


def pava(values, weights):
    a.require(len(values) == len(weights) and all(w > 0 for w in weights), "Invalid PAVA input")
    blocks = []
    for i, (value, weight) in enumerate(zip(values, weights)):
        blocks.append([i, i + 1, value * weight, weight])
        while len(blocks) >= 2 and blocks[-2][2] / blocks[-2][3] > blocks[-1][2] / blocks[-1][3]:
            right, left = blocks.pop(), blocks.pop()
            blocks.append([left[0], right[1], left[2] + right[2], left[3] + right[3]])
    result = [0.0] * len(values)
    for start, end, total, weight in blocks:
        result[start:end] = [total / weight] * (end - start)
    return result


def fit_mapping(cells):
    counts, positives = [0] * 5, [0] * 5
    for row in cells:
        k = row["level"]
        if k is not None:
            counts[k] += 1
            positives[k] += row["gold"]
    strength = SPEC["prior_strength"]
    smoothed = [(positives[k] + strength * FIXED[k]) / (counts[k] + strength) for k in range(5)]
    return {"counts": counts, "positives": positives, "prior_smoothed": smoothed,
            "mapping": pava(smoothed, [n + strength for n in counts])}


def gold_cells(cohort, annotations):
    return [{"study_id": r["study_id"], "report_sha256": r["report_sha256"], "target": t,
             "level": annotations[r["study_id"]]["labels"][t]["support_level"], "gold": y}
            for r in cohort for t, y in r["labels"].items()]


def loss_metrics(predictions, key):
    if not predictions:
        return {"n": 0, "brier": None, "log_loss": None}
    squared, logs = 0.0, 0.0
    for row in predictions:
        p, y = row[key], row["gold"]
        squared += (p - y) ** 2
        p = min(1 - SPEC["clip_epsilon"], max(SPEC["clip_epsilon"], p))
        logs -= y * math.log(p) + (1 - y) * math.log1p(-p)
    return {"n": len(predictions), "brier": squared / len(predictions), "log_loss": logs / len(predictions)}


def cv_scores(predictions, targets):
    result = {}
    for method in ("fixed", "empirical"):
        by_study = defaultdict(list)
        for row in predictions:
            by_study[row["study_id"]].append(row)
        study_losses = [loss_metrics(rows, method) for rows in by_study.values()]
        result[method] = {"pooled": loss_metrics(predictions, method),
                          "per_target": {t: loss_metrics([r for r in predictions if r["target"] == t], method) for t in targets},
                          "study_balanced": {"studies_with_nonnull_cells": len(study_losses),
                              **{k: sum(r[k] for r in study_losses) / len(study_losses) if study_losses else None for k in ("brier", "log_loss")}}}
    return result


def cross_validate(cells, targets, group_key="study_id"):
    predictions, folds = [], []
    for held in sorted({r[group_key] for r in cells}):
        train = [r for r in cells if r[group_key] != held]
        test = [r for r in cells if r[group_key] == held]
        fitted = fit_mapping(train)
        held_ids = sorted({r["study_id"] for r in test})
        a.require(not set(held_ids) & {r["study_id"] for r in train}, "CV study leakage")
        folds.append({"held_out_group": held, "held_out_study_ids": held_ids,
                      "training_study_ids": sorted({r["study_id"] for r in train}), **fitted})
        for row in test:
            if row["level"] is not None:
                predictions.append(dict(row, fold=held, fixed=FIXED[row["level"]], empirical=fitted["mapping"][row["level"]]))
    return {"group_key": group_key, "folds": folds, "predictions": predictions, "scores": cv_scores(predictions, targets)}


def improvement_passes(cv):
    fixed, empirical = [cv["scores"][key]["pooled"] for key in ("fixed", "empirical")]
    return (fixed["n"] > 0 and fixed["brier"] - empirical["brier"] >= SPEC["minimum_brier_improvement"]
            and empirical["log_loss"] < fixed["log_loss"])


def wilson(positive, n):
    if not n:
        return [None, None]
    z = 1.959963984540054
    p = positive / n
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [max(0.0, center - half), min(1.0, center + half)]


def divide(n, d):
    return n / d if d else None


def threshold_metrics(rows, threshold, extremes=False):
    included = [r for r in rows if r["level"] is not None and (not extremes or r["level"] in {0, 4})]
    tp = sum(r["gold"] == 1 and r["level"] >= threshold for r in included)
    tn = sum(r["gold"] == 0 and r["level"] < threshold for r in included)
    fp = sum(r["gold"] == 0 and r["level"] >= threshold for r in included)
    fn = sum(r["gold"] == 1 and r["level"] < threshold for r in included)
    sensitivity, specificity = divide(tp, tp + fn), divide(tn, tn + fp)
    return {"n_total": len(rows), "n_evaluated": len(included), "abstained": len(rows) - len(included),
            "coverage": divide(len(included), len(rows)), "tp": tp, "tn": tn, "fp": fp, "fn": fn,
            "sensitivity": sensitivity, "specificity": specificity, "precision": divide(tp, tp + fp),
            "f1": divide(2 * tp, 2 * tp + fp + fn),
            "balanced_accuracy": (sensitivity + specificity) / 2 if sensitivity is not None and specificity is not None else None}


def evaluation(cells, targets):
    result = {}
    for target in ["pooled", *targets]:
        rows = cells if target == "pooled" else [r for r in cells if r["target"] == target]
        rates = {}
        for k in [0, 1, 2, 3, 4, None]:
            subgroup = [r for r in rows if r["level"] == k]
            positive = sum(r["gold"] for r in subgroup)
            rates["null" if k is None else str(k)] = {"n": len(subgroup), "positives": positive,
                "positive_rate": divide(positive, len(subgroup)), "wilson_95": wilson(positive, len(subgroup))}
        result[target] = {"level_rates": rates,
                          "thresholds": {str(k): threshold_metrics(rows, k) for k in range(1, 5)},
                          "positive_vs_negative_only": threshold_metrics(rows, 4, True)}
    return result


def probability_weight(level, mapping, counts, eligible):
    if level is None:
        return 0.5, 0.0
    p = mapping[level]
    return p, abs(2 * p - 1) * counts[level] / (counts[level] + 10) if eligible else 0.0


def calculate(cohort, annotations, reports, targets):
    cells = gold_cells(cohort, annotations)
    cv = cross_validate(cells, targets)
    duplicate_gold = len({r["report_sha256"] for r in cohort}) != len(cohort)
    grouped = cross_validate(cells, targets, "report_sha256") if duplicate_gold else None
    fitted = fit_mapping(cells)
    choose_empirical = improvement_passes(cv) and (grouped is None or improvement_passes(grouped))
    mapping = fitted["mapping"] if choose_empirical else FIXED
    gold_ids = {r["study_id"] for r in cohort}
    gold_hashes = {r["report_sha256"] for r in cohort}
    overlaps = sorted(s for s, r in reports.items() if s not in gold_ids and a.report_hash(r) in gold_hashes)
    summary = {"status": "passed", "cohort_studies": len(cohort), "gold_cells": len(cells),
               "nonnull_gold_cells": sum(r["level"] is not None for r in cells),
               "selected_mapping": "empirical" if choose_empirical else "fixed", "mapping": mapping,
               "fixed_mapping": FIXED, "full_cohort_empirical_fit": fitted,
               "leave_one_study_out_scores": cv["scores"], "exact_report_duplicates_in_gold": duplicate_gold,
               "leave_one_report_group_out_scores": grouped["scores"] if grouped else None,
               "eligible_studies_with_gold_report_hash_overlap": overlaps,
               "weights_are_heuristic": True, "gold_training_eligible": False,
               "limitations": ["Small 58-study image-gold agreement evaluation, not a medical-accuracy certification.",
                               "Pooled cells within a study are dependent; pooled Wilson intervals are descriptive.",
                               "CV compares candidate mappings; the selected mapping has no independent test-set evaluation.",
                               "Training weights are heuristic and have not been validated through training."]}
    return summary, cv, grouped, evaluation(cells, targets)


def run(artifact, verify=False):
    a.require(a.read_json(artifact / "audit/full_validation.json")["status"] == "passed", "Phase 4 required")
    a.require(verify or not (artifact / "FROZEN").exists(), "Artifact frozen")
    annotations = {p.stem: a.read_json(p) for p in (artifact / "annotations").glob("*.json")}
    reports = a.reports_by_id(artifact / "source/reports.jsonl")
    targets = a.labels()
    lock = a.inventory(list((artifact / "annotations").glob("*.json")) + [artifact / "adjudication/decisions.jsonl"], artifact)
    destination = artifact / "calibration"
    if verify:
        a.require(a.read_json(destination / "annotation_lock.json") == lock, "Post-gold annotation changes")
        a.require(a.read_json(destination / "specification.json") == SPEC, "Calibration specification mismatch")
        cohort = a.read_json(destination / "gold_cohort.json")
    else:
        put_json(destination / "annotation_lock.json", lock)
        put_json(destination / "specification.json", SPEC)
        gold_path = Path(os.environ.get("RSNA_GOLD_CSV", str(a.ROOT / "data/train.csv"))).resolve()
        a.require(gold_path.is_relative_to(a.ROOT), "Gold outside working tree")
        cohort, provenance = load_gold(gold_path, reports, targets)
        put_json(destination / "gold_source_provenance.json", provenance)
        put_json(destination / "gold_cohort.json", cohort)
    a.require(len(cohort) == 58 and len({r["study_id"] for r in cohort}) == 58, "Invalid saved cohort")
    for row in cohort:
        a.require(row["report_sha256"] == a.report_hash(reports[row["study_id"]]), "Gold report hash mismatch")
        a.require(list(row["labels"]) == targets and all(type(v) is int and v in {0, 1} for v in row["labels"].values()), "Invalid saved gold labels")
    summary, cv, grouped, metrics = calculate(cohort, annotations, reports, targets)
    products = {"summary.json": summary, "leave_one_study_out.json": cv, "metrics.json": metrics}
    if grouped:
        products["leave_one_report_group_out.json"] = grouped
    for name, value in products.items():
        if verify:
            a.require(a.read_json(destination / name) == value, "Calibration reproduction mismatch")
        else:
            put_json(destination / name, value)
    gold_ids = {r["study_id"] for r in cohort}
    derived = []
    for sid, ann in sorted(annotations.items()):
        eligible = sid not in gold_ids
        for target, cell in ann["labels"].items():
            p, weight = probability_weight(cell["support_level"], summary["mapping"], summary["full_cohort_empirical_fit"]["counts"], eligible)
            derived.append({"study_id": sid, "target": target, "state": cell["state"], "support_level": cell["support_level"],
                            "soft_target": p, "training_weight": weight, "training_eligible": eligible,
                            "mapping_version": SPEC["version"] + "/" + summary["selected_mapping"],
                            "annotation": f"annotations/{sid}.json", "receipt": f"receipts/{sid}.json"})
    data = "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in derived).encode()
    if verify:
        a.require((destination / "derived_targets.jsonl").read_bytes() == data, "Derived recommendations mismatch")
    else:
        put_bytes(destination / "derived_targets.jsonl", data)
        fixed, empirical = [summary["leave_one_study_out_scores"][k]["pooled"] for k in ("fixed", "empirical")]
        report = ("# Gold agreement and support calibration\n\n"
                  f"58 studies / 696 gold cells; {summary['nonnull_gold_cells']} non-null support cells scored in 58 leave-one-study-out folds.\n\n"
                  "| Mapping | OOF Brier | OOF log loss |\n|---|---:|---:|\n"
                  f"| Fixed | {fixed['brier']:.6f} | {fixed['log_loss']:.6f} |\n"
                  f"| Prior-smoothed isotonic | {empirical['brier']:.6f} | {empirical['log_loss']:.6f} |\n\n"
                  f"Selected: **{summary['selected_mapping']}**. Mapping levels 0–4: `{summary['mapping']}`. "
                  "Selection required at least 0.005 Brier improvement and lower log loss. All fitted fold counts exclude held-out studies.\n\n"
                  f"Exact-report duplicates within gold: {duplicate_gold_text(summary)}. Eligible studies sharing exact gold report text: {len(summary['eligible_studies_with_gold_report_hash_overlap'])}.\n\n"
                  "Every gold study is training-ineligible and has zero weight. Unmentioned/null cells use placeholder probability 0.5 and zero weight. Other weights use the predeclared heuristic in specification.json; these weights are not validated training optima.\n\n"
                  "metrics.json records counts, denominators, Wilson intervals, target-level and pooled threshold metrics and abstention coverage. Undefined metrics are null. Pooled cells are dependent. These results describe agreement with image gold and do not establish clinical accuracy or image-model performance.\n")
        put_bytes(destination / "report.md", report.encode())
    return summary


def duplicate_gold_text(summary):
    return "present; grouped validation also required" if summary["exact_report_duplicates_in_gold"] else "none"


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, default=a.CANON)
    parser.add_argument("--verify", action="store_true", help="Reproduce from saved cohort; no raw gold CSV needed")
    args = parser.parse_args()
    result = run(args.artifact.resolve(), args.verify)
    print(json.dumps({k: result[k] for k in ("status", "cohort_studies", "nonnull_gold_cells", "selected_mapping", "mapping", "exact_report_duplicates_in_gold")}))
