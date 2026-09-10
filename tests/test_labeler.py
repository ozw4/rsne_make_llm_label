"""Offline tests only: synthetic reports and mocked Codex processes."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import tomllib
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
        label: {"state": "not_mentioned", "support_level": None, "evidence": []} for label in LABELS
    }}
    result["labels"]["ACL"] = {"state": "negative", "support_level": 0, "evidence": ["The ACL is intact."]}
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

    def mock_codex(self, *, invalid_first=False, invalid_level=False, tool=False):
        calls = []

        def invoke(argv, **kwargs):
            calls.append((argv, kwargs))
            report = json.loads(kwargs["input"])
            path = Path(argv[argv.index("--output-last-message") + 1])
            value = annotation(report)
            if invalid_level:
                value["labels"]["ACL"]["support_level"] = 1
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

    def seed_inert_plugin_cache(self, home=None):
        home = self.home if home is None else home
        package = home / "plugins/cache/openai-curated-remote/google-drive/0.1.16"
        manifest = package / ".codex-plugin/plugin.json"
        skill = package / "skills/example/SKILL.md"
        manifest.parent.mkdir(parents=True)
        skill.parent.mkdir(parents=True)
        manifest.write_text('{"name":"synthetic-plugin","version":"0.0.0"}')
        skill.write_text("Synthetic cached skill; never load or execute.")
        (package.parent / ".codex-remote-plugin-install.json").write_text('{"synthetic":true}')
        (home / "plugins/.remote-plugin-install-staging").mkdir()

    def synthetic_reader_home(self, base):
        home = Path(base) / "reader-home"
        home.mkdir()
        (home / "config.toml").write_bytes(setup.CONFIG.read_bytes())
        return home

    def test_inert_curated_plugin_cache_is_allowed_with_features_disabled(self):
        features = tomllib.loads(setup.CONFIG.read_text())["features"]
        self.assertIs(features["plugins"], False)
        self.assertIs(features["apps"], False)
        self.seed_inert_plugin_cache()
        setup.check_worker(self.worker, self.home)

    def test_inert_cache_requires_explicitly_disabled_plugins_and_apps(self):
        self.seed_inert_plugin_cache()
        original = setup.CONFIG.read_text()
        canonical = self.base / "test-canonical.toml"
        for feature in ("plugins", "apps"):
            line = f"{feature} = false\n"
            self.assertIn(line, original)
            for replacement in ("true", "0", '"false"', None):
                updated = "" if replacement is None else f"{feature} = {replacement}\n"
                config = original.replace(line, updated)
                canonical.write_text(config)
                (self.home / "config.toml").write_text(config)
                with self.subTest(feature=feature, value=replacement):
                    with mock.patch.object(setup, "CONFIG", canonical), self.assertRaises(ValueError):
                        setup.check_worker(self.worker, self.home)

    def test_unapproved_mcp_plugins_and_apps_config_rejected(self):
        self.seed_inert_plugin_cache()
        original = setup.CONFIG.read_text()
        canonical = self.base / "test-canonical.toml"
        for table in ("mcp_servers", "plugins", "apps"):
            config = original + f"\n[{table}.synthetic]\ncommand = \"unused-synthetic-command\"\n"
            canonical.write_text(config)
            (self.home / "config.toml").write_text(config)
            with self.subTest(table=table):
                with mock.patch.object(setup, "CONFIG", canonical), self.assertRaises(ValueError):
                    setup.check_worker(self.worker, self.home)

    def test_unexpected_plugin_paths_and_types_rejected(self):
        cases = [
            ("plugins/unapproved", "directory"),
            ("plugins/.system", "directory"),
            ("plugins/cache/unapproved", "directory"),
            ("plugins/.remote-plugin-install-staging/incomplete", "file"),
            ("plugins", "file"),
            ("plugins/cache", "file"),
            ("plugins/.remote-plugin-install-staging", "file"),
            ("plugins/cache/openai-curated-remote", "file"),
        ]
        for relative, kind in cases:
            with self.subTest(path=relative, kind=kind), tempfile.TemporaryDirectory(dir=self.base) as temporary:
                home = self.synthetic_reader_home(temporary)
                path = home / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                if kind == "directory":
                    path.mkdir()
                else:
                    path.write_text("Synthetic unexpected cache entry")
                with self.assertRaises(ValueError):
                    setup.check_worker(self.worker, home)

    def test_plugin_symlinks_rejected_at_every_depth(self):
        paths = [
            "plugins",
            "plugins/cache",
            "plugins/cache/openai-curated-remote",
            "plugins/cache/openai-curated-remote/google-drive/0.1.16/skills/example/link.md",
        ]
        for relative in paths:
            with self.subTest(path=relative), tempfile.TemporaryDirectory(dir=self.base) as temporary:
                home = self.synthetic_reader_home(temporary)
                target = Path(temporary) / "synthetic-link-target"
                if relative.endswith(".md"):
                    target.write_text("Synthetic linked skill")
                else:
                    target.mkdir()
                link = home / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(target, target_is_directory=target.is_dir())
                with self.assertRaises(ValueError):
                    setup.check_worker(self.worker, home)

    def test_inert_plugin_cache_does_not_allow_standalone_custom_skills(self):
        for location in ("reader", "shared"):
            with self.subTest(location=location), tempfile.TemporaryDirectory(dir=self.base) as temporary:
                home = self.synthetic_reader_home(temporary)
                self.seed_inert_plugin_cache(home)
                directory = home / "skills" if location == "reader" else Path(temporary) / ".agents/skills"
                skill = directory / "custom/SKILL.md"
                skill.parent.mkdir(parents=True)
                skill.write_text("Synthetic standalone skill")
                with mock.patch("setup_worker.Path.home", return_value=Path(temporary)), self.assertRaises(ValueError):
                    setup.check_worker(self.worker, home)

    def test_unexpected_plugin_cache_blocks_codex_launch(self):
        (self.home / "plugins/cache/unapproved").mkdir(parents=True)
        with mock.patch.object(runner, "execute") as launch:
            with self.assertRaises(ValueError):
                runner.label_one(REPORT, self.args, self.worker, self.home, self.output, LABELS)
        launch.assert_not_called()
        self.assertFalse(runner.completed(self.output, REPORT, LABELS))

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
        for name in ("AGENTS.md", "AGENTS.override.md", "config.override.toml"):
            with self.subTest(name=name), tempfile.TemporaryDirectory(dir=self.base) as temporary:
                home = self.synthetic_reader_home(temporary)
                self.seed_inert_plugin_cache(home)
                (home / name).write_text("Other synthetic instructions")
                with self.assertRaises(ValueError):
                    setup.check_worker(self.worker, home)

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
        value["labels"]["Synovitis"] = {"state": "negative", "support_level": 0, "evidence": ["滑膜炎は認めない。"]}
        runner.validate_annotation(value, REPORT, LABELS)

    def test_missing_extra_or_reordered_labels_rejected(self):
        for mode in ("missing", "extra", "order"):
            value = annotation()
            if mode == "missing":
                value["labels"].pop("ACL")
            elif mode == "extra":
                value["labels"]["OTHER"] = {"state": "negative", "support_level": 0, "evidence": []}
            else:
                value["labels"] = dict(reversed(list(value["labels"].items())))
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                runner.validate_annotation(value, REPORT, LABELS)

    def test_all_six_state_support_level_pairs(self):
        cases = [
            ("negative", 0, "The ACL is intact."),
            ("uncertain", 1, "An ACL tear is unlikely but cannot be excluded."),
            ("uncertain", 2, "Possible ACL tear."),
            ("uncertain", 3, "Findings are highly suspicious for an ACL tear."),
            ("positive", 4, "Complete ACL tear."),
            ("not_mentioned", None, "Small joint effusion."),
        ]
        for state, level, text in cases:
            report = dict(REPORT, report=text)
            value = annotation(report)
            value["labels"]["ACL"] = {
                "state": state, "support_level": level,
                "evidence": [] if state == "not_mentioned" else [text],
            }
            before = json.dumps(value)
            with self.subTest(state=state, level=level):
                runner.validate_annotation(value, report, LABELS)
                self.assertEqual(json.dumps(value), before)

    def test_incompatible_state_support_levels_rejected(self):
        valid_pairs = {
            ("negative", 0), ("uncertain", 1), ("uncertain", 2),
            ("uncertain", 3), ("positive", 4), ("not_mentioned", None),
        }
        for state in ("negative", "uncertain", "positive", "not_mentioned"):
            for level in (None, 0, 1, 2, 3, 4, -1, 5):
                if (state, level) in valid_pairs:
                    continue
                value = annotation()
                value["labels"]["ACL"] = {
                    "state": state, "support_level": level,
                    "evidence": [] if state == "not_mentioned" else ["The ACL is intact."],
                }
                with self.subTest(state=state, level=level), self.assertRaisesRegex(ValueError, "support_level"):
                    runner.validate_annotation(value, REPORT, LABELS)

    def test_support_level_requires_integer_or_null(self):
        for level in (False, True, 0.0, 1.0, 2.0, 3.0, 4.0, "0", [], {}):
            value = annotation()
            value["labels"]["ACL"]["support_level"] = level
            with self.subTest(level=level), self.assertRaisesRegex(ValueError, "integer or null"):
                runner.validate_annotation(value, REPORT, LABELS)

    def test_missing_and_extra_cell_fields_rejected(self):
        for field in ("state", "support_level", "evidence", "extra"):
            value = annotation()
            if field == "extra":
                value["labels"]["ACL"][field] = "unexpected"
            else:
                del value["labels"]["ACL"][field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "invalid fields"):
                runner.validate_annotation(value, REPORT, LABELS)

    def test_state_evidence_constraints(self):
        cells = [
            {"state": "negative", "support_level": 0, "evidence": []},
            {"state": "positive", "support_level": 4, "evidence": []},
            {"state": "uncertain", "support_level": 2, "evidence": []},
            {"state": "not_mentioned", "support_level": None, "evidence": ["The ACL is intact."]},
            {"state": "positive", "support_level": 4, "evidence": ["Translated evidence"]},
            {"state": "uncertain", "support_level": 1, "evidence": [""]},
            {"state": "uncertain", "support_level": 3, "evidence": [" "]},
            {"state": "negative", "support_level": 0, "evidence": [None]},
            {"state": "negative", "support_level": 0, "evidence": "The ACL is intact."},
            {"state": "negative", "support_level": 0, "evidence": None},
        ]
        for cell in cells:
            value = annotation()
            value["labels"]["ACL"] = cell
            with self.subTest(cell=cell), self.assertRaisesRegex(ValueError, "evidence"):
                runner.validate_annotation(value, REPORT, LABELS)

    def test_invalid_states_rejected(self):
        for state in ("other", []):
            value = annotation()
            value["labels"]["ACL"]["state"] = state
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, "invalid state"):
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

    def test_first_mocked_report_can_create_inert_cache_before_second_report(self):
        calls, invoke = self.mock_codex()
        second = dict(REPORT, study_id="synthetic-002")

        def invoke_and_create_cache(argv, **kwargs):
            result = invoke(argv, **kwargs)
            if len(calls) == 1:
                self.seed_inert_plugin_cache()
            return result

        with mock.patch.object(runner, "execute", side_effect=invoke_and_create_cache):
            runner.label_one(REPORT, self.args, self.worker, self.home, self.output, LABELS)
            runner.label_one(second, self.args, self.worker, self.home, self.output, LABELS)
        self.assertEqual(len(calls), 2)
        self.assertEqual([json.loads(call[1]["input"]) for call in calls], [REPORT, second])
        threads = []
        for report, (argv, kwargs) in zip((REPORT, second), calls):
            self.assertEqual(argv[:2], ["codex", "exec"])
            self.assertNotIn("resume", argv)
            self.assertNotIn("fork", argv)
            self.assertEqual(kwargs["env"]["CODEX_HOME"], str(self.home))
            self.assertTrue(runner.completed(self.output, report, LABELS))
            receipt = json.loads((self.output / "receipts" / (report["study_id"] + ".json")).read_text())
            threads.append(receipt["thread_id"])
        self.assertEqual(len(set(threads)), 2)

    def test_retry_is_new_process_with_original_input(self):
        calls, invoke = self.mock_codex(invalid_first=True)
        self.args.attempts = 2
        with mock.patch.object(runner, "execute", side_effect=invoke), mock.patch.object(runner.time, "sleep"):
            runner.label_one(REPORT, self.args, self.worker, self.home, self.output, LABELS)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1]["input"], calls[1][1]["input"])
        self.assertEqual(len(list((self.output / "attempts").rglob("failure.json"))), 1)
        self.assertTrue(runner.completed(self.output, REPORT, LABELS))

    def test_invalid_support_level_never_marks_report_complete(self):
        _, invoke = self.mock_codex(invalid_level=True)
        with mock.patch.object(runner, "execute", side_effect=invoke), self.assertRaises(RuntimeError):
            runner.label_one(REPORT, self.args, self.worker, self.home, self.output, LABELS)
        self.assertFalse(runner.completed(self.output, REPORT, LABELS))
        self.assertFalse((self.output / "annotations/synthetic-001.json").exists())
        self.assertEqual(len(list((self.output / "attempts").rglob("failure.json"))), 1)

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
        cell = schema["$defs"]["cell"]
        self.assertEqual(cell["required"], ["state", "support_level", "evidence"])
        self.assertEqual(list(cell["properties"]), cell["required"])
        self.assertFalse(cell["additionalProperties"])
        self.assertEqual(cell["properties"]["support_level"]["type"], ["integer", "null"])
        self.assertEqual(cell["properties"]["support_level"]["enum"], [0, 1, 2, 3, 4, None])


if __name__ == "__main__":
    unittest.main()
