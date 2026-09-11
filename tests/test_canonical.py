"""Synthetic tests for canonical derivation; never call the reader model."""
import copy
import csv
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import canonical_audit as a
import canonical_build as build
import calibrate_support as calibration
import freeze_canonical as freeze
from finalize_first_study import reserve_launch


class CanonicalTests(unittest.TestCase):
    def test_launch_reservation_survives_restart_and_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "launch.json"
            reserve_launch(path, {"status": "reserved"})
            original = path.read_bytes()
            with self.assertRaises(FileExistsError):
                reserve_launch(path, {"status": "retry"})
            self.assertEqual(path.read_bytes(), original)

    def test_decisions_require_original_hash_and_exact_before(self):
        report = {"study_id": "synthetic", "report": "Normal study."}
        value = {"study_id": "synthetic", "labels": {t: {"state": "not_mentioned", "support_level": None, "evidence": []} for t in a.labels()}}
        decision = {"study_id": "synthetic", "report_sha256": a.report_hash(report),
                    "source_annotation_sha256": "original", "target": "ACL",
                    "before": copy.deepcopy(value["labels"]["ACL"]),
                    "after": {"state": "negative", "support_level": 0, "evidence": ["Normal study."]}}
        output = a.apply_decisions(value, report, "original", [decision])
        self.assertEqual(output["labels"]["ACL"]["state"], "negative")
        self.assertEqual(value["labels"]["ACL"]["state"], "not_mentioned")
        with self.assertRaises(ValueError):
            a.apply_decisions(value, report, "changed", [decision])
        with self.assertRaises(ValueError):
            a.apply_decisions(output, report, "original", [decision])
        decision["after"]["evidence"] = ["Normal examination."]
        with self.assertRaises(ValueError):
            a.apply_decisions(value, report, "original", [decision])

    def test_provenance_cannot_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            for relative in ("../outside", "/outside"):
                with self.assertRaises(ValueError):
                    a.safe_relative(Path(temporary), relative)

    def test_copy_never_changes_source_or_overwrites_different_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, dest = Path(temporary) / "source", Path(temporary) / "dest"
            source.write_bytes(b'original\r\n')
            build.copy_file(source, dest)
            self.assertEqual(dest.read_bytes(), source.read_bytes())
            dest.write_bytes(b'different')
            with self.assertRaises(ValueError):
                build.copy_file(source, dest)
            self.assertEqual(source.read_bytes(), b'original\r\n')

    def test_derived_receipt_preserves_generation_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            final = Path(temporary) / "final.json"
            final.write_bytes(b'edited annotation')
            item = {"source_root": "source-run", "annotation_sha256": "original-hash",
                    "receipt_sha256": "receipt-hash", "receipt": {"report_sha256": "report-hash", "thread_id": "original-thread"}}
            receipt = build.derived_receipt("synthetic", item, final, [{"decision_id": "d1"}], "policy")
            self.assertEqual(receipt["derivation"], "adjudicated")
            self.assertEqual(receipt["source_annotation_sha256"], "original-hash")
            self.assertEqual(receipt["source_generation_thread_id"], "original-thread")
            self.assertNotEqual(receipt["annotation_sha256"], "original-hash")
            self.assertNotIn("usage", receipt)
            self.assertNotEqual(receipt["status"], "complete")


