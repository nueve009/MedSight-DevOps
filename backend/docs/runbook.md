# MedSight backend runbook

What to do when something needs fixing, for anyone on the team. Each section
is a checklist. Run every command from the `backend/` folder, with `.venv`
active. Replace `<url>` with the service's address (staging or production)
and `<admin key>` with your own admin key.

Railway, Supabase and the provider accounts are set up by DevOps. This
runbook only covers using them.

## Contents

1. Deploy and roll back
2. Remove or fix a bad answer
3. Review web answers
4. Rotate a key
5. Restore the database
6. Add a medicine to the reviewed list
7. A provider is down
8. Failure drills
9. Alerts and first actions

---

## 1. Deploy and roll back

**Deploy:** merge the PR into `main`. Railway builds it and deploys to
staging. Migrations are applied by hand (`migrations/README.md`); the
reviewed dataset comes from Supabase, not from the build. Then:

1. Run the smoke test against staging:
   ```powershell
   $env:SMOKE_API_KEY = "<a staging app key>"
   python -m scripts.smoke_test --url <staging url>
   ```
   All 6 checks must pass.
2. If the release changes parsing, matching or speech, `python -m scripts.eval_kb` must also pass (0 wrong reviewed answers).
3. Promote the same build to production in Railway, and run the smoke test against production.
4. Add a dated entry to `CHANGES.md`.

After the deploy, `/health` shows the new `commit` and `"database": "ok"`.

**Roll back:**

1. Redeploy the previous build in Railway's Deployments tab. It's one click.
2. Run the smoke test again.

Migrations only add tables and columns, so the old build still works with the new schema.

**Run a migration before the build that needs it.** The web-answers build writes `answers.page_text`, so `0007_web_answers_review.sql` goes first: `python -m scripts.migrate --check`, then `python -m scripts.migrate`. Without it, every new web answer fails to save and the store's circuit breaker skips the database for 60 seconds each time.

## 2. Remove or fix a bad answer

A tester or user reports a wrong spoken answer. Admin routes need `ADMIN_ENABLED=on` on that service. Turn it on in Railway, and turn it back off afterwards on production.

1. Find the answer. List what's cached and look for the product:
   ```powershell
   curl.exe -H "X-Admin-Key: <admin key>" "<url>/admin/answers?limit=100"
   ```
   Each answer has its `cache_key`, `tier`, `product`, `description`, `sources` and `status`.
2. Pick one fix:
   - **Delete it.** The next scan answers afresh: the web tier first, then the general tier.
     ```powershell
     curl.exe -X DELETE -H "X-Admin-Key: <admin key>" "<url>/admin/answers/<cache_key>"
     ```
   - **Fix the text.** It must be 300 characters or fewer, with no doses or schedules.
     ```powershell
     curl.exe -X PATCH -H "X-Admin-Key: <admin key>" -H "Content-Type: application/json" `
       -d '{\"description\": \"Used to relieve pain and reduce fever.\"}' "<url>/admin/answers/<cache_key>"
     ```
   - **Reject it.** It stops being spoken, and the next scan searches again.
     ```powershell
     curl.exe -X PATCH -H "X-Admin-Key: <admin key>" -H "Content-Type: application/json" `
       -d '{\"status\": \"rejected\"}' "<url>/admin/answers/<cache_key>"
     ```
3. Scan the product again and listen to the answer.
4. Add the scan to `kb/eval/` so it's tested from now on, and fix the cause: a dataset row, a prompt, or a gate.

Every change is in the audit log with your name: `GET <url>/admin/audit`.

Admins can't mark an answer "reviewed". Reviewed answers come only from `kb/`, through a PR (section 6).

## 3. Review web answers

Web answers are spoken right away, after the web warning, and wait for review as `pending_review`. Review them once a week during the pilot:

1. List them:
   ```powershell
   curl.exe -H "X-Admin-Key: <admin key>" "<url>/admin/answers?status=pending_review"
   ```
