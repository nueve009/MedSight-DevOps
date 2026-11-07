# CI/CD

How a change gets from a commit to a running app in this project, with the numbers and problems behind it.

Path of a change: branch, pull request, checks pass, merge into `main`, Android build.

## Overview

| Stage              | What happens                                                                                                | File                                 |
| ------------------ | ----------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| Frontend checks    | Lint and type check on every pull request and every push to `main`                                          | `.github/workflows/ci.yml`           |
| Backend checks     | ruff, pytest (also on a real Postgres), `kb/` data check, dependency audit, secret scan, Docker image build | `.github/workflows/backend-ci.yml`   |
| Merge rules        | Pull request required, the `test` check must pass                                                           | Branch ruleset on `main`             |
| Mobile build (CD)  | After a merge, builds the Android `.apk` with Expo EAS Build                                                | `.github/workflows/mobile-build.yml` |
| Dependency updates | Weekly GitHub Actions updates                                                                               | `.github/dependabot.yml`             |

## Continuous integration

### Frontend checks (`ci.yml`)

- Runs on every push to `main` and every pull request into `main`
- Job `test`: install packages (`npm ci`), lint (`npm run lint`), type check (`npx tsc --noEmit`)
- The job must keep the name `test`, because the branch rule depends on it

Run the same checks on your laptop:

    npm ci
    npm run lint
    npx tsc --noEmit

Safety settings in `ci.yml`:

- `permissions: contents: read` so the pipeline can only read the code
- `concurrency` with cancel-in-progress, so an old run stops when a newer push arrives
- `timeout-minutes: 10` so a stuck job is stopped

### Backend checks (`backend-ci.yml`)

Written by the backend teammate. It runs on every push to `main` and every pull request into `main`, with no path filter. It has three jobs:

- `backend`: ruff, `pytest`, `kb/` data check (with `--allow-empty`), and `pip-audit`
- `secret-scan`: gitleaks over the full history
- `docker-build`: builds the backend Docker image (see below)

The `backend` job starts a throwaway `postgres:16` container as a service and sets `MEDSIGHT_TEST_DATABASE_URL` to it. This makes the Postgres half of `backend/tests/test_store.py` run in CI. Before PR #47, those 13 tests were skipped. The tests drop and re-create their tables, so this variable must only ever point at a throwaway database, never at Supabase.

### Docker

- `backend/Dockerfile` packages the backend. It starts from `python:3.12-slim`, installs `requirements.txt`, runs as a normal user (not root), and starts the server with `uvicorn app.api:app` on the port in the `PORT` variable (8000 if it is not set).
- `backend/.dockerignore` keeps `.env` files, caches and tests out of the image.
- The `docker-build` job builds the image on every pull request, so a broken Dockerfile turns the check red. The image is not pushed or deployed yet, and CI does not start the container.
- CI also uses Docker for the `postgres:16` test container described above.

## Branch protection

- `main` needs a pull request and the `test` check to pass before merging
- No approvals are required (0), so a person can merge their own pull request once the checks pass
- Force pushes are blocked, and the bypass list is empty
- Tested with a deliberately broken pull request (type error): the check failed and the merge was blocked

## Environments and secrets

- `staging`: no rules. It holds the `EXPO_TOKEN` secret, and the mobile build job runs in it.
- `production`: required reviewer, only `main` can deploy, admin bypass turned off
- Secrets are stored per environment in GitHub, never in the code

## Security tools

- Dependabot version updates: GitHub Actions only, checked weekly (`.github/dependabot.yml`). Example: PR #41 moved `expo-github-action` from v8 to v9.
- App packages are left to the team, because Expo SDK 57 expects specific versions and CI cannot test the running app. Dependabot's automatic fixes for npm packages fail at `npm ci` (ERESOLVE), so they are not merged.
- Dependabot security alerts and secret scanning with push protection are on
- CodeQL code scanning is on (default setup: actions, JavaScript/TypeScript, Python). It is not a required check, so a person has to look at its alerts.
- gitleaks runs in `backend-ci.yml` as a second secret scan
- AI scan for pull requests and Copilot Autofix are on (suggestions only, agentic autofix is off)
- Every alert review is written in the security review log at the end of this file

## Mobile build (CD)

### What it does

