<!-- =============================================================================
File: 2026-09-02-markdown-rendering-and-devcontainer-socket.md
Version: 1
Path: .claude/sessions/2026-09-02-markdown-rendering-and-devcontainer-socket.md
Description: Closed Q-500-003 — Markdown rendering across every Markdown-bearing
             UI surface via a shared `<MessageBody>` (react-markdown + remark-gfm,
             chosen on SECURITY grounds over `marked`). Live-docs is the one
             opt-in surface (Source default + Preview toggle, R-500-005 v3).
             Recovery session: the operator rebuilt the devcontainer mid-feature
             and lost the conversation (not the code). Found and fixed a
             STRUCTURAL devcontainer defect introduced by devcontainer.json v9 —
             the docker-socket GID alignment cannot work on Docker Desktop for
             macOS, silently breaking every `docker` call and producing a phantom
             deploy (rebuilt image never rolled out). UI CI: 465/465, 4 coverage
             thresholds. Deploy still pending on the operator's side.
============================================================================= -->

# Session — Markdown rendering (Q-500-003) + devcontainer socket fix (2026-09-02)

## Context

Two entangled threads. The operator was adding Markdown rendering to the UI
(the reported defect: `**Rome**` displayed with literal asterisks in chat).
Adding `react-markdown` + `remark-gfm` to `package.json` required rebuilding
the devcontainer image (UI deps are baked into `/opt/ui-deps` and symlinked
into the workspace, per devcontainer.json v7). That rebuild cost the
conversation — the operator resumed with "I lost everything".

Nothing was lost: the entire feature was on disk, uncommitted. What the
interrupted session had not reached was its own closing checks (CLAUDE.md
§12), which is where the remaining defects were hiding.

## Bloc 1 — Q-500-003 resolved (was already on disk)

`components/message-body.tsx` — one shared renderer, wired into four surfaces:
chat bubbles (both roles), the chat sidebar, the requirements document view,
and the live-docs viewer. A constrained component map keeps block spacing
chat-sized, scales headings down, scrolls wide tables/code inside their own
box (the bubble must never scroll horizontally), and hardens links
(`target="_blank"` + `noopener noreferrer` — without `noopener` the opened
page keeps a live `window.opener` handle back into this origin).

**The library choice is a security decision, not a bundle-size one.** The
content rendered here is LLM output, and LLM output is influenceable by
whatever was ingested into the RAG corpus — untrusted by construction.
`marked` emits an HTML *string*, mountable only via
`dangerouslySetInnerHTML`; that opens an XSS path that then has to be closed
by never forgetting to sanitise. `react-markdown` emits React elements and
escapes raw HTML, closing the hole by construction. `rehype-raw` would
re-open exactly that hole and is forbidden by the spec. A dedicated test
asserts the escape, so adding `rehype-raw` breaks the build — which is the
point.

The Server-Component / MDX path (the other option Q-500-003 named) was
rejected as structurally unfit: the assistant bubble is filled by a
client-side SSE stream token by token, and a server render cannot follow an
in-flight stream — it would only ever cover persisted messages, never the
live row. Partial markdown mid-stream needs no guard: react-markdown renders
what currently parses and reflows as the rest arrives (tested).

## Bloc 2 — the two unfinished closing checks

1. **Formatting.** `message-body.tsx` had never passed biome. Fixed.
2. **One red test.** `live-docs-manager.test.tsx` asserted the raw string
   `# Hello`; the new wiring rendered it as `<h1>`. Diagnosed §10.3 as case
   **D** (intentional contract change), NOT an implementation defect.

## Bloc 3 — live-docs: rendering made opt-in (R-500-005 v3)

Rather than adapt the assertion, challenged the wiring itself. Of the four
surfaces, live-docs is the only questionable one: it is a file *manager*: the
read pane sits beside a raw textarea editor, and what an operator verifies
before editing — indentation, YAML frontmatter of spec files, exact offsets —
is the source, which a rendering hides. The operator chose the toggle.

`LiveDocsManager` v3 gains a Source ↔ Preview button, **default Source**,
sticky across file selections (a view mode, not per-file state). Side effect:
the red test goes green **unmodified** — the old assertion is once again the
correct expression of the default behaviour, so no §10.4 test modification
was needed at all. A new test covers the toggle round-trip.

Spec updated: `500-SPEC-UI-UX` v5 → **v6**, `R-500-005` v2 → **v3** (the
opt-in exception stated explicitly, with its rationale), Q-500-003 resolution
note extended.

## Bloc 4 — the phantom deploy, and the devcontainer defect under it

