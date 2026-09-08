#!/usr/bin/env bash
set -euo pipefail
cd /workspaces/rsne_make_llm_label
export PATH="$HOME/.local/bin:$PATH"
version="$(cat config/codex-version.txt)"
if ! command -v codex >/dev/null || [[ "$(codex --version)" != "codex-cli $version" ]]; then
  npm install --global --prefix "$HOME/.local" "@openai/codex@$version"
fi
mkdir -p "$LABELER_CODEX_HOME"
sudo chown -R "$(id -u):$(id -g)" "$LABELER_CODEX_HOME"
chmod 700 "$LABELER_CODEX_HOME"
python scripts/setup_worker.py
python -m unittest discover -s tests -v
# This checks local settings only. No inference, login, or report upload is run.
CODEX_HOME="$LABELER_CODEX_HOME" codex --cd "$LABELER_WORKER_DIR" features list >/dev/null
printf '\nSetup finished. Authenticate the dedicated CODEX_HOME as described in README.md.\n'
