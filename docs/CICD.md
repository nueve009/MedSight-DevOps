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
