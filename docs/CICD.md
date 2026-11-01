# CI/CD

## What the pipeline does

- Runs on every push to `main` and every pull request into `main`
- Steps: install packages (`npm ci`), lint (`npm run lint`), type check (`npx tsc --noEmit`)
- File: `.github/workflows/ci.yml`

## Run the same checks on your laptop

    npm ci
    npm run lint
    npx tsc --noEmit

## Safety settings in ci.yml

- `permissions: contents: read` so the pipeline can only read the code
- `concurrency` with cancel-in-progress, so an old run stops when a newer push arrives
- `timeout-minutes: 10` so a stuck job is stopped

## Branch protection

- `main` needs a pull request and the `test` check to pass before merging
- Force pushes are blocked, and the bypass list is empty
- Tested with a deliberately broken pull request (type error):ss the check failed and the merge was blocked

## Environments and secrets

- `staging`: no rules
- `production`: required reviewer, only `main` can deploy, admin bypass turned off
- Secrets are stored per environment in GitHub, never in the code

## Security tools

- Dependabot version updates: GitHub Actions only, checked weekly (`.github/dependabot.yml`)
- App packages are left to the team, because Expo SDK 57 expects specific versions and CI cannot test the running app
- Dependabot security alerts, secret scanning, and CodeQL code scanning are on
- CodeQL is not a required check
- AI scan for pull request (Github Settings)

## Numbers

- First green run: 36s (CI #1)
- Pull request run: about 32s
- Run on `main` after the safety lines were added: 25s (CI #31)

## Known problems

- Dependabot shows 6 alerts (1 critical, 3 high, 2 moderate) on `package-lock.json`. This repo has not been synced with the team repo yet, so the list will be re-checked after the sync
- No `build` or `test` script in the project, so CI checks style and types only

## Not done yet

- Sync with the team repo (`upstream`)
- Automatic code review (CodeRabbit)
- Deploy (CD)

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

## Mobile build (CD)

### What it does

After a change is merged into `main`, GitHub Actions builds the Android app with Expo EAS Build and produces an installable `.apk` file. Nobody has to run the build command on their own laptop.

### Pipeline

- **Workflow file:** `.github/workflows/mobile-build.yml`, job `build-android`
- **Trigger:** push to `main` (a merged PR), plus a manual "Run workflow" button. The build is skipped when a merge only changes `backend/**`, `docs/**` or `.md` files, because those do not change the phone app.
- **Steps:** check out the code, install `eas-cli` and log in with `expo/expo-github-action`, then run `eas build --platform android --profile preview --non-interactive --wait`
- **Build profile:** `preview` in `eas.json`. It uses internal distribution and produces an `.apk`, which can be installed directly on an Android phone.
- **Where it builds:** on Expo's servers. GitHub starts the build and waits for the result.

### Secrets and environments

- The Expo access token is stored as the environment secret `EXPO_TOKEN` in the `staging` environment.
- The workflow can only read the secret because the job declares `environment: staging`.
- The token is never written in any file in the repo. Push protection and gitleaks would flag it if it were.
- Workflow permissions are `contents: read`.

### Safety settings

- `concurrency` stops two builds from running at once, and never cancels a build that is already running.
- `timeout-minutes: 45` stops a stuck build.

### Numbers

| Run                                                                         | Date         | Wait / queue | Build time | Total time |
| --------------------------------------------------------------------------- | ------------ | ------------ | ---------- | ---------- |
| First build, started by hand from my laptop (Expo SDK 57, commit `90f17a9`) | Oct 28, 2026 | 4s / 4s      | 19m 19s    | 19m 28s    |

For comparison, the `test` job in `ci.yml` takes about 25-41s and `backend-ci.yml` takes about 28-30s.

### Setup history

- PR #34 added `eas.json` and put the Expo project ID, `owner` and Android package name (`com.nueve09.medsight`) into `app.json`.
- The signing keystore was generated by EAS and is stored on Expo's servers. It must not be deleted.

### Known limits

- Only Android is built. An iPhone build needs a paid Apple Developer account, which this project does not have.
- Each `.apk` is available on Expo for about 13 days, so it has to be downloaded and kept if it is needed longer.
- The free Expo plan has a monthly build limit and builds can wait in a queue.
- The Expo project belongs to one account (`nueve09`). Teammates need an invite on expo.dev to see the build page, or they can be sent the `.apk` file.
- The workflow only builds the app. Lint and type checks run in `ci.yml` before a merge.

### Not done yet

- A production build job that needs the reviewer's approval (`production` environment).
- Regression and integration tests, DORA metrics, and Docker, which are still to be mapped.
