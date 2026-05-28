# Contributing to AyWizz

Thank you for your interest in AyWizz. This document explains how to
submit changes that we can accept and merge.

## TL;DR

1. Open or pick up an issue first so the direction is agreed before code
   is written.
2. Write your change in a branch on your own fork. Keep the diff focused.
3. Sign off every commit with `git commit -s` (Developer Certificate of
   Origin — see below).
4. Open a pull request against `main`. CI will run lint, type checks,
   tests, the coherence engine, and the DCO check.
5. Address review feedback. Once approved, your change is merged.

If anything below is unclear, open a discussion or an issue — we'd
rather answer your question than have you guess.

---

## License of contributions

AyWizz is licensed under the **GNU Affero General Public License,
version 3.0** (see [LICENSE](LICENSE)). By submitting a contribution to
this repository — through a pull request, patch, or any other means —
you agree that your contribution is made available under the same
AGPLv3 license and may be redistributed by the project under those
terms.

If you have any concerns about contributing under AGPLv3 (for example,
your employer's IP policy may need explicit clearance), please raise
them before submitting the contribution. We will not ask you to assign
copyright to the project, and you retain ownership of your code; the
DCO sign-off (see below) is the only certification we require.

A separate **commercial license** of AyWizz is also offered by the
Licensor to organizations that cannot operate under AGPLv3 (see
[LICENSE-COMMERCIAL.md](LICENSE-COMMERCIAL.md)). Contributing to the
AGPLv3 codebase does not entitle you to a free commercial license, but
your AGPLv3-licensed contribution may, at the Licensor's discretion, be
included in commercially-licensed builds of AyWizz.

---

## Developer Certificate of Origin (DCO)

Every commit in every pull request must include a `Signed-off-by:`
trailer. This is the **Developer Certificate of Origin** (DCO), a
lightweight contributor agreement used by the Linux kernel, Docker, and
many other open-source projects. By signing off, you certify that you
wrote the code yourself, or that you have the right to submit it under
the project's license. The full text of the DCO is at
<https://developercertificate.org>.

### How to sign off

Use the `-s` flag when you commit:

```bash
git commit -s -m "fix(c4): handle empty plan in orchestrator"
```

Git appends a line at the end of the commit message:

```
Signed-off-by: Your Name <your.email@example.com>
```

The name and email must match the ones configured in `git config`. You
can verify with:

```bash
git config user.name
git config user.email
```

### What if I forgot to sign off?

If your last commit isn't signed:

```bash
git commit --amend --signoff
git push --force-with-lease
```

If several earlier commits in the branch aren't signed:

```bash
git rebase --signoff main
git push --force-with-lease
```

The CI's DCO check (see `.github/workflows/dco.yml`) will block the
merge until every commit is signed off.

---

## Workflow expectations

### Before opening a pull request

- **Agree the direction first.** For anything beyond a typo or trivial
  fix, open an issue (or comment on an existing one) describing what
  you intend to change and why. This avoids wasted effort if the
  direction would not be accepted.
- **One concern per pull request.** A PR that mixes a bug fix, a
  refactor, and a new feature is harder to review and harder to revert.
- **Match the codebase conventions.** AyWizz uses Python 3.13 with
  strict typing, `ruff` for lint, `mypy --strict` for type checks, and
  the test wrapper at `ay_platform_core/scripts/run_tests.sh ci` as the
  authoritative quality gate. See [`CLAUDE.md`](CLAUDE.md) for the
  full conventions; the same rules apply to human and AI contributors.
- **Update the relevant spec when behavior changes.** If your change
  alters platform-visible behavior, the corresponding requirement in
  `requirements/` must be updated in the same pull request. The
  coherence engine will fail the build otherwise.

### Pull request requirements

The CI runs the following on every PR; all must be green for merge:

| Check | Where | Blocks merge |
|---|---|---|
| `ruff check` (lint) | `ci-tests` | ✓ |
| `mypy` (type check) | `ci-tests` | ✓ |
| `pytest` (unit + contract + integration) | `ci-tests` | ✓ |
| 80% line-coverage gate | `ci-tests` | ✓ |
| Coherence engine (spec ↔ code) | `ci-tests` | ✓ |
| `npm run lint` / `typecheck` / `test` (UI only) | `ci-ui-tests` | ✓ |
| K8s manifest validation | `ci-k8s-validate` | ✓ |
| DCO sign-off on every commit | `dco` | ✓ |

You can run the same checks locally:

```bash
# Backend
bash ay_platform_core/scripts/run_tests.sh ci

# UI (if your change touches ay_platform_ui/)
npm --prefix ay_platform_ui run lint
npm --prefix ay_platform_ui run typecheck
npm --prefix ay_platform_ui run test
```

### Review and merge

- A reviewer (currently the Licensor) will review your PR. Expect
  feedback within a few business days. Larger changes take longer.
- Address feedback by pushing additional commits to the same branch —
  do not force-push during the review (the reviewer's threaded
  comments will detach). Squash on merge.
- Once approved and CI is green, the PR is squash-merged into `main`.
  The merge commit message becomes the squashed commit subject and
  body; please keep your PR description tidy.

---

## Code of conduct

Be civil, be specific, attack the idea not the person, and assume good
faith. Disagreements over technical direction are welcome and necessary;
personal attacks, harassment, or discrimination are not and will result
in the contribution being declined and, in severe cases, the contributor
being blocked from the project.

---

## Questions?

- For general questions about the project: open a discussion.
- For questions about a specific change: open an issue.
- For questions about commercial licensing: see
  [LICENSE-COMMERCIAL.md](LICENSE-COMMERCIAL.md).

Thank you for contributing.