The operator rebuilt the images and relaunched. The feature was still absent
from the running app. Three facts, no inference from pod age alone:

- `ay-platform-ui` last rolled at **2026-09-02 11:00:40 CEST**; the Markdown
  deps landed at **15:53** — ~5h later.
- The pod's `imageID` is `docker-pullable://ghcr.io/ayfondation/aywizz-ui@sha256:02dc0d96…`
  — a **registry digest**. A locally built image has no repo digest, so this
  pod has never served a local build.
- `image: …:latest` + `imagePullPolicy: IfNotPresent`: rebuilding under the
  same tag leaves the Deployment spec byte-identical, so `kustomize apply` is
  a no-op, no new ReplicaSet is created, and the pod keeps its old image.
  This is precisely why `run.sh` v4 has `--restart`.

Underneath sat a **structural defect introduced by devcontainer.json v9**.
v9 dropped the `docker-outside-of-docker` feature and asserted that "the
docker-group-GID alignment in postCreateCommand" still granted socket access.
It cannot. That code moved the **group onto the socket's GID**:

```
DOCKER_SOCK_GID=$(stat -c "%g" /var/run/docker.sock || echo 999)   # → 0
sudo groupmod -g 0 docker 2>/dev/null                              # fails: GID 0 is root's, not movable
  || sudo groupadd -g 0 docker                                     # fails: group already exists
sudo usermod -aG docker developer                                  # adds to docker, still 999
```

Both failures swallowed by `2>/dev/null` and `;`. Observed end state:
`developer` in docker=999, socket `root:root` mode 660 → every `docker` call
returns `permission denied ... /var/run/docker.sock`. On Docker Desktop for
macOS the bind-mounted socket is **always** root:root, so the strategy is
wrong for the platform, not merely mis-executed. It silently broke
`infra/scripts/k8s_build_images.sh` and testcontainers.

**Fix (devcontainer.json v10)** — invert the direction: move the **socket
onto the group** (`chgrp docker` + `chmod g+rw`), correct whatever the
socket's original group, so the special case disappears instead of being
caught. Placed in `postStartCommand`, NOT `postCreateCommand`: Docker Desktop
recreates the socket root:root on every daemon restart, so a create-time
one-shot does not hold. The whole groupmod/groupadd/usermod block is dropped
— `developer`'s membership of `docker` is already granted in the Dockerfile,
which is now the only membership step (a comment guards against removing it).
`Dockerfile` 1.12.1 is comments-only: three assertions about the GID
alignment were false and are corrected.

Security note recorded in the file: the socket becomes reachable by the
container's `docker` group = root-equivalent on the Docker Desktop VM. That
is inherent to the DooD design (unchanged since v5), not introduced here. The
chgrp mutates the inode inside the Linux VM only, never the macOS filesystem.

**Not verified at runtime**: `sudo` is deny-listed for Claude, so the chgrp
could not be executed nor `docker info` observed. `postStartCommand` fires on
the next container start; the equivalent one-liner unblocks the current one.

## Collateral

The operator's `rm node_modules` removed the **tracked symlink**
`ay_platform_ui/node_modules → /opt/ui-deps/ay_platform_ui/node_modules`, not
just a directory — `git status` showed `D`, and the workspace had no
dependencies at all. Restored identically. `/opt/ui-deps` had survived and
contains the Markdown deps, confirming the devcontainer image was correctly
rebuilt with the new `package.json`.

## Verification

`npm run ci` (lint → typecheck → coverage): **54/54 files, 465/465 tests**,
coverage 82.43 stmts / 70.68 branches / 82.91 funcs / 85.99 lines — all four
thresholds passed. Re-run after the symlink restore: identical.

No backend code changed; backend CI not re-run.

## Still open at close

- **Deploy**: `infra/scripts/k8s_build_images.sh --ui-only` then
  `infra/k8s/run.sh dev --restart`. The `--restart` is what was missing.
- **Devcontainer v10 unproven**: needs a container restart (or the manual
  `sudo chgrp docker /var/run/docker.sock`) plus a `docker info` check.
- Everything in this session is **uncommitted**, alongside a large unrelated
  `infra/k8s` workstream (ingress-nginx, networkpolicies, `.env.config`) that
  must not be bundled into the same commit.

## Lesson candidate (§7)

Same-tag image rebuild + `imagePullPolicy: IfNotPresent` = silent no-op
deploy. Worth a `.claude/learned/` rule: after any `k8s_build_images.sh`,
`run.sh --restart` is mandatory, and a deploy claim SHALL be backed by a pod
age or `imageID` check rather than by the build succeeding.
