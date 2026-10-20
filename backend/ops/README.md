# Ops hand-off

Files here belong outside `backend/`, which the backend developer doesn't
edit. The DevOps engineer moves each one into place in its own PR.

| File | Move to | Milestone |
|---|---|---|
| `backend-ci.yml` | `.github/workflows/backend-ci.yml` | M0 |

## M0 checklist (repo settings)

1. **CI:** copy `backend-ci.yml` into `.github/workflows/`. It runs `ruff`,
   `pytest`, `kb_build --check --allow-empty`, `pip-audit` and `gitleaks`.
   Drop `--allow-empty` once the team's first `kb/` rows are merged.
2. **Branch protection on `main`:** add the `backend` and `secret-scan` checks
   to the required status checks, next to the existing `test` check.
   The workflow runs on every PR (no path filter), so a required check never
   sits waiting on a PR that doesn't touch `backend/`.
3. **CODEOWNERS:** add a line so `kb/` changes need a second reviewer from
   the team (plan, section 7 item 13), for example
   `/backend/kb/ @<teammate-1> @<teammate-2>`.
4. **Dependabot:** add a `pip` entry for `/backend` (weekly), so pinned
   server packages get update PRs that CI then audits and tests.
5. **One-time history scan:** run `gitleaks detect` over the full history
   once and rotate anything it finds (plan, section 7 item 8).
