# CI/CD

## What the pipeline does

- Runs on every push to `main` and every pull request into `main`
- Steps: install packages (`npm ci`), lint (`npm run lint`), type check (`npx tsc --noEmit`)
- File: `.github/workflows/ci.yml`

## Run the same checks on your laptop

npm ci
npm run lint
npx tsc --noEmit

## Numbers

- First green run: 36s (CI #1)

## Not done yet

- No `build` or `test` script in the project
- Branch protection on `main`
- Deploy (CD)
- Secrets and environments

## Branch protection

- `main` needs a pull request and the `test` check to pass before merging
- Tested with a deliberately broken pull request (type error): check failed, merge was blocked

## Dependabot

- Checks npm packages and GitHub Actions once a week
- Opens a pull request for each update, and the `test` check runs on it
- Major version updates are ignored on purpose, because Expo SDK 57 expects specific versions
- Config file: `.github/dependabot.yml`

## Dependabot

- Version updates are limited to GitHub Actions (`ci.yml`), checked weekly
- App packages are left to the team, because Expo SDK 57 expects specific versions and CI cannot test the running app
- Security alerts and security updates stay on for all packages
- Config file: `.github/dependabot.yml`

## Safety settings in ci.yml

- `permissions: contents: read` so the pipeline can only read the code
- `concurrency` with cancel-in-progress, so an old run stops when a newer push arrives
- `timeout-minutes: 10` so a stuck job is stopped (a normal run takes about 25 to 35 seconds)
