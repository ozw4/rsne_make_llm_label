"""Offline tests only: synthetic reports and mocked Codex processes."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_labels as runner
import setup_worker as setup

LABELS = list(json.loads(runner.SCHEMA.read_text())["properties"]["labels"]["properties"])
REPORT = {"study_id": "synthetic-001", "report": "The ACL is intact. Small joint effusion. 滑膜炎は認めない。"}


def annotation(report=REPORT):
    result = {"study_id": report["study_id"], "labels": {
        label: {"state": "not_mentioned", "evidence": []} for label in LABELS
    }}
    result["labels"]["ACL"] = {"state": "negative", "evidence": ["The ACL is intact."]}
    return result


class LabelerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.worker, self.home = self.base / "worker", self.base / "reader-home"
        home_patch = mock.patch("setup_worker.Path.home", return_value=self.base)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        setup.setup(self.worker, self.home)
        self.output = self.base / "outputs"
        self.output.mkdir()
        self.args = argparse.Namespace(model="mock-model", reasoning_effort="medium", attempts=1, timeout=10)

    def mock_codex(self, *, invalid_first=False, tool=False):
        calls = []

        def invoke(argv, **kwargs):
            calls.append((argv, kwargs))
            report = json.loads(kwargs["input"])
            path = Path(argv[argv.index("--output-last-message") + 1])
            value = annotation(report)
            if invalid_first and len(calls) == 1:
                value["labels"]["ACL"]["evidence"] = ["Not in the report"]
            path.write_text(json.dumps(value))
            events = [{"type": "thread.started", "thread_id": f"new-thread-{len(calls)}"}]
            if tool:
                events.append({"type": "item.completed", "item": {"type": "command_execution"}})
            events.append({"type": "turn.completed", "usage": {
                "input_tokens": 100, "cached_input_tokens": 50, "output_tokens": 10,
            }})
            kwargs["stdout"].write("\n".join(json.dumps(e) for e in events))
            kwargs["stdout"].flush()
            return subprocess.CompletedProcess(argv, 0)

        return calls, invoke

    def test_prompt_copy_is_byte_identical(self):
        self.assertEqual((self.worker / "AGENTS.md").read_bytes(), setup.PROMPT.read_bytes())

    def test_worker_must_be_outside_repository(self):
        with self.assertRaises(ValueError):
            setup.check_locations(setup.ROOT / "worker", self.home)

    def test_extra_worker_file_rejected(self):
        (self.worker / "previous-report.json").write_text("{}")
        with self.assertRaises(ValueError):
            setup.check_worker(self.worker, self.home)

    def test_global_instruction_rejected(self):
        (self.home / "AGENTS.md").write_text("Other instructions")
        with self.assertRaises(ValueError):
            setup.check_worker(self.worker, self.home)

    def test_setup_refuses_changed_prompt_without_replace(self):
        (self.worker / "AGENTS.md").write_text("changed")
        with self.assertRaises(ValueError):
            setup.setup(self.worker, self.home)
        setup.setup(self.worker, self.home, replace=True)

    def test_input_preserves_original_text(self):
        path = self.base / "input.jsonl"
        path.write_text(json.dumps(REPORT, ensure_ascii=False) + "\n", encoding="utf-8")
        self.assertEqual(runner.read_reports(path), [REPORT])

    def test_input_rejects_gold_column(self):
        path = self.base / "input.jsonl"
        path.write_text(json.dumps(dict(REPORT, ACL=0)))
        with self.assertRaises(ValueError):
            runner.read_reports(path)

    def test_input_rejects_duplicate_id_and_blank_report(self):
        path = self.base / "input.jsonl"
        for records in ([REPORT, REPORT], [dict(REPORT, report=" ")], [dict(REPORT, study_id="../x")]):
            with self.subTest(records=len(records)):
                path.write_text("\n".join(json.dumps(x) for x in records))
                with self.assertRaises(ValueError):
                    runner.read_reports(path)

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(ValueError):
            runner.decode('{"study_id":"a","study_id":"b"}')

    def test_valid_annotation_and_unicode_evidence(self):
        value = annotation()
        value["labels"]["Synovitis"] = {"state": "negative", "evidence": ["滑膜炎は認めない。"]}
        runner.validate_annotation(value, REPORT, LABELS)

    def test_missing_extra_or_reordered_labels_rejected(self):
        for mode in ("missing", "extra", "order"):
            value = annotation()
            if mode == "missing":
                value["labels"].pop("ACL")
            elif mode == "extra":
                value["labels"]["OTHER"] = {"state": "negative", "evidence": []}
            else:
                value["labels"] = dict(reversed(list(value["labels"].items())))
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                runner.validate_annotation(value, REPORT, LABELS)

    def test_state_evidence_constraints(self):
        cells = [
            {"state": "negative", "evidence": []},
            {"state": "not_mentioned", "evidence": ["The ACL is intact."]},
            {"state": "positive", "evidence": ["Translated evidence"]},
            {"state": "uncertain", "evidence": [""]},
            {"state": "other", "evidence": []},
            {"state": [], "evidence": []},
        ]
        for cell in cells:
            value = annotation()
            value["labels"]["ACL"] = cell
            with self.subTest(cell=cell), self.assertRaises(ValueError):
                runner.validate_annotation(value, REPORT, LABELS)

    def test_wrong_id_rejected(self):
        value = annotation()
        value["study_id"] = "another-study"
        with self.assertRaises(ValueError):
            runner.validate_annotation(value, REPORT, LABELS)

    def test_command_is_new_exec_and_not_resume(self):
        argv = runner.command("mock-model", "medium", self.worker, self.base / "result.json")
        self.assertEqual(argv[:2], ["codex", "exec"])
        self.assertIn("--ephemeral", argv)
        self.assertEqual(argv[-1], "-")
        for word in ("resume", "fork", "--last"):
            self.assertNotIn(word, argv)

    def test_two_reports_start_separate_processes_and_skip_completed(self):
        calls, invoke = self.mock_codex()
        second = dict(REPORT, study_id="synthetic-002")
        with mock.patch.object(runner, "execute", side_effect=invoke):
            runner.label_one(REPORT, self.args, self.worker, self.home, self.output, LABELS)
            runner.label_one(second, self.args, self.worker, self.home, self.output, LABELS)
        self.assertEqual(len(calls), 2)
        self.assertEqual(json.loads(calls[1][1]["input"]), second)
        self.assertEqual(calls[0][1]["env"]["CODEX_HOME"], str(self.home))
        self.assertTrue(runner.completed(self.output, REPORT, LABELS))
        receipts = [json.loads(p.read_text()) for p in (self.output / "receipts").glob("*.json")]
        self.assertEqual(len({x["thread_id"] for x in receipts}), 2)
        self.assertEqual(receipts[0]["usage"]["cached_input_tokens"], 50)

    def test_retry_is_new_process_with_original_input(self):
        calls, invoke = self.mock_codex(invalid_first=True)
        self.args.attempts = 2
        with mock.patch.object(runner, "execute", side_effect=invoke), mock.patch.object(runner.time, "sleep"):
            runner.label_one(REPORT, self.args, self.worker, self.home, self.output, LABELS)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1]["input"], calls[1][1]["input"])
        self.assertEqual(len(list((self.output / "attempts").rglob("failure.json"))), 1)
        self.assertTrue(runner.completed(self.output, REPORT, LABELS))

    def test_tools_are_not_accepted(self):
        _, invoke = self.mock_codex(tool=True)
        with mock.patch.object(runner, "execute", side_effect=invoke), self.assertRaises(RuntimeError):
            runner.label_one(REPORT, self.args, self.worker, self.home, self.output, LABELS)
        self.assertFalse(runner.completed(self.output, REPORT, LABELS))

    def test_changed_input_and_corrupted_completed_output_stop(self):
        _, invoke = self.mock_codex()
        with mock.patch.object(runner, "execute", side_effect=invoke):
            runner.label_one(REPORT, self.args, self.worker, self.home, self.output, LABELS)
        with self.assertRaises(ValueError):
            runner.completed(self.output, dict(REPORT, report="changed"), LABELS)
        result = self.output / "annotations/synthetic-001.json"
        result.write_text("{}")
        with self.assertRaises(ValueError):
            runner.completed(self.output, REPORT, LABELS)

    def test_run_manifest_cannot_change(self):
        runner.ensure_manifest(self.output, {"model": "one"})
        runner.ensure_manifest(self.output, {"model": "one"})
        with self.assertRaises(ValueError):
            runner.ensure_manifest(self.output, {"model": "two"})

    def test_dry_run_never_calls_codex(self):
        path = self.base / "input.jsonl"
        path.write_text(json.dumps(REPORT))
        self.args.input, self.args.limit, self.args.dry_run = path, 1, True
        with mock.patch.object(runner, "default_paths", return_value=(self.worker, self.home)), mock.patch.object(runner.subprocess, "run") as launch:
            runner.run(self.args)
        launch.assert_not_called()

    def test_timeout_kills_cli_process_group(self):
        process = mock.Mock(pid=12345)
        process.communicate.side_effect = [subprocess.TimeoutExpired("codex", 1), (None, None)]
        with mock.patch.object(runner.subprocess, "Popen", return_value=process), mock.patch.object(runner.os, "killpg") as kill:
            with self.assertRaises(subprocess.TimeoutExpired):
                runner.execute(["codex"], input="{}", cwd=self.worker, env={}, stdout=None, stderr=None, timeout=1)
        kill.assert_called_once_with(12345, runner.signal.SIGKILL)

    def test_output_schema_has_twelve_required_labels(self):
        schema = json.loads(runner.SCHEMA.read_text())
        self.assertEqual(len(LABELS), 12)
        self.assertEqual(schema["properties"]["labels"]["required"], LABELS)
        self.assertFalse(schema["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
