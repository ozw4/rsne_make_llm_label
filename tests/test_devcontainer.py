"""Offline container-config and post-create tests; no Docker, npm, or inference."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEVCONTAINER = ROOT / ".devcontainer"


class DevcontainerTests(unittest.TestCase):
    def test_uses_local_dockerfile_not_network_installed_features(self):
        config = json.loads((DEVCONTAINER / "devcontainer.json").read_text())
        self.assertNotIn("features", config)
        self.assertNotIn("image", config)
        self.assertEqual(config["build"], {"dockerfile": "Dockerfile", "context": "."})
        dockerfile = (DEVCONTAINER / config["build"]["dockerfile"]).read_text()
        self.assertIn("FROM node:22-bookworm-slim AS node-runtime", dockerfile)
        self.assertIn("COPY --from=node-runtime /usr/local/bin/node", dockerfile)
        self.assertIn("COPY --from=node-runtime /usr/local/lib/node_modules/npm", dockerfile)
        commands = [line for line in dockerfile.splitlines() if line.startswith("RUN ")]
        self.assertTrue(commands)
        self.assertTrue(all(line.startswith("RUN --network=none ") for line in commands))
        self.assertNotIn("npm install", dockerfile)

    def test_reader_locations_and_credential_volume_are_preserved(self):
        config = json.loads((DEVCONTAINER / "devcontainer.json").read_text())
        self.assertEqual(config["workspaceFolder"], "/workspaces/rsne_make_llm_label")
        self.assertEqual(config["remoteUser"], "vscode")
        self.assertEqual(config["containerEnv"]["LABELER_WORKER_DIR"], "/home/vscode/label-worker")
        self.assertEqual(config["containerEnv"]["LABELER_CODEX_HOME"], "/home/vscode/.codex-labeler")
        self.assertEqual(config["mounts"], [
            "source=rsne-make-llm-label-codex-home,target=/home/vscode/.codex-labeler,type=volume"
        ])
        self.assertNotIn("CODEX_HOME", config["containerEnv"])
        self.assertNotIn("runArgs", config)

    def test_proxy_values_come_from_host_environment(self):
        environment = json.loads((DEVCONTAINER / "devcontainer.json").read_text())["containerEnv"]
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                    "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
            self.assertEqual(environment[key], "${localEnv:" + key + "}")

    def run_setup(self, *, installed: bool, install_fails: bool = False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / ".devcontainer").mkdir(parents=True)
            (project / "config").mkdir()
            (project / "config/codex-version.txt").write_text("0.153.4\n")
            shutil.copy(DEVCONTAINER / "post-create.sh", project / ".devcontainer/post-create.sh")
            home = root / "home"
            binaries = home / ".local/bin"
            binaries.mkdir(parents=True)
            trace = root / "commands.log"
            state = root / "installed"
            if installed:
                state.touch()
            stubs = {
                "node": 'printf "v22.0.0\\n"\n',
                "python": 'printf "python %s\\n" "$*" >> "$TEST_TRACE"\n',
                "sudo": 'printf "sudo %s\\n" "$*" >> "$TEST_TRACE"\n',
                "npm": '''printf 'npm %s\\n' "$*" >> "$TEST_TRACE"
if [[ "$1" == "install" ]]; then
  [[ "$TEST_INSTALL_FAILS" == "0" ]] || exit 42
  touch "$TEST_INSTALLED"
fi
''',
                "codex": '''printf 'codex %s\\n' "$*" >> "$TEST_TRACE"
if [[ "$1" == "--version" ]]; then
  if [[ -f "$TEST_INSTALLED" ]]; then
    printf 'codex-cli 0.153.4\\n'
  else
    printf 'codex-cli old-version\\n'
  fi
fi
''',
            }
            for name, body in stubs.items():
                path = binaries / name
                path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body)
                path.chmod(0o755)
            env = dict(os.environ, HOME=str(home), TEST_TRACE=str(trace),
                       TEST_INSTALLED=str(state), TEST_INSTALL_FAILS=str(int(install_fails)),
                       LABELER_CODEX_HOME=str(root / "reader-home"),
                       LABELER_WORKER_DIR=str(root / "worker"))
            result = subprocess.run(
                ["bash", str(project / ".devcontainer/post-create.sh")],
                cwd=root, env=env, capture_output=True, text=True, timeout=10,
            )
            return result, trace.read_text()

    def test_matching_codex_skips_install_but_runs_offline_setup(self):
        result, trace = self.run_setup(installed=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("npm install", trace)
        self.assertIn("python scripts/setup_worker.py", trace)
        self.assertIn("python -m unittest discover -s tests -v", trace)
        self.assertIn("features list", trace)
        self.assertNotIn("codex exec", trace)
        self.assertNotIn("codex login", trace)

    def test_outdated_codex_installs_the_pinned_version(self):
        result, trace = self.run_setup(installed=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("npm install --global --no-audit --no-fund --prefix", trace)
        self.assertIn("@openai/codex@0.153.4", trace)
        self.assertIn("python scripts/setup_worker.py", trace)

    def test_npm_failure_stops_before_worker_setup(self):
        result, trace = self.run_setup(installed=False, install_fails=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime access to the npm registry", result.stderr)
        self.assertNotIn("python scripts/setup_worker.py", trace)
        self.assertNotIn("features list", trace)

    def test_post_create_has_valid_shell_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(DEVCONTAINER / "post-create.sh")],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
