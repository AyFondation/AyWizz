<!-- =============================================================================
File: 2026-09-09-harness-comparatif-et-gate-b.md
Version: 1
Path: .claude/sessions/2026-09-09-harness-comparatif-et-gate-b.md
Description: Full reinstall of the platform on a from-scratch docker-desktop
             cluster (K8s v1.36.1), then a harness survey (14 tools) driven by
             one question: what would let C4 `generate` produce Claude-Code-like
             results. Main finding: the OpenHands engine ALREADY has a
             workspace, three tools and a bounded loop — it runs the tests and
             then DISCARDS the terminal transcript, which is why Gate B blocks.
             The fix is an event-stream mapping, not a rewrite. Also: three
             defects found and two fixed (LLM provider base_url has no
             normalisation; C1 dynamic config loaded twice; file-tree had no
             reachable root drop target). Written because the PRIOR session's
             analysis of the same topic was lost with its conversation and had
             left no artefact on disk.
============================================================================= -->

# Session — Cluster reinstall, harness survey, and the Gate B finding (2026-09-09)

## Context

The operator reset the docker-desktop Kubernetes cluster and lost the
conversation. Two distinct threads followed: bring the platform back up on a
virgin cluster, then resume an analysis of open-source agent harnesses that
the lost conversation had apparently covered — and which, as this session
established, left **no trace anywhere on disk** (sessions journal, learned
rules, SESSION-STATE, requirements corpus, git history, agent memory all
searched; the single hit was a false positive, `deepseek-v4-flash:free` as an
OpenRouter *model* in the 2026-05-19 entry). That gap is the direct reason
this entry exists: §9.2 entries are *proposed*, never automatic, so an
analysis session that ends without validation leaves nothing behind.

## Bloc 1 — Reinstall on a from-scratch cluster

Recovery is two independent breakages, and only the first is self-service:

- **kubeconfig** — rewrite the host mount's `127.0.0.1:6443` to
  `kubernetes.docker.internal:6443` into `~/.kube-local/config`, drop the
  stale CA, `--insecure-skip-tls-verify=true`. Self-service.
- **docker socket** — Docker Desktop recreates `/var/run/docker.sock` as
  `root:root` on every daemon restart. `postStartCommand` (devcontainer.json
  v10) fixes it, but only at CONTAINER start, so a mid-session reset leaves it
  broken. `sudo` is deny-listed: operator action. **This runtime-proved the
  v10 socket fix**, which the 2026-09-02 entry had flagged as unverified.

Deploy flags CHANGED and the old procedure is now wrong:
`infra/k8s/run.sh dev --ingress --workers`, **not** `--crds`. `--crds` is
obsolete since C1 moved to the Traefik file provider; `--ingress` is needed
once per fresh cluster; `--workers` because `C4_DISPATCHER_BACKEND=k8s`.

Result: 16/16 Deployments Available, 2 StatefulSets Ready, 4 bootstrap Jobs
Complete, `tests/system/k8s` 7/7 with ruff + mypy clean. ingress-nginx v1.11.3
came up on K8s **v1.36.1** without trouble — the compatibility risk flagged
beforehand did not materialise. A K8s-only reset does NOT wipe docker images;
check image dates against `git log -1 --format=%ci -- <src dir>` before
rebuilding.

Validation went beyond the smoke tests, which port-forward straight to
`c1-gateway` and therefore bypass the edge. Probing through ingress-nginx with
a real JWT reached **seven distinct backends** (C2, C3, C5, C6, C7, C8-admin,
C9), with forward-auth enforced and the E-100-002 role gate returning
`403 requires one of: platform_manager` on C8 admin.

## Bloc 2 — RBAC review: no cluster rights, with three caveats

Verified on the built manifests, the whole `infra/k8s/` tree, and the live
cluster. The application declares **zero** ClusterRole / ClusterRoleBinding /
CRD / PersistentVolume / StorageClass / IngressClass / webhook, and no
hostPath / hostNetwork / privileged / capabilities. The cluster runs with
**no CRD at all**, and the `traefik` ServiceAccount carries
`automountServiceAccountToken: false` — Traefik cannot reach the API server.
Those two facts together are the concrete proof the file-provider migration
severed API access. All remaining RBAC is one namespaced `Role` (`pods`:
create/get/list/watch/delete), shipped only with `--workers`.

Three caveats keep this short of an unqualified "no cluster rights":

