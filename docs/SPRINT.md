# MedSight sprint board: items to add

Source: merged PRs on nueve009/MedSight-DevOps main (git history, checked 2026-11-27). Dates are merge dates.
Owner mapping: rmndferrero = Reymond, nueve009 / David Joshua Albos = Joshua, stingdrei = Michael, BlinkyPanoptes = Cassandra, phlppdsqtd = Philipp.

## Board setup

- Template: Board. Columns: Todo, In progress, In review, Done. Put every item below in Done.
- Field "Sprint" (Iteration): 2-week iterations, first one starts 2026-10-12.
- Field "Owner": text field (Michael is not a repo collaborator, so he cannot be set as an Assignee).
- Optional field "Area": Backend, App, CI/CD, Docs, Security, Deps.
- Project description: "Reconstructed from our PR history; dates match merge dates."

## Sprint 1: Oct 12 to Oct 25. Repo setup, CI, security, backend M0/M1

| PR      | Title                                              | Owner                               | Area     |
| ------- | -------------------------------------------------- | ----------------------------------- | -------- |
| #1      | Test branch protection                             | Joshua                              | CI/CD    |
| #3      | Document branch                                    | Joshua                              | Docs     |
| #4      | Dependabot config for npm and Actions              | Joshua                              | Security |
| #8      | Document Dependabot                                | Joshua                              | Docs     |
| #14     | Dependabot actions only                            | Joshua                              | Security |
| #15     | SECURITY.md                                        | Joshua                              | Security |
| #16     | Fix code scanning alert: workflow permissions      | Joshua                              | Security |
| #18     | CI permissions, concurrency, timeout               | Joshua                              | CI/CD    |
| #19     | Security docs                                      | Joshua                              | Docs     |
| #20     | Sync upstream (app + backend code from team repos) | Cassandra, Reymond, Philipp, Joshua | App      |
| #21     | Remove temporary secret checker                    | Joshua                              | CI/CD    |
| #22     | Backend M0: tests, JSON logs, defect fixes         | Reymond                             | Backend  |
| #23     | Backend M1: parse and scan rules                   | Reymond                             | Backend  |
| #25     | CI/CD documentation update                         | Joshua                              | Docs     |
| #26     | Backend CI workflow                                | Joshua                              | CI/CD    |
| #27     | Security review log (CodeQL alert on #23)          | Joshua                              | Security |
| #5, #10 | Dependabot bumps (checkout, setup-node)            | Dependabot, merged by Joshua        | Deps     |

## Sprint 2: Oct 26 to Nov 8. Supabase, mobile builds, backend M2 to M7

| PR                      | Title                                                                    | Owner                        | Area     |
| ----------------------- | ------------------------------------------------------------------------ | ---------------------------- | -------- |
| #30                     | App: Supabase for history and My medicines                               | Michael                      | App      |
| #32                     | AI scan for pull requests                                                | Joshua                       | CI/CD    |
| #33                     | Security review log                                                      | Joshua                       | Security |
| #34                     | EAS config for Android preview build                                     | Joshua                       | CI/CD    |
| #35, #36, #38           | Android build workflow                                                   | Joshua                       | CI/CD    |
| #39                     | Backend M2: dataset matching                                             | Reymond                      | Backend  |
| #40                     | App: anonymous Supabase sign-in, own-rows-only data                      | Reymond                      | App      |
| #42                     | Backend M3+M4: Supabase cache, scan log, rate limiting                   | Reymond                      | Backend  |
| #43                     | Clean up CICD.md, mobile build numbers                                   | Joshua                       | Docs     |
| #44                     | Backend M5: smoke test, API docs setting                                 | Reymond                      | Backend  |
| #45                     | Backend M6: web search tier                                              | Reymond                      | Backend  |
| #46                     | Backend M7: admin routes, audit log, drills, runbook                     | Reymond                      | Backend  |
| #47                     | Postgres 16 service for backend tests in CI                              | Joshua                       | CI/CD    |
| #48                     | Backend Dockerfile + CI build job                                        | Joshua                       | CI/CD    |
| #50                     | CI/CD docs update                                                        | Joshua                       | Docs     |
| #53                     | Production mobile build with approval                                    | Joshua                       | CI/CD    |
| #28, #29, #41, #51, #52 | Dependabot bumps (setup-python, gitleaks, expo action, fastapi, psycopg) | Dependabot, merged by Joshua | Deps     |

## Sprint 3: Nov 9 to Nov 22. Verified dataset in Supabase, review console, phone OCR, auto-deploy

| PR       | Title                                             | Owner                        | Area    |
| -------- | ------------------------------------------------- | ---------------------------- | ------- |
| #54      | Reviewed dataset in Supabase, approved row by row | Reymond                      | Backend |
| #55      | FDA lookup, staging import, review console        | Reymond                      | Backend |
| #56      | Ignore two gitleaks false positives               | Joshua                       | CI/CD   |
| #58      | CI/CD docs update                                 | Joshua                       | Docs    |
| #60      | Review console updates                            | Reymond                      | Backend |
| #62      | Docs update for CI/CD                             | Joshua                       | Docs    |
| #63      | On-device OCR, app switched to /v2/identify       | Cassandra                    | App     |
| #65, #66 | Pipeline demo, auto-deploy test                   | Joshua                       | CI/CD   |
| #57      | Dependabot bump (node-forge)                      | Dependabot, merged by Joshua | Deps    |

## Sprint 4: Nov 23 to Dec 6. Evaluation, console polish

| PR  | Title                                              | Owner   | Area    |
| --- | -------------------------------------------------- | ------- | ------- |
| #67 | Pipeline demo run 2                                | Joshua  | CI/CD   |
| #69 | Photo eval scans (24 labeled), eval_kb on Supabase | Reymond | Backend |
| #70 | Review console layout, forms, navigation, branding | Michael | App     |

Tip: you can drop the Dependabot rows if the board feels crowded. They show that dependency updates go through PR review.
