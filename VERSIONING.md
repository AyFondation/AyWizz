<!--
File: VERSIONING.md
Version: 1
Path: VERSIONING.md
-->

# Versioning policy

This document defines how AyWizz versions its public artefacts. It is the
single authoritative reference for both human contributors and the CI
pipeline. Internal per-file / per-entity versioning (CLAUDE.md §4.3 and
the spec methodology) is orthogonal and is described in §11 below.

---

## 1. Standard adopted

AyWizz follows **Semantic Versioning 2.0.0** (<https://semver.org>).

The platform is currently in the `0.y.z` initial-development phase (also
known as "ZeroVer"). Per SemVer §4, anything **MAY** change between
`0.y.z` releases. The criteria for promotion to `1.0.0` are listed in §8.

---

## 2. Scope of the public API

Versioning applies to the **platform's public surface**, defined as:

- **HTTP endpoints** exposed by C1 Traefik (paths, methods, request and
  response schemas, status codes, header contract).
- **Pydantic models** registered through `register_contract(...)` in
  `ay_platform_core/tests/fixtures/contract_registry.py` (per
  CLAUDE.md §8.4).
- **NATS event payloads** published by any component on the shared bus.
- **Environment variables** consumed by deployed components (the schema
  defined under `infra/<component>/config/`).
- **CLI entry points** shipped by `ay_platform_core` (e.g. wrapper
  scripts users invoke directly).

Versioning does **NOT** apply to:

- Internal module layout, private classes, helper functions.
- File paths inside the monorepo (except those listed above).
- Dev tooling (`scripts/`, devcontainer, lint config).
- Spec files under `requirements/` — they have their own per-entity
  `version:` field (see §11).
- Container image build internals (Dockerfile layers, base image
  versions). The image tag itself follows §10.

---

## 3. Version components

A release is identified by `MAJOR.MINOR.PATCH[-pre-release][+build]`.

| Bump | Trigger |
|---|---|
| **MAJOR** | Backward-incompatible change to any element of the §2 public API (removed endpoint, renamed field, type change, new required field without default, removed env var, etc.). |
| **MINOR** | Backward-compatible addition (new endpoint, new optional field, new env var with default, deprecation notice). |
| **PATCH** | Backward-compatible bug fix or internal-only change (refactor, performance, documentation, dependency bump that preserves the §2 surface). |

During the `0.y.z` phase, `0.MINOR` is bumped on any user-visible
change (additive or breaking) and `0.MINOR.PATCH` is bumped on
internal-only fixes. The strict MAJOR/MINOR/PATCH discipline kicks in
at `1.0.0`.

---

## 4. Pre-release labels

A release `X.Y.Z` may go through three pre-release stages before the
final release. Each stage is identified by a SemVer pre-release suffix
of the form `-<label>.<N>`, where `<N>` is a monotonic integer per
label, per `X.Y.Z`.

| Stage | Suffix | Meaning | Audience |
|---|---|---|---|
| Alpha | `X.Y.Z-alpha.N` | Internal milestone. Scope still being refined. Public API of `X.Y.Z` **may** still change between alphas. | Maintainers, AI assistants generating code, early integrators willing to absorb churn. |
| Beta | `X.Y.Z-beta.N` | Feature-complete for `X.Y.Z`. Public API frozen for `X.Y.Z`. Bugs expected. Breaking changes between betas are exceptional and require a written justification in the release notes. | Adventurous users, integration partners running staging environments. |
| RC | `X.Y.Z-rc.N` | Release candidate. Only critical or release-blocker fixes accepted. If a fix is needed, a new `-rc.(N+1)` is cut. | Production integrators rehearsing the upgrade. |
| Release | `X.Y.Z` (no suffix) | Final release. Supported per the release-support policy (TBD). | Everyone. |

Lexicographic ordering of SemVer pre-release identifiers naturally gives
`alpha < beta < rc < (no pre-release)`, so tools that resolve "latest"
correctly skip pre-releases unless explicitly opted in.

### 4.1 Pre-release lifecycle

```
X.Y.Z-alpha.1 -> X.Y.Z-alpha.2 -> ... -> X.Y.Z-alpha.N
                                              |
                                              v
                                       X.Y.Z-beta.1 -> ... -> X.Y.Z-beta.N
                                                                   |
                                                                   v
                                                            X.Y.Z-rc.1 -> ... -> X.Y.Z-rc.N
                                                                                       |
                                                                                       v
                                                                                   X.Y.Z
```

Stage promotion criteria:

- **alpha -> beta**: every `R-NNN-XXX` requirement scoped to `X.Y.Z`
  has a corresponding `@relation implements:` marker; coherence engine
  green; `requirements/060-IMPLEMENTATION-STATUS.md` reports no
  `not-yet` for the release scope.
- **beta -> rc**: no known critical or major defect; contract registry
  stable for ≥ 1 working day; integration tier green on the
  release branch.
- **rc -> release**: `rc.N` held ≥ 3 working days without a critical
  defect report from any integrator. No code change since `rc.N` other
  than version-string bumps and release notes.

A regression discovered at any stage rewinds the version one stage
back (e.g. a critical defect at `rc.1` cuts `rc.2`; a public-API change
needed at `beta.2` cuts `beta.3`; a scope addition at `beta.3` requires
restarting at `alpha` for the new scope OR deferring it to `X.Y.(Z+1)`).

---

## 5. Build metadata

Optional build metadata MAY follow the version with a `+` separator
(SemVer §10): `0.3.0-beta.1+sha.a1b2c3d`. Build metadata is **ignored**
when comparing versions and SHALL NOT carry semantic meaning. The CI
pipeline appends `+sha.<short-commit>` automatically to all image tags
for traceability (see §10).

---

## 6. Examples

| Situation | Resulting version |
|---|---|
| Add an optional field `description` to `POST /requirements` | MINOR bump: `0.3.0` -> `0.4.0` |
| Rename `POST /chat/send` to `POST /chat/messages` | MAJOR bump: `0.4.0` -> `0.5.0` (still `0.x`, breaking still allowed) |
| Fix a 500 when payload is empty | PATCH: `0.4.0` -> `0.4.1` |
| Cut the first alpha of the `0.5.0` cycle after the rename | `0.5.0-alpha.1` |
| Found a critical bug at rc.1 | Fix on the release branch, cut `0.5.0-rc.2` |
| `0.5.0-rc.2` quiet for 3 days, promote | `0.5.0` |

---

## 7. Initial `0.y.z` phase

The platform is in `0.y.z` while:

- The contract registry is still gaining entries (some component
  contracts are not yet declared).
- The C1 ingress contract is still being shaped.
- The UI tier (`ay_platform_ui/`) is in scaffold.
- The C13 / C14 / C15 components are partially implemented.

During this phase, version progression is driven by milestones, not by
calendar. There is no commitment of backward compatibility between
`0.y` and `0.(y+1)`.

---

## 8. Criteria for `1.0.0`

`1.0.0` is cut when **all** of the following hold:

1. Every component declared in `requirements/100-SPEC-ARCHITECTURE.md`
   (§5 component decomposition) is implemented and integration-tested.
2. The contract registry covers 100% of the public API as defined in
   §2 above (verified by the coherence engine).
3. The platform has been deployed end-to-end (Traefik + ArangoDB +
   MinIO + n8n + every Python component + UI) at least once on a real
   K8s cluster (not Docker Desktop).
4. At least one external integrator (i.e. not the Licensor) has
   integrated AyWizz against the public API.
5. No `divergent` entry in `requirements/060-IMPLEMENTATION-STATUS.md`.

Bumping to `1.0.0` is a deliberate decision that goes through the
standard decision channel (`requirements/999-SYNTHESIS.md`) and is
recorded as a `D-XXX` entry.

---

## 9. Git tag conventions

Releases are marked by **annotated** git tags on the `main` branch.

| Stage | Tag form | Example |
|---|---|---|
| Alpha | `vX.Y.Z-alpha.N` | `v0.5.0-alpha.1` |
| Beta | `vX.Y.Z-beta.N` | `v0.5.0-beta.2` |
| RC | `vX.Y.Z-rc.N` | `v0.5.0-rc.1` |
| Release | `vX.Y.Z` | `v0.5.0` |

The `v` prefix is the de facto SemVer convention and disambiguates
version tags from non-version tags. The tag message SHOULD reference
the release notes and the PR / commit range it covers.

Pre-release tags (`alpha`, `beta`, `rc`) are immutable once pushed and
SHALL NOT be retro-active deleted. A mistake at any stage is fixed by
cutting the next `.N`.

The legacy `pre-alpha-XXX` tags currently on `main` predate this
policy. They are preserved as historical milestones and not retro-fitted
to SemVer.

---

## 10. CI / image-tag mapping

The `ci-build-images` workflow publishes container images to GHCR
(`ghcr.io/ayfondation/aywizz-api`, `…/aywizz-ui`,
`…/aywizz-c13-extractor`) with tags derived from the git event.

| Git event | Image tags pushed |
|---|---|
| Push to `main` | `:sha-<short>`, `:main` |
| Tag `vX.Y.Z-alpha.N` | `:sha-<short>`, `:X.Y.Z-alpha.N`, `:alpha` (floating) |
| Tag `vX.Y.Z-beta.N` | `:sha-<short>`, `:X.Y.Z-beta.N`, `:beta` (floating) |
| Tag `vX.Y.Z-rc.N` | `:sha-<short>`, `:X.Y.Z-rc.N`, `:rc` (floating) |
| Tag `vX.Y.Z` (release) | `:sha-<short>`, `:X.Y.Z`, `:X.Y`, `:X`, `:latest` (floating) |

The floating tags (`:alpha`, `:beta`, `:rc`, `:latest`, `:X`, `:X.Y`)
are convenience pointers and are NOT used by the production overlay
(per `R-100-114 v2`, production pins `:sha-<short>` or `:X.Y.Z`).

---

## 11. Articulation with internal versioning

The repo already maintains three internal versioning layers. They are
**orthogonal** to the public SemVer policy and remain in force:

| Layer | Format | Source of truth | Purpose |
|---|---|---|---|
| Per-file header | `Version: N` (monotonic int) | CLAUDE.md §4.3 | Per-delivery audit of generated artefacts. |
| Per-spec-entity frontmatter | `version: N` + `status: draft/approved/superseded` | `requirements/meta/100-SPEC-METHODOLOGY.md` | Per-requirement traceability. |
| Per-spec-document frontmatter | `version: N` + leading "Version N changes" log | Spec methodology | Cross-cutting change history per spec file. |

These internal layers are NOT exposed to external users. A bump to a
file header does not imply a SemVer bump; a SemVer release does not
imply bumping every file header.

---

## 12. Release notes

Each release SHALL ship release notes under `.claude/sessions/` (per
CLAUDE.md §9.2) AND a top-level `CHANGELOG.md` entry. The
`CHANGELOG.md` follows the "Keep a Changelog" convention
(<https://keepachangelog.com>) with the standard sections: `Added`,
`Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`. Pre-release
entries are grouped under the target release header.

`CHANGELOG.md` does not exist yet and will be initialised when the
first `0.1.0` cycle starts.
