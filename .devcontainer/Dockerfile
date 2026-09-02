# =============================================================================
# Dockerfile for Ubuntu Dev Environment
# Version: 1.12.1
# Path: .devcontainer/Dockerfile
# Description: Development container with Python 3.13, Node.js 25, kubectl,
#              Docker CLI (docker-outside-of-docker), Claude Code CLI.
#              Python 3.13 installed via deadsnakes PPA.
#
# Versions:
#   1.12.1: comments only — no layer changes. 1.12.0 (and devcontainer.json
#           v9) asserted that a "docker-group-GID alignment in
#           postCreateCommand" granted `developer` access to the socket. It
#           did not: on Docker Desktop for macOS the bind-mounted socket is
#           root:root, and GID 0 cannot be moved onto the `docker` group.
#           devcontainer.json v10 replaces that with a `chgrp docker` on the
#           socket at postStartCommand. The `usermod -aG docker developer`
#           below is what makes that work and is now the ONLY membership
#           step — do not remove it.
#   1.12.0: install the Docker CLI + Compose/Buildx plugins directly from
#           Docker's official apt repo, replacing the
#           `docker-outside-of-docker` devcontainer feature (removed from
#           devcontainer.json v9). The feature's install script failed on an
#           unauthenticated `git ls-remote https://github.com` version-
#           resolution step (rate-limited/proxied in this environment) —
#           an inconsistency no feature option could bypass. DooD capability
#           is UNCHANGED: the CLI targets the HOST daemon via the socket
#           bind-mounted in devcontainer.json v9 (DOCKER_HOST=
#           unix:///var/run/docker.sock), with socket access granted by the
#           chgrp in postStartCommand (see 1.12.1 above — the original claim
#           about a postCreateCommand GID alignment was wrong).
#           Pure apt, no dynamic git resolution → deterministic build.
#   1.11.0: editable-install the `ay_extractor` (C13) sub-project with its
#           full dependency set (`[all,dev,integration]`) at build time,
#           mirroring the ay_platform_core install. This makes the C13
#           pipeline importable + its test suite runnable inside the
#           devcontainer (previously `ayextractor` was not installed, so its
#           tests could not run on the host). Requires a devcontainer rebuild.
#   1.10.0: pin Node.js to 25 (nodesource setup_25.x) instead of `lts`
#           (which resolved to 24). Aligns the devcontainer with CI
#           (`node-version: "25"`), `ay_platform_ui` engines (`>=25`) and
#           the `node:25-alpine` UI build image — so Node-25-specific
#           behaviour (e.g. the default-on Web Storage API, vitest#8757)
#           reproduces locally instead of only biting in CI. Requires a
#           devcontainer rebuild to take effect.
#   1.9.0: pre-install Playwright Chromium + system deps at build time.
#          Browsers are installed to PLAYWRIGHT_BROWSERS_PATH=
#          /opt/playwright-browsers (shared, OUTSIDE the workspace bind
#          mount, OUTSIDE any user $HOME) so both root (build) and the
#          `developer` user (runtime) resolve them via the env var.
#          `--with-deps` adds the Chromium apt deps (libnss3, libxss1,
#          etc.) so the browser can actually launch ; first build adds
#          ~250 MB to the image but eliminates the manual
#          `npx playwright install` step on every fresh devcontainer.
#          Companion .dockerignore (v1) trims build context — saves
#          minutes on subsequent rebuilds when caches/reports are
#          present on the host.
#   1.8.0: bake ay_platform_ui's node_modules/ at build time into
#          /opt/ui-deps/ay_platform_ui/node_modules — a path OUTSIDE the
#          workspace bind mount. devcontainer.json v7's postStartCommand
#          symlinks ${LOCAL_WORKSPACE_FOLDER}/ay_platform_ui/node_modules
#          to that location IF the host has no real node_modules/ already.
#          This mirrors v1.6.0's Python install pattern functionally :
#          deps survive the runtime bind mount, first-shell `npm run X`
#          works without manual setup. Asymmetry rationale : Python's
#          `pip install -e` writes a .pth to /usr/local/lib/.../site-
#          packages (outside workspace) so the bind mount can't shadow
#          it ; npm writes node_modules INTO the workspace, so a
#          symlink redirect is needed instead.
#   1.7.0: parameterise the build/runtime install path via the
#          `LOCAL_WORKSPACE_FOLDER` build arg. devcontainer.json v5 aligns
#          the bind-mount target with the host workspace path so that
#          docker-outside-of-docker bind mounts in compose files resolve
#          on the host daemon. The Dockerfile must use the SAME path for
#          its build-time editable install so the `.pth` stays valid once
#          the runtime mount comes up.
#   1.6.0: install all ay_platform_core dependencies (main + [test] + [dev])
#          from pyproject.toml at build time AGAINST the same path that is
#          bind-mounted at runtime (`/workspace/ay_platform_core`). pip
#          records a `.pth` pointing at `/workspace/ay_platform_core/src`;
#          the bind mount at container start replaces the content of that
#          directory with the host tree while the `.pth` entry stays valid,
#          so edits to the host source are picked up immediately without
#          re-running pip. No postCreateCommand swap needed.
#   1.5.0: first pyproject-driven install (installed to /opt/..., required
#          a postCreateCommand swap). Superseded by 1.6.0.
# =============================================================================