After a change is merged into `main`, GitHub Actions builds the Android app with Expo EAS Build and produces an installable `.apk` file. Nobody has to run the build command on their own laptop.

### Pipeline

- **Workflow file:** `.github/workflows/mobile-build.yml`, job `build-android`
- **Trigger:** push to `main` (a merged pull request), plus a manual "Run workflow" button. The build is skipped when a merge only changes `backend/**`, `docs/**` or `.md` files, because those do not change the phone app.
- **Steps:**
  1. Check out the code
  2. Set up Node 22 with the npm cache
  3. Install packages (`npm ci`)
  4. Install `eas-cli` and log in with `expo/expo-github-action`
  5. Run `eas build --platform android --profile preview --non-interactive --wait`
- **Build profile:** `preview` in `eas.json`. It uses internal distribution and produces an `.apk`, which can be installed directly on an Android phone.
- **Where it builds:** on Expo's servers. GitHub starts the build and waits for the result.

### Secrets and environments

- The Expo access token is stored as the environment secret `EXPO_TOKEN` in the `staging` environment.
- The workflow can only read the secret because the job declares `environment: staging`.
- The token is never written in any file in the repo. Push protection and gitleaks would flag it if it were.
- Workflow permissions are `contents: read`.

### Safety settings

- `concurrency` stops two builds from running at once, and never cancels a build that is already running. A second build waits for the first.
- `timeout-minutes: 45` stops a stuck build.

### Setup history

- PR #34 added `eas.json` and put the Expo project ID, `owner` and Android package name (`com.nueve09.medsight`) into `app.json`.
- The signing keystore was generated by EAS and is stored on Expo's servers. It must not be deleted.
- PR #36 added the workflow, first as a manual button and then with the push trigger.
- PR #38 added Node setup and `npm ci` (see the problem below).
- PR #41 (Dependabot) updated `expo-github-action` from v8 to v9. The build after it passed.

### Problem found and fixed

