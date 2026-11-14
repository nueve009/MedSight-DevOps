# Ops hand-off

Files here belong outside `backend/`, which the backend developer doesn't
edit. The DevOps engineer moves each one into place in its own PR.

| File | Move to | Milestone |
|---|---|---|
| `backend-ci.yml` | `.github/workflows/backend-ci.yml` (replace it) | M0, updated 2026-11-14 |
| `llm-eval-live.yml` | `.github/workflows/llm-eval-live.yml` | 2026-11-14 |

## 2026-11-14 checklist (final checking)

1. Replace `.github/workflows/backend-ci.yml` with `backend-ci.yml` from here.
   It adds a test summary table on the run page, the offline LLM evaluation
   cases, and the JUnit and eval reports as run artifacts. Job names don't
   change, so the required checks stay the same.
2. Add `llm-eval-live.yml`, and a `GROQ_API_KEY` secret to the `staging`
   environment (a separate Groq key from Railway's is better, so a bad run
   can't use up the app's limit). Start it once with "Run workflow".
3. In the `protect-main` ruleset, require 1 approval, so the change-to-deploy
   demo shows a teammate's review being enforced.
4. In Railway, turn on "Wait for CI" for the backend service, so a merge
   deploys only after the checks on `main` pass.

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
