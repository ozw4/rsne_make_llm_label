#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PATH="$HOME/.local/bin:$PATH"

# Node and npm are provided by the image, not a network-installed Feature.
node --version
npm --version
python --version
version="$(cat config/codex-version.txt)"
if ! command -v codex >/dev/null || [[ "$(codex --version)" != "codex-cli $version" ]]; then
  if ! npm install --global --no-audit --no-fund --prefix "$HOME/.local" "@openai/codex@$version"; then
    printf '\nCodex installation failed. Check runtime access to the npm registry and your proxy settings.\n' >&2
    exit 1
  fi
fi
[[ "$(codex --version)" == "codex-cli $version" ]]

mkdir -p "$LABELER_CODEX_HOME"
sudo chown -R "$(id -u):$(id -g)" "$LABELER_CODEX_HOME"
chmod 700 "$LABELER_CODEX_HOME"
python scripts/setup_worker.py
python -m unittest discover -s tests -v
# This checks local settings only. No inference, login, or report upload is run.
CODEX_HOME="$LABELER_CODEX_HOME" codex --cd "$LABELER_WORKER_DIR" features list >/dev/null
printf '\nSetup finished. Authenticate the dedicated CODEX_HOME as described in README.md.\n'
