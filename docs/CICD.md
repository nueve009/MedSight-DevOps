# CI/CD

How a change gets from a commit to a running app in this project, with the numbers and problems behind it.

Path of a change: branch, pull request, checks pass, one teammate approves, merge into `main`, Android build. A production build is started by hand and needs an approval.

## Overview

| Stage                 | What happens                                                                                                | File                                   |
| --------------------- | ----------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| Frontend checks       | Lint and type check on every pull request and every push to `main`                                          | `.github/workflows/ci.yml`             |
| Backend checks        | ruff, pytest (also on a real Postgres), `kb/` data check, dependency audit, secret scan, Docker image build | `.github/workflows/backend-ci.yml`     |
| Merge rules           | Pull request required, 1 approving review, the `test`, `backend` and `secret-scan` checks must pass         | Branch ruleset `protect-main`          |
| Staging build (CD)    | After a merge, builds the Android `.apk` with Expo EAS Build (`preview` profile)                            | `.github/workflows/mobile-build.yml`   |
| Production build (CD) | Started by hand, waits for an approval, then builds the `.apk` with the `production` profile                | `.github/workflows/mobile-release.yml` |
| Dependency updates    | Weekly GitHub Actions updates                                                                               | `.github/dependabot.yml`               |
| Release and rollback  | See the "Release and rollback" section                                                                      | -                                      |

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

Ruleset `protect-main`:

- `main` needs a pull request and the `test`, `backend` and `secret-scan` checks to pass before merging
- **Required approvals: 1** (changed from 0 on 2026-11-14). The author of a pull request cannot approve their own pull request, so another person with write access must approve it. Here that is the backend teammate (`rmndferrero`), who is a collaborator.
- Code-owner review is not required. A second reviewer only for `kb/` changes was discussed and left off for now. It can be turned on in the ruleset later.
- `docker-build` and CodeQL are not required checks
- Force pushes are blocked, and the bypass list is empty, so nobody can skip the rules, including the repository owner
- Tested with a deliberately broken pull request (type error): the check failed and the merge was blocked
- Tested again on PR #55: `secret-scan` failed on two gitleaks findings and the merge stayed blocked until it was resolved (see the security review log)
- Tested the approval rule on 2026-11-14 with a test pull request: with no approval, the merge button was blocked and the merge box said an approving review is required (screenshot saved on that pull request, which was closed without merging)

Why approvals are on: passing checks only shows that the code builds and the tests pass. A second person can catch wrong settings, risky changes or files that should not be committed, before they reach `main` and start a build.

## Environments and secrets

- `staging`: no rules. It holds the `EXPO_TOKEN` secret, and the staging build job runs in it.
- `production`: required reviewer, only `main` can deploy, admin bypass turned off. It holds its own copy of the `EXPO_TOKEN` secret, and the production build job runs in it. Self-review is allowed, so the person who starts a release can approve it.
- Secrets are stored per environment in GitHub, never in the code

## Security tools

- Dependabot version updates: GitHub Actions only, checked weekly (`.github/dependabot.yml`). Example: PR #41 moved `expo-github-action` from v8 to v9.
- App packages are left to the team, because Expo SDK 57 expects specific versions and CI cannot test the running app. Dependabot's automatic fixes for npm packages fail at `npm ci` (ERESOLVE), so they are not merged.
- Dependabot security alerts and secret scanning with push protection are on
- CodeQL code scanning is on (default setup: actions, JavaScript/TypeScript, Python). It is not a required check, so a person has to look at its alerts.
- gitleaks runs in `backend-ci.yml` as a second secret scan. Reviewed false positives are listed one by one in `.gitleaksignore`, with no folder-wide or rule-wide ignore.
- AI scan for pull requests and Copilot Autofix are on (suggestions only, agentic autofix is off)
- Every alert review is written in the security review log at the end of this file

## Mobile build (CD)

### Staging build (`mobile-build.yml`)