2. Open each source URL. Check that the page supports every claim in the description, and that nothing in it says how much to take. If `product.rx` is `true`, the app is telling users the page says this is a prescription medicine, so check that the page really says that for this strength and form.
3. Mark each one approved (`{"status": "active"}`) or rejected (`{"status": "rejected"}`), using the same PATCH as above. An approved web answer is still spoken with the web warning.

Web answers expire after 30 days either way.

## 4. Rotate a key

Rotate a key when it may have leaked, when someone leaves the team, or on a schedule. Never paste a key into chat, a ticket or git.

| Key | Where it's set | Steps |
|---|---|---|
| App key (`MEDSIGHT_API_KEYS`) | Railway, and the app build | 1. Add the new key next to the old one: `release-1=old,release-2=new`. 2. Ship an app build with the new key. 3. Once no active build uses the old key, remove it. Requests with it then get 401. |
| Admin key (`ADMIN_KEYS`) | Railway | Replace that person's `name=key` entry. Their old key stops working at once. |
| Groq (`GROQ_API_KEY`) | Groq console, then Railway | Make a new key, set it in Railway, redeploy, run the smoke test, then delete the old key in the console. |
| Tavily (`TAVILY_API_KEY`) | Tavily dashboard, then Railway | Same as Groq. |
| Supabase backend role (`DATABASE_URL`) | Supabase SQL editor, then Railway | `ALTER ROLE medsight_backend PASSWORD '<new>';` then update the password in `DATABASE_URL` and redeploy. Scans keep answering in the gap, because the cache fails open. |
| Supabase owner (`MIGRATION_DATABASE_URL`) | Supabase dashboard | Reset the database password. Only migration runs use it. |

After any rotation, run the smoke test.

## 5. Restore the database

The nightly job writes `answers-<time>.jsonl` and `audit-<time>.jsonl` (`python -m scripts.store_admin export`). Full steps are in `migrations/README.md`, under "Restore". In short:

1. If the project is gone, make a new Supabase project and do "First setup" in `migrations/README.md`.
2. Restore the newest answers file:
   ```powershell
   python -m scripts.store_admin restore <answers-....jsonl>
   ```
3. Check that `/health` answers and that a scan of a cached product says `"cache": "hit"`.

The audit export is kept as a record. It isn't loaded back. Rate counters and the parse cache rebuild themselves.

## 6. Add a medicine to the reviewed list

In Supabase, never through admin routes. See `kb/README.md` for the full steps.

1. Enter the rows from the box photos, the TheFilipinoDoctor page and the FDA portal (by hand, or drafted by the FDA lookup script and confirmed on the portal by the reviewer): in the Table Editor, or `python -m scripts.kb_build --push --kb-dir <draft folder>`.
2. A second person checks every value, types their name in `checked_by` and sets `approved` to true.
3. Within a minute `/health` shows the new `kb_version` and `kb_products`.
4. `python -m scripts.kb_build --pull`, then `python -m scripts.eval_kb` must pass.
5. To take a row off the air at once, set its `approved` to false.

### Using the review console

The console approves rows from a laptop with no deploy: `python -m scripts.review_console`, then http://127.0.0.1:8100/review/. Setup and rules are in `kb/README.md`, "Reviewing in the console".

| Problem | What to do |
|---|---|
| "This account can't use the review console" | The account isn't in `reviewers`, or `active` is false. |
| "Enter your two-factor code first" | Only with two-step sign-in on. The session didn't finish the code step. Sign out and sign in again. |
| Lost authenticator | In Supabase, Authentication, Users, remove that user's MFA factor. They set it up again at next sign-in. |
| Someone leaves the team | `UPDATE reviewers SET active = false WHERE name = '<name>';` and delete their Supabase user. |
| The login may have leaked | `ALTER ROLE medsight_review_app PASSWORD '<new>';` and update each laptop's `REVIEW_DATABASE_URL`. |
| An approved row isn't being spoken | Check that its ingredients and an `indications` passage are approved too. The backend's log lists rows it dropped. |

