# =============================================================================
# Dockerfile for Ubuntu Dev Environment
# Version: 1.9.0
# Path: .devcontainer/Dockerfile
# Description: Development container with Python 3.13, Node.js LTS, kubectl,
#              Claude Code CLI. Python 3.13 installed via deadsnakes PPA.
#
# Versions:
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

# Install Node.js (latest LTS) - required for Claude Code CLI
RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
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

# Docker group (GID will be re-aligned at container start by postCreateCommand)
RUN getent group docker || groupadd -g 999 docker

# Non-root developer user
RUN useradd -m -s /bin/bash developer \
 && usermod -aG sudo developer \
 && usermod -aG docker developer \
 && echo "developer ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

USER developer
WORKDIR ${LOCAL_WORKSPACE_FOLDER}

CMD ["/bin/bash"]