After a change is merged into `main`, GitHub Actions builds the Android app with Expo EAS Build and produces an installable `.apk` file. Nobody has to run the build command on their own laptop.

- **Workflow file:** `.github/workflows/mobile-build.yml`, job `build-android`, environment `staging`
- **Trigger:** push to `main` (a merged pull request), plus a manual "Run workflow" button. The build is skipped when a merge only changes `backend/**`, `docs/**` or `.md` files, because those do not change the phone app.
- **Steps:**
  1. Check out the code
  2. Set up Node 22 with the npm cache
  3. Install packages (`npm ci`)
  4. Install `eas-cli` and log in with `expo/expo-github-action`
  5. Run `eas build --platform android --profile preview --non-interactive --wait`
- **Build profile:** `preview` in `eas.json`. It uses internal distribution and produces an `.apk`, which can be installed directly on an Android phone.
- **Where it builds:** on Expo's servers. GitHub starts the build and waits for the result.

### Production build (`mobile-release.yml`)

- **Workflow file:** `.github/workflows/mobile-release.yml`, job `release-android`, environment `production`
- **Trigger:** only the manual "Run workflow" button, so approval requests do not pile up on every merge
- **Approval gate:** the `production` environment has a required reviewer, so GitHub pauses the run at "waiting for review" until it is approved. The approval and its comment are recorded on the run page.
- **Steps:** the same as the staging build, with `--profile production`
- **Build profile:** `production` in `eas.json`. It builds an `.apk` with `autoIncrement` on, because the project has no Google Play account.
- **First run:** Mobile Release #2 was approved with the comment "First production release test" and succeeded (see Numbers). Run #1 was started by mistake and deleted before it was approved.

### Secrets and environments

- The Expo access token is stored as the environment secret `EXPO_TOKEN`, once in `staging` and once in `production` (a separate token for each).
- A job can only read the secret of the environment it declares.
- The token is never written in any file in the repo. Push protection and gitleaks would flag it if it were.
- Workflow permissions are `contents: read`.

### Safety settings

- `concurrency` stops two builds from running at once, and never cancels a build that is already running. A second build waits for the first.
- `timeout-minutes: 45` stops a stuck build.

### Setup history

- PR #34 added `eas.json` and put the Expo project ID, `owner` and Android package name (`com.nueve09.medsight`) into `app.json`.
- The signing keystore was generated by EAS and is stored on Expo's servers. It must not be deleted.
- PR #36 added the staging workflow, first as a manual button and then with the push trigger.
- PR #38 added Node setup and `npm ci` (see the problem below).
- PR #41 (Dependabot) updated `expo-github-action` from v8 to v9. The build after it passed.
- The production profile and `mobile-release.yml` were added in their own pull request.

### Problem found and fixed