Table Editor approval stays on as a fallback until `kb_settings.allow_table_editor_approval` is set to false.

**Hosting the console (Vercel page + a second Railway service):**

1. Railway: in the same project, add a service from the same repo, root directory `backend`, watch paths `/backend/**`, healthcheck `/health`. Variables: `APP_MODULE=app.review_service:app`, `REVIEW_DATABASE_URL` (the `medsight_review_app` login, session pooler), `DATABASE_SSLMODE=require`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `REVIEW_ALLOWED_ORIGINS` (the Vercel URL, `https://...`, no trailing slash). Leave `REVIEW_REQUIRE_MFA` unset (on). The service refuses to start, and says why in its log, if any of these is missing. It serves only the review routes and `/health`: no scan API.
2. Vercel: new project from the repo, root directory `backend/review`. `vercel.json` sets the build (`node build.mjs`), the output folder and the security headers. Environment variables: `REVIEW_SUPABASE_URL`, `REVIEW_SUPABASE_ANON_KEY`, `REVIEW_API_BASE` (the Railway review service URL). The build fails if one is missing.
3. Put the Vercel URL in the review service's `REVIEW_ALLOWED_ORIGINS`, and redeploy it.
4. Open the Vercel URL, sign in, and set up the authenticator.

**Before the console is hosted (Vercel or anywhere else):**
- [ ] Leave `REVIEW_REQUIRE_MFA` on for the hosted service (the default). The local launcher's "off" applies only to its own process.
- [ ] Run storage variant (b) in `kb/README.md`, "Two-step sign-in setting".
- [ ] Have every reviewer sign in once to set up an authenticator app.

## 7. A provider is down

| What's down | What users hear | What to do |
|---|---|---|
| Groq | "The identification service is busy or unavailable right now. Please try again in a moment." (503) | Check status.groq.com. If the model was retired, set `GROQ_MODEL` to its replacement, redeploy, and re-run `eval_kb`. |
| Tavily (search) | Unlisted medicines get the general answer, behind its warning | Nothing is cached while it's down, so web answers resume by themselves. Check the Tavily status page and the key's credits. |
| Supabase | Nothing changes for users. Scans answer without the cache, and rate limits run from memory | Check Supabase status. A paused free project needs a restart from the dashboard. The logs show one `database skipped` warning per minute while it's down. |
| Daily model budget used up | "The identification service has reached its limit for today. Please try again later." (429) | Look in the logs for one device or address with most of the calls. Raise `LLM_DAILY_BUDGET` only if the usage is real. |

## 8. Failure drills

These run automatically in `tests/test_drills.py` on every PR. Before the pilot, run each one once by hand on **staging** and listen to the result on a phone:

1. **Groq down:** set `GROQ_API_KEY` to `invalid` and redeploy. A scan should speak the "busy or unavailable" line.
2. **Search down:** set `TAVILY_API_KEY` to `invalid`. An unlisted medicine should get the general answer with its warning.
3. **Supabase down:** pause the staging project. Scans should still answer, and the rate limit should still give "You're scanning very quickly" after 15 fast scans.
4. **Key rotated:** add a new app key, scan with it, then remove the old one. The old key should give 401.

Put every value back afterwards, and run the smoke test.

## 9. Alerts and first actions

| Alert | First action |
|---|---|
| `/health` fails 3 times in a row | Check the Railway deploy log. Roll back if the last deploy caused it (section 1). |
| Over 10% of scans are 503 in 5 minutes | Groq is down or the model was retired (section 7). |
| `database skipped` more than 3 times in an hour | Supabase (section 7). It isn't urgent: scans still work. |
| `llm budget at 80%` or `web budget at 80%` | Look for one device or address with most of the calls. |
| Over 5% of requests get 429 in an hour | Check whether it's an attack or limits that are too low. |
| A wrong answer is reported | Section 2. |