ARG TARGETOS=linux
ARG TARGETARCH=amd64
ARG LOCAL_WORKSPACE_FOLDER=/workspace
FROM --platform=${TARGETOS}/${TARGETARCH} ubuntu:22.04
ARG LOCAL_WORKSPACE_FOLDER=/workspace

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Paris

USER root

# Base tooling + Python 3.13 via deadsnakes (single apt layer; no recommends)
# `libffi-dev` added so cryptography/argon2-cffi wheels can build if needed.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl wget git nano unzip ca-certificates gnupg lsb-release \
    net-tools iputils-ping telnet \
    software-properties-common build-essential libffi-dev \
    sudo jq file \
    python3-pip python3-venv \
 && add-apt-repository ppa:deadsnakes/ppa -y \
 && apt-get update && apt-get install -y --no-install-recommends \
    python3.13 python3.13-venv python3.13-dev \
 && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install kubectl (latest stable)
RUN curl -LO "https://dl.k8s.io/release/$(curl -Ls https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
 && chmod +x kubectl && mv kubectl /usr/local/bin/

# Docker CLI + Compose/Buildx plugins from Docker's official apt repo.
# docker-outside-of-docker (DooD): only the CLIENT is installed here — no
# daemon. Commands hit the HOST docker daemon through the socket bind-mounted
# by devcontainer.json (DOCKER_HOST=unix:///var/run/docker.sock). That socket
# arrives root:root, so devcontainer.json v10 chgrp's it to the `docker` group
# at postStartCommand; the `usermod -aG docker developer` below is the other
# half of that grant.
# arch/codename hardcoded (amd64/jammy) to match the pinned ubuntu:22.04 base
# and the rest of this file (kubectl linux/amd64, deadsnakes jammy PPA).
# `docker-ce` (daemon) is deliberately NOT installed — we reuse the host's.
RUN install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc \
 && chmod a+r /etc/apt/keyrings/docker.asc \
 && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu jammy stable" \
    > /etc/apt/sources.list.d/docker.list \
 && apt-get update && apt-get install -y --no-install-recommends \
    docker-ce-cli docker-buildx-plugin docker-compose-plugin \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Node.js 25 - required for Claude Code CLI AND to match the rest
# of the stack (CI `node-version: "25"`, `ay_platform_ui` engines `>=25`,
# the `node:25-alpine` base in infra/docker/Dockerfile.ui). The previous
# `setup_lts.x` resolved to Node 24, which diverged from CI and let the
# Node-25-only web-storage behaviour (vitest#8757) slip past local runs.
RUN curl -fsSL https://deb.nodesource.com/setup_25.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# Make Python 3.13 the default for python3 and python
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.13 2 \
 && update-alternatives --install /usr/bin/python  python  /usr/bin/python3.13 2

# Ensure pip is for Python 3.13
RUN curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py \
 && python3.13 /tmp/get-pip.py \
 && rm -f /tmp/get-pip.py

# Claude Code CLI (Node) + latest pip/setuptools/wheel.
RUN npm install -g @anthropic-ai/claude-code \
 && python3.13 -m pip install --upgrade --no-cache-dir pip setuptools wheel

