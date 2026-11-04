# CI/CD

How a change gets from a commit to a running app in this project, with the numbers and problems behind it.

Path of a change: branch, pull request, checks pass, merge into `main`, Android build.

## Overview

| Stage              | What happens                                                       | File                                 |
| ------------------ | ------------------------------------------------------------------ | ------------------------------------ |
| Frontend checks    | Lint and type check on every pull request and every push to `main` | `.github/workflows/ci.yml`           |
| Backend checks     | ruff, pytest, `kb/` data check, dependency audit, secret scan      | `.github/workflows/backend-ci.yml`   |
| Merge rules        | Pull request required, the `test` check must pass                  | Branch ruleset on `main`             |
| Mobile build (CD)  | After a merge, builds the Android `.apk` with Expo EAS Build       | `.github/workflows/mobile-build.yml` |
| Dependency updates | Weekly GitHub Actions updates                                      | `.github/dependabot.yml`             |

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

Written by the backend teammate. It has two jobs:

- `backend`: ruff, pytest, `kb/` data check (with `--allow-empty`), and `pip-audit`
- `secret-scan`: gitleaks

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
- App packages are left to the team, because Expo SDK 57 expects specific versions and CI cannot test the running app
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

## Numbers

### Checks

- First green run of `ci.yml`: 36s (CI #1)
- Pull request run: about 32s
- Run on `main` after the safety lines were added: 25s (CI #31)
- `test` job in `ci.yml`: about 25-41s
- `backend-ci.yml`: about 28-30s

### Mobile build

| Run     | Date   | Trigger                                               | Result                         | Job time | Total time |
| ------- | ------ | ----------------------------------------------------- | ------------------------------ | -------- | ---------- |
| By hand | Oct 28 | `npx eas-cli build` from my laptop (commit `90f17a9`) | Success                        | 19m 19s  | 19m 28s    |
| #1      | Nov 1  | Merge of PR #36 (workflow added)                      | Failed: packages not installed | 22s      | 25s        |
| #2      | Nov 1  | Merge of PR #38 (`npm ci` added)                      | Success                        | 16m 20s  | 16m 24s    |
| #3      | Nov 2  | Merge of PR #40 (`supabase-anon-auth`)                | Success                        | 15m 15s  | 15m 18s    |
| #4      | Nov 2  | Merge of PR #41 (`expo-github-action` v9)             | Success                        | 16m 19s  | 28m 47s    |

- For the by-hand row, "job time" is the Expo build time.
- The three successful automatic runs took between 15m 15s and 16m 20s, about 16 minutes.
- Total time includes waiting. Run #4 started 3 minutes after run #3 and waited about 12 minutes for it, because `concurrency` allows one build at a time. The job itself took 16m 19s.

## Known problems

- 19 Dependabot alerts are open on `package-lock.json` (details in the security review log). Fixing them means moving to Expo SDK 58, which is a team decision.
- No `build` or `test` script in the project, so CI checks style and types only
- CodeQL is not a required check, so its alerts need a person to look at them

## Not done yet

- A production build job that needs the reviewer's approval (`production` environment)
- Regression tests, integration tests, DORA metrics, hosting and Docker, which are still to be mapped
- Team decision on staying on Expo SDK 57 or moving to SDK 58, and what to do with the open alerts

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
- **Dependabot pull requests:** #13, #24, #31 and #37 were closed without merging. #31 and #37 failed at `npm ci` with ERESOLVE, because Expo 58 packages conflict with SDK 57.
- **Status:** all alerts stay open and none has been dismissed.
- **Decision:** the fix means moving the app to Expo SDK 58, which is a team decision.
- **Not checked yet:** whether the vulnerable packages reach the phone app or only the build tools. The dependency path on each alert shows this.
- **Not used:** `--force` and `--legacy-peer-deps`, because they hide the conflict instead of fixing it