The first automatic run (run #1) failed after 25 seconds at the `eas build` step with:

    Failed to resolve plugin for module "expo-router" ... Do you have node modules installed?

The cause: `eas build` reads `app.json` and loads its plugins from `node_modules`, and the fresh GitHub runner had no packages installed. The fix was PR #38, which added the Node setup and `npm ci` before the build. Run #2 passed.

### Known limits

- Only Android is built. An iPhone build needs a paid Apple Developer account, which this project does not have.
- Each `.apk` is available on Expo for about 13 days, so it has to be downloaded and kept if it is needed longer.
- The free Expo plan has a monthly build limit and builds can wait in a queue.
- A merge that changes `.github/` also starts a staging build, because only `backend/`, `docs/` and `.md` changes are skipped.
- The Expo project belongs to one account (`nueve09`). Teammates need an invite on expo.dev to see the build page, or they can be sent the `.apk` file.
- The workflows only build the app. Lint and type checks run in `ci.yml` before a merge.

## Release and rollback

### Release procedure

Staging (automatic):

1. Create a branch, make the change, push it, and open a pull request into `main`.
2. Wait for the required checks (`test`, `backend`, `secret-scan`) to pass.
3. A teammate reviews the pull request and approves it.
4. Merge with a merge commit.
5. `mobile-build.yml` starts by itself and builds the preview `.apk` (about 15-16 minutes). Merges that only change `backend/**`, `docs/**` or `.md` files start no build.
6. Check the run in the Actions tab, then check that the build on expo.dev is for the same commit.

Production (by hand, with an approval):

1. Open Actions, then Mobile Release, then "Run workflow" on `main`.
2. The run stops at "waiting for review" because of the `production` environment.
3. The reviewer approves it and writes a comment. This is recorded on the run page.
4. `mobile-release.yml` builds the `production` profile `.apk` (about 16 minutes in total in Mobile Release #2).
5. Download the `.apk` from expo.dev, install it on an Android phone or emulator and open it to check that the app starts.

### Rollback procedure

There are two ways to go back to a working version. Neither has been tested yet (see "Rollback test" below).

1. **Install an earlier build.** This is the fastest way, because it needs no new build. Open expo.dev, find the last good build, and install its `.apk`. Expo keeps each build for about 13 days, so an older `.apk` must be downloaded and saved before it expires.
2. **Revert the change in Git.** On the merged pull request, click the "Revert" button. GitHub makes a new branch and a new pull request that undoes the change. That pull request needs the same checks and 1 approval as any other, because `main` is protected. After it is merged, `mobile-build.yml` builds a new staging `.apk` from the reverted code. For production, start Mobile Release again and approve it.

Rules for both ways:

- Never rewrite history on `main` (no force push, no reset). Force pushes are blocked, and the `.gitleaksignore` entries contain commit hashes, so rewriting history would break them.
- Never delete the signing keystore on Expo's servers.
- After a rollback, write what happened in this file.

### Rollback test

- Status: not tested yet
- Plan: revert the pull request used in the recorded demo with the "Revert" button, merge the revert, and check that the new staging build starts and the app opens
- Result: (to be filled in after the test: date, pull request numbers, run number, build time, and whether the app worked)

## Optimizations

What is already in place to keep the pipeline fast, safe and cheap:

- **Skipped builds:** a merge that only changes `backend/**`, `docs/**` or `.md` files does not start a mobile build, which saves about 15 minutes of build time and one build from the Expo monthly limit.
- **npm cache:** `actions/setup-node` caches the npm packages, so `npm ci` is faster on later runs.
- **Cancel old runs:** `ci.yml` stops an older run when a newer push arrives on the same branch.
- **One build at a time:** the mobile workflows use `concurrency` without cancelling a running build, so builds do not overlap and no build is lost. The cost is waiting: run #4 waited about 12 minutes for run #3.
- **Timeouts:** 10 minutes for the frontend checks and 45 minutes for mobile builds, so a stuck job does not run for hours.
- **Read-only permissions:** `contents: read` on the workflows, so a compromised step cannot change the code.
- **Production only by hand:** the production build starts only from the manual button, so approval requests do not pile up on every merge.
- **Backend checks in parallel:** `backend`, `secret-scan` and `docker-build` are separate jobs, so they run at the same time. The whole workflow takes about 1 minute.

Measured result: the automatic staging runs took 14m 54s to 16m 20s. Most of that time is the Expo build itself (about 13-15 minutes), which the project does not control.

## Teacher's checklist mapped to the pipeline

| Item              | What stands for it in MedSight                                                                                                                                                                                                                            |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Unit tests        | Backend: `pytest` in `backend-ci.yml`, 403 tests passed and 0 skipped in the run for PR #47. Frontend: no tests yet, because the project has no `test` script and no test tool. Lint and the type check stand in for it.                                  |
| Regression tests  | `backend/tests/test_m0_defects.py` holds regression tests for defects listed in the development plan, for example the cache key ignoring unit spacing and case, and a failed description not being cached. The whole suite re-runs on every pull request. |
| Integration tests | The store tests run on SQLite and on a real Postgres 16. `backend-ci.yml` starts a throwaway `postgres:16` container and sets `MEDSIGHT_TEST_DATABASE_URL`. Before PR #47, 13 of these tests were skipped in CI.                                          |
| Time check        | See the Numbers section: frontend checks take about 25-41s, backend checks about 1 minute, a mobile build about 15 minutes.                                                                                                                               |
| DORA metrics      | See below.                                                                                                                                                                                                                                                |
| Hosting           | See below.                                                                                                                                                                                                                                                |
| Docker            | `backend/Dockerfile` packages the backend, and the `docker-build` job builds the image on every pull request (PR #48). CI also runs a `postgres:16` container for the store tests.                                                                        |

### DORA metrics (as of Nov 11, 2026)

A "deployment" here is a successful mobile build, which produces an installable `.apk`. Staging builds start after a merge, and production builds are started by hand after an approval. The numbers were calculated by hand from the Actions runs and the git history.

| Metric                | Result                                                                                                                                                                                                                                                                                                                                                                 |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Deployment frequency  | 8 successful staging builds (Mobile Build runs #2 to #9) and 1 production release (Mobile Release #2) between Nov 1 and Nov 11. Merges that only change `backend/`, `docs/` or `.md` files do not start a build.                                                                                                                                                       |
| Lead time for changes | PR #47: about 18 minutes (commit 10:49, merge 10:52, build finished about 11:07). PR #41: about 1 hour 3 minutes, including about 12 minutes waiting for the previous build. PR #40: at least 11 hours 37 minutes, because the branch waited overnight before merging. A production release takes about 16 minutes from start to a finished build, after the approval. |
| Change failure rate   | 1 of 9 staging runs (11%) and 0 of 1 production releases. The failed run (#1) was a pipeline setup problem (packages not installed), not app code.                                                                                                                                                                                                                     |
| Time to restore       | About 3 hours 40 minutes, from the failed run #1 to the end of the first green run (#2).                                                                                                                                                                                                                                                                               |

The sample is small (10 builds in about 10 days), so these numbers show how the pipeline behaves, not a long-term trend. Since Nov 14, every pull request also waits for a teammate's approval, so lead times from now on include the time until someone reviews.

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
- `backend-ci.yml`: about 28-30s at first. With the Postgres container and the image build added, it takes about 1 minute (Backend CI #45 on `main`: 1m 3s, Backend CI #64 on `main`: 1m 5s).
- Backend tests: 403 passed in 9.87s, 0 skipped (PR #47)

### Staging build

| Run              | Trigger                                               | Result                         | Job time | Total time |
| ---------------- | ----------------------------------------------------- | ------------------------------ | -------- | ---------- |
| By hand (Oct 28) | `npx eas-cli build` from my laptop (commit `90f17a9`) | Success                        | 19m 19s  | 19m 28s    |
| #1 (Nov 1)       | Merge of PR #36 (workflow added)                      | Failed: packages not installed | 22s      | 25s        |
| #2 (Nov 1)       | Merge of PR #38 (`npm ci` added)                      | Success                        | 16m 20s  | 16m 24s    |
| #3 (Nov 2)       | Merge of PR #40 (`supabase-anon-auth`)                | Success                        | 15m 15s  | 15m 18s    |
| #4 (Nov 2)       | Merge of PR #41 (`expo-github-action` v9)             | Success                        | 16m 19s  | 28m 47s    |
| #5 (Nov 6)       | Merge of PR #47 (Postgres test container)             | Success                        | 14m 54s  | 14m 57s    |
| #9               | Merge of PR #56 (`.gitleaksignore`)                   | Success                        | 14m 59s  | 15m 3s     |

- For the by-hand row, "job time" is the Expo build time.
- Runs #6 to #8 also succeeded. Their Expo build times were 13m 2s (#6, merge of PR #48, the Dockerfile), 14m 39s (#7) and 14m 42s (#8).
- The successful automatic runs took between 14m 54s and 16m 20s, about 15-16 minutes. The Expo build itself takes about 13-15 minutes of that, and the rest is installing packages and logging in.
- Total time includes waiting. Run #4 started 3 minutes after run #3 and waited about 12 minutes for it, because `concurrency` allows one build at a time. The job itself took 16m 19s.

### Production build

| Run               | Trigger                                                      | Result  | Job time | Total time | Expo build time |
| ----------------- | ------------------------------------------------------------ | ------- | -------- | ---------- | --------------- |
| Mobile Release #2 | Started by hand on `main` (commit `680491a`), approved by me | Success | 14m 21s  | 16m 4s     | 13m 8s          |

## Known problems

- The dismissed Dependabot alerts are not fixed. The vulnerable packages are still in `package-lock.json`, and new alerts keep arriving for the same packages (21 for node-forge alone), so the cost of staying on SDK 57 grows over time. See the security review log.
- The frontend has no tests, and no `build` or `test` script in the project, so CI checks style and types only
- `docker-build` and CodeQL are not required checks, so a failure there does not block a merge. CodeQL alerts need a person to look at them.
- Every pull request now needs an approval from a teammate. If the only reviewer is not available, merging waits, because nobody can bypass the rule.

## Not done yet

- A first frontend test, which is the app team's decision
- Setting up Railway (backend hosting) and finishing the Supabase staging project
- A full approval test: a teammate approves a pull request and the merge button turns green (only the blocked side has been tested so far)
- The rollback test (see "Rollback test")

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
- **Status:** dismissed with the reason "risk is tolerable to this project" and a written comment.
- **Not used:** `--force` and `--legacy-peer-deps`, because they hide the conflict instead of fixing it

### 2026-11-10: gitleaks findings on PR #55 (false positives)

- **Alert:** `secret-scan` failed on PR #55 with 2 `generic-api-key` findings in commit `53caa7f`: `backend/tests/test_review_routes.py` line 15 and `backend/tests/test_review_auth.py` line 12. The required check blocked the merge.
- **What I checked:** the flagged lines and the job summary table. The value in `test_review_routes.py` is the placeholder `test-secret-that-is-long-enough`. The value in `test_review_auth.py` is also a fake placeholder.
- **Author's confirmation:** both values were confirmed as fake test data.
- **Decision:** both findings were added to `.gitleaksignore` (PR #56), one entry each, with a comment. No folder-wide or rule-wide ignore was used.
- **Result:** after `main` was merged into the branch of PR #55, `secret-scan` passed on the pull request and on `main`.
- **Why this was safe:** the values are fake test data, not credentials for any service.
- **If it comes back:** open the summary table first. If a value looks like a real key, treat it as real, rotate it, and do not ignore it. The ignore entries contain the commit hash, so rebasing or squashing the branch would break them.
- **Also noted:** PR #55 was merged by its author while the code-owner review requested from me was still pending. The ruleset did not require approvals at that time, so this was allowed. Since 2026-11-14 the ruleset requires 1 approval, so this can no longer happen. Whether `kb/` changes should also need a code-owner review is a team decision, and it was left as it is for now.

### 2026-11-11: nine new node-forge alerts (#24 to #32)

- **Finding:** nine new alerts opened together for node-forge: 2 High (#24, #25), 4 Moderate (#26, #29, #30, #31) and 3 Low (#27, #28, #32). All are transitive and appear in `package-lock.json`. The advisory for #24 says RSA signature verification can accept forged signatures for keys with a low public exponent, fixed in node-forge 1.4.1.
- **Decision:** the team stays on Expo SDK 57. The alerts were dismissed on 2026-11-11 with the reason "risk is tolerable to this project" and a written comment.
- **Not checked:** whether a lockfile-only update to a patched node-forge is possible, and whether the vulnerable code is used by the phone app or only by build tools.
- **Reopen if:** the project moves to a newer SDK, or a package reaches the running app.