# -----------------------------------------------------------------------------
# ay_platform_core build-time install.
# Build context is the monorepo root (see devcontainer.json `context: ".."`),
# so we can COPY `ay_platform_core/` directly. The copy goes to the SAME
# path that is bind-mounted at runtime (LOCAL_WORKSPACE_FOLDER, e.g.
# `/Users/<user>/.../ay-monorepo` on macOS docker-outside-of-docker) so the
# `.pth` file written by `pip install -e` keeps pointing to a valid
# location after the mount — the directory CONTENTS change (build-time
# snapshot → live host source), but `${LOCAL_WORKSPACE_FOLDER}/ay_platform_core/src`
# still resolves, and Python reads the live code.
# Installs: main `dependencies` + [test] + [dev] via the `all` extra.
# -----------------------------------------------------------------------------
COPY ay_platform_core/pyproject.toml ${LOCAL_WORKSPACE_FOLDER}/ay_platform_core/pyproject.toml
COPY ay_platform_core/src ${LOCAL_WORKSPACE_FOLDER}/ay_platform_core/src
RUN python3.13 -m pip install --no-cache-dir -e "${LOCAL_WORKSPACE_FOLDER}/ay_platform_core[all]"

# -----------------------------------------------------------------------------
# ay_extractor (C13) build-time install — same editable-install pattern as
# ay_platform_core above (the build-time COPY path matches the runtime bind
# mount so pip's `.pth` stays valid once the live source is mounted). The
# `[all,dev,integration]` extras pull every runtime dep (pymupdf, ebooklib,
# beautifulsoup4, scikit-learn, openai, boto3, fastapi, refextract, …) plus
# the test toolchain (pytest, testcontainers) so the C13 suite runs
# inside the devcontainer.
# -----------------------------------------------------------------------------
COPY ay_extractor/pyproject.toml ${LOCAL_WORKSPACE_FOLDER}/ay_extractor/pyproject.toml
COPY ay_extractor/src ${LOCAL_WORKSPACE_FOLDER}/ay_extractor/src
RUN python3.13 -m pip install --no-cache-dir -e "${LOCAL_WORKSPACE_FOLDER}/ay_extractor[all,dev,integration]"

# -----------------------------------------------------------------------------
# ay_platform_ui build-time dependency install (Option A: bake + symlink).
# Install node_modules/ at /opt/ui-deps/ay_platform_ui/ (OUTSIDE the workspace
# bind mount). devcontainer.json v7's postStartCommand creates a symlink
# from ${LOCAL_WORKSPACE_FOLDER}/ay_platform_ui/node_modules to this baked
# location IF the host has no real node_modules/ already — host-side npm
# always wins, we never fight a developer who runs `npm install` on the
# host. Ownership is fixed to the `developer` user in postCreateCommand
# (after `updateRemoteUserUID` rewrites the runtime UID).
#
# We use `npm install` (not `npm ci`) because no lock file is committed
# yet ; install creates one when absent, honours it when present. The
# generated lock at /opt/ui-deps/... is image-internal — commit a host-
# side lock to switch to deterministic builds.
# -----------------------------------------------------------------------------
COPY ay_platform_ui/package*.json /opt/ui-deps/ay_platform_ui/
RUN cd /opt/ui-deps/ay_platform_ui && npm install --no-audit --no-fund

# -----------------------------------------------------------------------------
# Playwright Chromium pre-install. PLAYWRIGHT_BROWSERS_PATH redirects the
# default (~/.cache/ms-playwright) to a system-wide path that survives
# both the workspace bind mount AND the build-time/runtime user mismatch
# (root at build, developer at runtime — both consult the env var).
# `--with-deps` runs apt-get to add libnss3, libxss1, libasound2, etc.,
# without which Chromium fails to launch on minimal Ubuntu images.
# Ownership is fixed to `developer` in postCreateCommand (after
# updateRemoteUserUID).
# -----------------------------------------------------------------------------
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers
RUN cd /opt/ui-deps/ay_platform_ui \
 && npx --yes playwright install --with-deps chromium \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# Docker group. Its GID is arbitrary and deliberately NOT aligned with the
# host socket's — devcontainer.json v10 chgrp's the SOCKET onto this group at
# postStartCommand instead, which works whatever GID the socket arrives with
# (root:root on Docker Desktop for macOS, where alignment was impossible).
RUN getent group docker || groupadd -g 999 docker

# Non-root developer user
RUN useradd -m -s /bin/bash developer \
 && usermod -aG sudo developer \
 && usermod -aG docker developer \
 && echo "developer ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

USER developer
WORKDIR ${LOCAL_WORKSPACE_FOLDER}

CMD ["/bin/bash"]
