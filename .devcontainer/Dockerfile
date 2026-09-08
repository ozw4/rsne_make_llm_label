# Copy the Node runtime from an image, as in ozw4/kaggle_rsna_knee.
# Keep build steps network-free, as in ozw4/closed-llm-lab.
FROM node:22-bookworm-slim AS node-runtime
FROM mcr.microsoft.com/devcontainers/python:3.12-bookworm

USER root

# Only Node and npm are required; do not install nvm, yarn, or pnpm.
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm

# Image pulls need registry access. These build commands do not need a network.
RUN --network=none set -eu; \
    ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm; \
    ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx; \
    node --version; \
    npm --version; \
    python --version

USER vscode
WORKDIR /workspaces/rsne_make_llm_label
