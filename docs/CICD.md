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