1. `base/_namespace/namespace.yaml` **creates the Namespace** — a
   cluster-scoped write. So "no cluster-scoped RBAC objects" is true, but
   "installable by a namespace-admin" is not. Proposed fix: move the
   Namespace to its own opt-out target (same pattern as `c4_workers` and
   `ingress_nginx`) behind a `--namespace` flag.
2. RBAC escalation-prevention: creating that `Role` requires the installer to
   already hold those pod permissions, or the `escalate` verb.
3. ingress-nginx IS a cluster-admin install (2 ClusterRoles, 2 bindings,
   IngressClass, ValidatingWebhookConfiguration, its own Namespace) — but it
   is outside the app, opt-in, and skippable on any cluster that already has
   a controller.

## Bloc 3 — Three defects

**LLM provider `base_url` has no normalisation (OPEN).** A conversation failed
with `litellm.NotFoundError: AnthropicException - .` — an empty message. The
LiteLLM logs carried the actual cause:
`404 for url 'https://api.anthropic.com//v1/messages'` — a **double slash**,
because the operator-entered base URL ended with `/`. `_ProviderFields.base_url`
is `Field(min_length=1)`: any non-empty string passes. Trailing slash is the
most ordinary way a human pastes a base URL, and it produces a TOTAL outage
whose error message is empty and surfaces raw in the user's conversation.
Proposed: a `field_validator` applying `.rstrip("/")` plus a scheme check —
one place covering the HMI upsert, the stored document and the seed. Touches a
public Pydantic contract, so §8.4 applies. Does not repair existing rows.

**C1 dynamic config loaded twice (OPEN).** Traefik logs exactly 28 routers +
11 services + 3 middlewares as `already configured, skipping` — matching the
declared counts precisely (`grep -c "rule:"` = 28, `loadBalancer:` = 11). The
file provider walks `/etc/traefik/dynamic`, a ConfigMap volume that K8s
materialises as symlinks plus a hidden `..data` directory, so every file is
parsed twice. Effect today is nil (the routing table is correct, proven by
access logs), but it would **silently swallow a genuine duplicate**. Proposed:
point the provider at `/etc/traefik/dynamic/..data`, which preserves
`watch: true`; the `subPath` alternative kills live reload. Both need
empirical confirmation. Verify with
`kubectl exec -n aywizz deploy/c1-gateway -- ls -la /etc/traefik/dynamic`.

**File-tree had no reachable root drop target (FIXED, deployed).** v3's root
drop zone was the scroll container, reachable only by event bubbling through
its empty space — and that container is as tall as its content, leaving 4px of
`py-1`. With any directory present, moving a file back to the repository root
was impossible. `file-tree.tsx` v5 adds an explicit `/` row, always rendered.
Deliberately NOT a persisted artifact: the backend surfaces files only
(`_blob_to_node`, `kind="file"`, "pseudo-dirs are inferred client-side") and
`buildTree` drops anything that is not a file, so a stored root node would
have nothing to attach to. Being synthetic is what makes it impossible to
delete — a property by construction, not a guard to enforce. Same release
closed the accidental affordance that made the old zone usable at all: a drop
on a FILE row bubbled to the container and silently relocated to root. UI CI
468/468, four coverage thresholds held. Deployed and digest-verified against
the pod's `imageID` (no phantom deploy).

## Bloc 4 — Harness survey (14 tools)

Sources: `github/spec-kit`, `deepseek-ai/deepseek-harness`, then a French
comparator (`ia-top.fr`, 403 on the whole domain — read from an operator
screenshot). Three non-negotiable filters cut 14 to 4: multi-provider
agnosticism (D-011) drops Claude Code / Codex CLI / Gemini CLI; permissive
licence drops Plandex / GPT Pilot / Hermes / Copilot CLI and **Crush**
(FSL-1.1-MIT is source-available, not OSI, MIT only after two years);
context management beyond basic drops Goose and Pi.

**Integration criterion is the stack, not quality.** OpenCode is TypeScript /
bun, dsh is Node/TS in developer preview: adopting either means a second
runtime tier under a Python 3.13 backbone. The OpenHands SDK is Python + MIT,
exposes Agent / Tools / Workspace / Conversation / Event model / Agent Server
with ephemeral **Docker or Kubernetes** workspaces — infrastructure this
platform already has. It is the only credible integration candidate, and it is
already the one wired behind the `generate_engine` seam.

Mechanisms worth taking, none of which require adopting a harness:

- **Aider** — the lint/test → repair loop (`--auto-test` + `--test-cmd`; exit
  code and error output fed back to the model); the repo map built by graph
  ranking over a file-dependency graph within a token budget
  (`--map-tokens`, default 1000, dynamically expanded); per-model-family edit
  formats (`diff`, `diff-fenced` for Gemini's fencing failures, `udiff`
  against GPT-4 Turbo's lazy coding) and the **architect/editor** split, which
  maps directly onto the existing per-role `agent_routes`.
- **OpenCode** — `plan` / `build` agent profiles as a privilege boundary
  (read-only, asks before bash, restricted file edits vs full access). The
  platform's agents currently have **no permission engine at all**, and this
  split matches the phase gates.
- **dsh** — Landlock as OS-level hardening INSIDE the pod, the layer container
  isolation does not cover.
- **SWE-bench** — `FAIL_TO_PASS` / `PASS_TO_PASS` as the shape of Gate B
  evidence, and as the measurement instrument that is currently absent.

`spec-kit` contributes little: its `constitution → specify → plan → tasks →
implement → converge` cycle is what this platform already does, with a real
requirements service behind it. Two ideas are worth the theft: the
**converge** pass (compare real code against the artefacts, loop until the gap
closes — the dynamic cousin of the static `060-IMPLEMENTATION-STATUS.md`), and
a **constitution** injected into the platform's own agent prompts. Do NOT
import its posture of gitignoring `specs/`.

Explicitly rejected: AutoGen / CrewAI / LangGraph. Multi-agent orchestration
is what C4 already is; adding one would be a redundant layer.

## Bloc 5 — THE finding: the evidence is produced, then thrown away

Two claims made earlier in this session were **wrong** and are corrected here:

- The adapter is NOT on stale packaging. `pyproject.toml` pins
  `openhands-sdk>=1.23,<2.0` + `openhands-tools>=1.23,<2.0`, and the imports
  (`openhands.sdk`, `openhands.tools.{terminal,file_editor,task_tracker}`)
  match the restructured SDK exactly.
- "The platform has no agent loop with an execution environment" is true only
  of `_sub_agent/runtime.py` (bundle → inlined context → ONE LLM call → parse
  → exit, zero tools). It is **false** of the gated `openhands` engine.

`_default_runner` already builds a real workspace (`tempfile.mkdtemp`), an
`Agent` with `TerminalTool` + `FileEditorTool` + `TaskTrackerTool`, and a
`Conversation` bounded by `max_iteration_per_run`, routed through C8/LiteLLM
with per-turn attribution headers (R-200-035).

**So the agent can already run the tests. What is missing is that nobody keeps
the record.** `_RunOutcome` carries only `workspace` + `execution_status` +
`detail`; `_collect_files` reads back the FILES; then
`shutil.rmtree(outcome.workspace)`. The terminal transcript — commands, exit
codes, pytest output, i.e. exactly the red→green proof — is never captured.
`_map_outcome` says so in its own docstring: "no `gate_b_evidence` is emitted
— the engine does not fabricate red-first proof". Gate B therefore blocks, and
that is correct behaviour, not a defect.

The gap is not the loop. **The loop runs the tests and the evidence is
discarded.** The blocked-completion message already names the fix's location:
"Inspect the OpenHands event stream".

Scope: capture the `Conversation` event stream, extract terminal actions and
their observations (command, exit code, output), map test invocations into
`gate_b_evidence` in `FAIL_TO_PASS` / `PASS_TO_PASS` shape. Three edit points
— `_RunOutcome`, `_default_runner`, `_map_outcome`. The injectable `runner`
stays the sole importer of `openhands.*` (R-200-029), so it remains testable
with a fake and without the heavy extra.

## Open questions

- **Does the OpenHands SDK event stream expose command / exit code / output on
  terminal observations?** Unverified — the README defers to
  `docs.openhands.dev/sdk` and `examples/`. This single answer decides whether
  the Gate B work is small and bounded or requires instrumenting the tool
  layer. **Verify this first.**
- R-200-029 remains PROPOSED, not ratified. Emitting `gate_b_evidence` moves
  the POC from "blocks honestly" to "proves", which is what unblocks the §8.1
  spec amendment and settles Q1 (single-shot vs gated sub-steps) empirically.
- `base_url` normalisation and the C1 double-load both await a decision.

## Verification

`run_tests.sh k8s-systest` — ruff OK, mypy OK, pytest 7/7
(`reports/2026-09-08_1554_k8s-systest/`). UI: `test:coverage` 468/468 with
statements 82.42 / branches 70.72 / functions 82.87 / lines 85.99; lint and
typecheck clean. Full backend `run_tests.sh ci` was NOT run this session — no
application code was touched outside `ay_platform_ui`.