class CalibrationTests(unittest.TestCase):
    def test_weighted_pava_pools_violations(self):
        result = calibration.pava([0.1, 0.8, 0.2, 0.9], [1, 1, 3, 1])
        for actual, expected in zip(result, [0.1, 0.35, 0.35, 0.9]):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(calibration.fit_mapping([])["mapping"], calibration.FIXED)

    def test_held_out_gold_does_not_change_its_fitted_mapping(self):
        cells = [{"study_id": sid, "report_sha256": sid, "target": "ACL", "level": 2, "gold": y}
                 for sid, y in (("a", 0), ("b", 1), ("c", 0))]
        cv = calibration.cross_validate(cells, ["ACL"])
        changed = copy.deepcopy(cells)
        changed[0]["gold"] = 1
        altered = calibration.cross_validate(changed, ["ACL"])
        self.assertEqual(cv["folds"][0], altered["folds"][0])
        self.assertEqual(cv["folds"][0]["training_study_ids"], ["b", "c"])
        self.assertEqual(cv["scores"]["fixed"]["pooled"]["n"], 3)

    def test_grouped_cv_excludes_identical_reports(self):
        cells = [{"study_id": s, "report_sha256": h, "target": "ACL", "level": 4, "gold": y}
                 for s, h, y in (("a", "same", 1), ("b", "same", 0), ("c", "other", 1))]
        cv = calibration.cross_validate(cells, ["ACL"], "report_sha256")
        fold = next(f for f in cv["folds"] if f["held_out_group"] == "same")
        self.assertEqual(fold["held_out_study_ids"], ["a", "b"])
        self.assertEqual(fold["training_study_ids"], ["c"])
        self.assertEqual(sum(fold["counts"]), 1)

    def test_null_and_gold_weights_are_zero(self):
        self.assertEqual(calibration.probability_weight(None, calibration.FIXED, [20]*5, True), (0.5, 0.0))
        for k in range(5):
            self.assertEqual(calibration.probability_weight(k, calibration.FIXED, [20]*5, False)[1], 0.0)
        self.assertAlmostEqual(calibration.probability_weight(4, calibration.FIXED, [20]*5, True)[1], 0.6)

    def test_gold_rejects_partial_labels_duplicates_and_wrong_cohort_size(self):
        reports = {s: {"study_id": s, "report": "Synthetic."} for s in ("a", "b")}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "gold.csv"
            def write(rows):
                with path.open("w", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(["StudyInstanceUID", "ACL", "MCL"])
                    writer.writerows(rows)
            write([["a", "1", "0"], ["b", "", ""]])
            cohort, provenance = calibration.load_gold(path, reports, ["ACL", "MCL"], expected=1)
            self.assertEqual([r["study_id"] for r in cohort], ["a"])
            self.assertEqual(provenance["all_targets_blank_rows"], 1)
            for rows in ([["a", "1", ""], ["b", "", ""]], [["a", "1", "0"], ["a", "0", "1"]], [["a", "1", "0"], ["b", "0", "1"]]):
                write(rows)
                with self.assertRaises(ValueError):
                    calibration.load_gold(path, reports, ["ACL", "MCL"], expected=1)

    def test_thresholds_exclude_null_and_preserve_denominators(self):
        rows = [{"level": 4, "gold": 1}, {"level": 0, "gold": 0}, {"level": None, "gold": 1}]
        result = calibration.threshold_metrics(rows, 4)
        self.assertEqual((result["tp"], result["tn"], result["abstained"]), (1, 1, 1))
        self.assertEqual(result["coverage"], 2/3)
        self.assertIsNone(calibration.threshold_metrics([], 4)["sensitivity"])


class FreezeTests(unittest.TestCase):
    def test_missing_gold_failed_tests_or_unresolved_conflicts_block_freeze(self):
        valid = {"status": "passed", "studies": 4407, "target_cells": 52884,
                 "duplicates_after": {"state_support_conflict_groups": 0}}
        gold = {"status": "passed", "cohort_studies": 58}
        tests = {"returncode": 0, "tests_run": 1}
        syntax = {"returncode": 0}
        freeze.require_gates(valid, gold, tests, syntax)
        for args in ((valid, {}, tests, syntax), (valid, gold, {"returncode": 1}, syntax),
                     (dict(valid, duplicates_after={"state_support_conflict_groups": 1}), gold, tests, syntax)):
            with self.assertRaises(ValueError):
                freeze.require_gates(*args)

    def test_zip_verification_checks_content_and_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "artifact"
            artifact.mkdir()
            (artifact / "data.json").write_bytes(b'{}')
            good = Path(temporary) / "good.zip"
            with zipfile.ZipFile(good, "w") as archive:
                archive.writestr("artifact/data.json", b'{}')
            self.assertEqual(freeze.verify_zip(good, artifact)["status"], "passed")
            for i, (name, data) in enumerate((("artifact/../outside", b'{}'), ("artifact/data.json", b'changed'))):
                bad = Path(temporary) / f"bad{i}.zip"
                with zipfile.ZipFile(bad, "w") as archive:
                    archive.writestr(name, data)
                with self.assertRaises(ValueError):
                    freeze.verify_zip(bad, artifact)


if __name__ == "__main__":
    unittest.main()
