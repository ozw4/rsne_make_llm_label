# Devcontainer

This is a CPU-only Python 3.12 and Node.js 22 environment for the report labeler.
The repository remains at `/workspaces/rsne_make_llm_label`; the reader runs from
`/home/vscode/label-worker` with its own `/home/vscode/.codex-labeler` volume.
The labeling prompt, schema, model settings, and one-new-session-per-report
contract are not changed by this container configuration.

## Build design and references

The Node Feature has been removed. It used nvm and queried GitHub during the
build, which failed with `Could not resolve host: github.com`.

The Dockerfile copies Node and npm from `node:22-bookworm-slim` into the existing
Python devcontainer image. It does not run nvm, git downloads, apt, pip, or npm
installation during the build. Its runtime checks run with `--network=none`.
The build context is only `.devcontainer`, not the reports or output folders.

The design follows these existing repositories, without importing their GPU,
Docker socket, other-agent, or training-data configuration:

- [ozw4/kaggle_rsna_knee Dockerfile](https://github.com/ozw4/kaggle_rsna_knee/blob/main/Dockerfile): copy Node from a Bookworm image.
- [ozw4/kaggle_rsna_knee Compose configuration](https://github.com/ozw4/kaggle_rsna_knee/blob/main/.devcontainer/compose-dev.yaml): pass proxy settings to the container.
- [ozw4/closed-llm-lab Dockerfile](https://github.com/ozw4/closed-llm-lab/blob/45aa02f3053400e2f2f02b50089f08b6156e6e0c/Dockerfile.devcontainer): avoid package downloads in devcontainer build steps.

Docker's [multi-stage build documentation](https://docs.docker.com/build/building/multi-stage/)
describes copying artifacts from other images.

## Rebuild

On the host, in this repository:

```bash
git pull --ff-only
```

Then run **Dev Containers: Rebuild Container** in VS Code. Do not delete the
reader credential volume. Host-wide Docker DNS changes, host networking, and
privileged mode are not required by this configuration.

The post-create script installs the exact Codex CLI version from
`config/codex-version.txt` if needed, sets up the worker, runs offline tests, and
checks local Codex features. It never starts labeling or authentication.
A matching installed Codex version is reused rather than installed again.

## Network requirements

This is a network-light build, not an offline installation:

- Docker must be able to pull the Node and Python images unless cached. Image
  pulls use the Docker daemon or builder's network and proxy configuration.
- After creation, npm registry access is required to install Codex if it is not
  already installed. The script stops with an error if installation fails.
- Authentication and labeling require access to the service used by Codex.

`devcontainer.json` forwards upper- and lower-case `HTTP_PROXY`, `HTTPS_PROXY`,
`ALL_PROXY`, and `NO_PROXY` variables from the environment of VS Code. There are
no hard-coded proxy addresses, public DNS servers, or credentials. Unset host
variables expand to empty values; use the host environment as the source of
truth for runtime proxies in this configuration. Do not commit credentials.

When a proxy is needed, export the appropriate variables before launching VS
Code from that shell. An already-running VS Code process may retain its old
environment, so restart it after changing these values. The proxy address must
be reachable from the container; container localhost is not the host localhost.
These runtime settings do not configure image pulls or fix host-wide DNS.

If creation succeeds but post-create npm installation fails, inspect that npm
error and the runtime proxy/network separately. After correcting the network,
rerun the idempotent setup from the container terminal:

```bash
bash .devcontainer/post-create.sh
```

For local configuration checks without Docker or Codex inference:

```bash
python -m unittest discover -s tests -p 'test_devcontainer.py' -v
bash -n .devcontainer/post-create.sh
```

Mock tests do not prove that image pulls or a real Docker build succeed on the
host. A real rebuild is still the integration check for the local environment.