The first automatic run (run #1) failed after 25 seconds at the `eas build` step with:

    Failed to resolve plugin for module "expo-router" ... Do you have node modules installed?

The cause: `eas build` reads `app.json` and loads its plugins from `node_modules`, and the fresh GitHub runner had no packages installed. The fix was PR #38, which added the Node setup and `npm ci` before the build. Run #2 passed.

### Known limits

- Only Android is built. An iPhone build needs a paid Apple Developer account, which this project does not have.
- Each `.apk` is available on Expo for about 13 days, so it has to be downloaded and kept if it is needed longer.
- The free Expo plan has a monthly build limit and builds can wait in a queue.
- A merge that changes `.github/` also starts a build, because only `backend/`, `docs/` and `.md` changes are skipped.
- The Expo project belongs to one account (`nueve09`). Teammates need an invite on expo.dev to see the build page, or they can be sent the `.apk` file.
- The workflow only builds the app. Lint and type checks run in `ci.yml` before a merge.

## Teacher's checklist mapped to the pipeline

| Item              | What stands for it in MedSight                                                                                                                                                                                                                            |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Unit tests        | Backend: `pytest` in `backend-ci.yml`, 403 tests passed and 0 skipped in the latest run (PR #47). Frontend: no tests yet, because the project has no `test` script and no test tool. Lint and the type check stand in for it.                             |
| Regression tests  | `backend/tests/test_m0_defects.py` holds regression tests for defects listed in the development plan, for example the cache key ignoring unit spacing and case, and a failed description not being cached. The whole suite re-runs on every pull request. |
| Integration tests | The store tests run on SQLite and on a real Postgres 16. `backend-ci.yml` starts a throwaway `postgres:16` container and sets `MEDSIGHT_TEST_DATABASE_URL`. Before PR #47, 13 of these tests were skipped in CI.                                          |
| Time check        | See the Numbers section: frontend checks take about 25-41s, backend checks about 1 minute, a mobile build about 15-16 minutes.                                                                                                                            |
| DORA metrics      | See below.                                                                                                                                                                                                                                                |
| Hosting           | See below.                                                                                                                                                                                                                                                |
| Docker            | `backend/Dockerfile` packages the backend, and the `docker-build` job builds the image on every pull request (PR #48). CI also runs a `postgres:16` container for the store tests.                                                                        |

### DORA metrics (as of Nov 6, 2026)

A "deployment" here is a successful Mobile Build, which produces an installable `.apk`. There is no production deployment yet. The numbers were calculated by hand from the Actions runs and the git history.

| Metric                | Result                                                                                                                                                                                                                                                                 |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Deployment frequency  | 4 successful builds between Nov 1 and Nov 6 (runs #2 to #5). Merges that only change `backend/`, `docs/` or `.md` files do not start a build.                                                                                                                          |
| Lead time for changes | PR #47: about 18 minutes (commit 10:49, merge 10:52, build finished about 11:07). PR #41: about 1 hour 3 minutes, including about 12 minutes waiting for the previous build. PR #40: at least 11 hours 37 minutes, because the branch waited overnight before merging. |
| Change failure rate   | 1 of 5 runs (20%). The failed run (#1) was a pipeline setup problem (packages not installed), not app code.                                                                                                                                                            |
| Time to restore       | About 3 hours 40 minutes, from the failed run #1 to the end of the first green run (#2).                                                                                                                                                                               |

The sample is small (5 runs in 6 days), so these numbers show how the pipeline behaves, not a long-term trend.

### Hosting

| What                            | Where            | Status                                                    |
| ------------------------------- | ---------------- | --------------------------------------------------------- |
| Code, pipeline and secrets      | GitHub           | In use                                                    |
| Android builds and `.apk` files | Expo (EAS Build) | In use. Each `.apk` is kept about 13 days.                |
| Backend API                     | Railway          | Planned, not set up yet                                   |
| Database                        | Supabase         | Planned. A staging project exists, setup is not finished. |

## Numbers

### Checks

- First green run of `ci.yml`: 36s (CI #1)
- Pull request run: about 32s
- Run on `main` after the safety lines were added: 25s (CI #31)
- `test` job in `ci.yml`: about 25-41s
- `backend-ci.yml`: about 28-30s at first. With the Postgres container and the image build added, the run on `main` after PR #48 took 1m 3s (Backend CI #45).
- Backend tests: 403 passed in 9.87s, 0 skipped (PR #47)

### Mobile build

| Run     | Date   | Trigger                                               | Result                         | Job time | Total time |
| ------- | ------ | ----------------------------------------------------- | ------------------------------ | -------- | ---------- |
| By hand | Oct 28 | `npx eas-cli build` from my laptop (commit `90f17a9`) | Success                        | 19m 19s  | 19m 28s    |
| #1      | Nov 1  | Merge of PR #36 (workflow added)                      | Failed: packages not installed | 22s      | 25s        |
| #2      | Nov 1  | Merge of PR #38 (`npm ci` added)                      | Success                        | 16m 20s  | 16m 24s    |
| #3      | Nov 2  | Merge of PR #40 (`supabase-anon-auth`)                | Success                        | 15m 15s  | 15m 18s    |
| #4      | Nov 2  | Merge of PR #41 (`expo-github-action` v9)             | Success                        | 16m 19s  | 28m 47s    |
| #5      | Nov 6  | Merge of PR #47 (Postgres test container)             | Success                        | 14m 54s  | 14m 57s    |

- For the by-hand row, "job time" is the Expo build time.
- The four successful automatic runs took between 14m 54s and 16m 20s, about 15-16 minutes.
- Total time includes waiting. Run #4 started 3 minutes after run #3 and waited about 12 minutes for it, because `concurrency` allows one build at a time. The job itself took 16m 19s.

## Known problems

- The 18 dismissed Dependabot alerts are not fixed. The vulnerable packages are still in `package-lock.json`, and new alerts keep appearing as advisories are published (the open count went from 4 to 19 in two weeks). See the security review log.
- The frontend has no tests, and no `build` or `test` script in the project, so CI checks style and types only
- Only `test` is a required check. A failing `backend`, `secret-scan` or `docker-build` job does not block a merge.
- CodeQL is not a required check, so its alerts need a person to look at them

## Not done yet

- A production build job that needs the reviewer's approval (`production` environment)
- Making the `backend` and `secret-scan` checks required in the branch ruleset
- A Dependabot `pip` entry for `/backend` (asked for in `backend/ops/README.md`)
- A first frontend test, which is the app team's decision
- Setting up Railway (backend hosting) and finishing the Supabase staging project
- Automatic code review (CodeRabbit)

## Security review log

### 2026-10-20: CodeQL alert on PR #23 (dismissed as false positive)

- **Alert:** "Clear-text logging of sensitive information" (high), rule `py/clear-text-logging-sensitive-data`
- **Where:** `backend/scripts/tts_output.py`, line 16: `print(f"\n🔊 {text}")` inside `speak()`
- **What I checked:** the alert's "Show paths" list. The flagged data started at a fixed sentence (`PRESCRIPTION_LINE` in `backend/app/speech.py`) and moved through `pipeline.py` to `speak()`. No key, password, or user data appeared in the path.
- **Author's explanation:** CodeQL matched a variable name that looked sensitive.
- **Decision:** dismissed as a false positive, with a written reason in the dismissal comment.
- **Why this was safe:** the text is fixed app wording and the generated answer, printed by the local demo script.
- **If it comes back:** open "Show paths" first. If the path carries only fixed app text, ask the author to rename the constant or dismiss it with a reason. If it carries real data (user input, keys, medicine records tied to a person), treat it as real and ask the author to fix the code.
- **Note:** CodeQL is not a required check, so merges are not blocked automatically. A person has to look at its alerts.

### 2026-10-26: Dependabot security update #31 (closed)

- **What:** update of `expo-router` to 58.0.26 (Expo SDK 58) while the project is on SDK 57
- **Result:** `CI / test` failed at `npm ci` with ERESOLVE (peer dependency conflict), so the required check blocked the merge
- **Decision:** closed without merging, and reported to the app developers. Moving to SDK 58 is a team decision, not a single-package update
- **Not used:** `--force` and `--legacy-peer-deps`, because they hide the conflict instead of fixing it

### 2026-11-02: 19 open Dependabot alerts

- **Finding:** the open alerts went from 4 to 19. Most of the new ones were opened together about 16 hours before the review. By package: node-forge 12 (6 High, 3 Moderate, 3 Low), decode-uri-component 2 (1 High, 1 Moderate), zod 1 (High), minimatch 1 (Moderate), glob 1 (Moderate), uuid 1 (Moderate), deepmerge 1 (details not recorded).
- **Where:** all appear in `package-lock.json`. The ones reviewed are transitive, which means they come inside other packages and not from packages the app asks for directly.
- **Already fixed before this review:** shell-quote (Critical, alert #6) and source-map-js (High, alert #5, fixed by Dependabot PR #20).
- **Dependabot pull requests:** #13, #24, #31 and #37 were closed without merging. #31 and #37 failed at `npm ci` with ERESOLVE, because Expo 58 packages conflict with SDK 57.
- **Decision:** the team decided to stay on Expo SDK 57. Fixing these alerts means moving to a newer SDK or adding an npm `overrides` entry, and the team chose neither.
- **Action:** 18 alerts were dismissed on 2026-11-04 with the reason "risk is tolerable to this project" and a written comment. Example comment: "Transitive dependency in package-lock.json. The fix needs Expo SDK 58, and the Dependabot update fails at npm ci with ERESOLVE against SDK 57 (PRs #31 and #37). Team decision: the app stays on SDK 57 for this project."
- **Reopen if:** the project moves to a newer SDK, or a package turns out to reach the running app.
- **Not checked:** whether the vulnerable packages reach the phone app or only the build tools.
- **Not used:** `--force` and `--legacy-peer-deps`, because they hide the conflict instead of fixing it

### 2026-11-07: Dependabot alert #23 (minimatch, Moderate)

- **Finding:** a second open alert for minimatch (the first is #22). It is transitive.
- **Path:** the Expo packages reach minimatch through `@expo/fingerprint` and `glob`.
- **Result:** a patched version exists, but Dependabot's automatic update would downgrade `expo` from 57.0.28 to 52.0.49, so it was not applied.
- **Options:** update the Expo packages to versions that support the patched minimatch, or add an npm `overrides` entry that pins minimatch to a patched version. Both are the team's decision, and the team stays on SDK 57.
- **Status:** [dismissed on DATE with the reason "risk is tolerable to this project" and a written comment / still open]
- **Not used:** `--force` and `--legacy-peer-deps`, because they hide the conflict instead of fixing it
