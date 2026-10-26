# MedSight: where things stand, what's decided, what to do

Written 2026-10-19 from the repo (`main` at `0a8144d`), the development plan,
and this thread's decisions, with Mon's decisions 1 to 15. **Updated
2026-10-27:** ideation is finished and coding has started. M0 and M1 are
merged into `main`; the backend part of M2 is on branch
`backend/m2-dataset-matching`. Decisions made since 2026-10-19 are 16 to 19
below, and decisions 1 to 4 and 11 are revised.

## 1. Where the architecture is heading

```
Phone: camera -> OCR (Apple Vision / ML Kit) -> POST /v2/identify
       (text, text-block positions, device ID, scan attempt number)
                                                  |
Backend (FastAPI on paid Railway hosting)         v
  0. Rate limit by device ID and network address          <- planned (M4)
     Input checks: no letters, or too much text -> rescan  <- built (M1)
  1. Parse label text with an LLM (Groq, Qwen) -> every product seen,
     with brand, ingredients and strengths                 <- built (M1)
  2. Scan checks (section 3): rescan, specific hint, several
     medicines in view, or give up after 3 tries           <- built (M1)
  3. Match against the team's dataset in kb/:              <- built (M2 branch);
       29 brands, TheFilipinoDoctor indications turned        dataset still empty
       into a short description by an LLM once, checked
       by the team
       product match     -> speak it as "reviewed"
       partial match     -> specific rescan ("turn the box")
       ingredient match  -> checked ingredient descriptions
                            (unlisted generics and same-ingredient brands)
       no match          -> web search on allowed sites    <- planned (M6)
                            (TheFilipinoDoctor and others), behind the
                            spoken "not reviewed" warning; until then a
                            general LLM description behind its warning
     Prescription products also say "This is a prescription medicine."
  4. Cache the answer in Supabase (Postgres), failing open  <- planned (M3);
                                                               SQLite today
  5. Return spoken_text, so every phone says the warnings the same way
Phone: text-to-speech; "My medicines" list stored only on the phone (no login)
Admin: CRUD over cached records with an audit log           <- planned (M7)
```

**Built (on `main`):** tests (pytest, LLM always faked), JSON logs with
request IDs, contract v2 (`/v2/identify`) next to v1, parsing of every
product with ingredient and strength lists, the scan rules in section 3,
the raw-text and answer caches (SQLite), all spoken text in one module, the
64 KB body limit and 15 s LLM timeout, and API key auth.
**Built (M2 branch, not merged yet):** matching against `kb/`
(`retrieval.py`), the reviewed and ingredient tiers, the validator for the
new columns, the description-drafting script and the evaluation script.
**Not built:** Supabase, rate limiting, web search, Railway deployment,
admin CRUD. **`kb/` is still empty**, so every scan still gets the general
tier.

## 2. Decisions

| # | Topic | Decision | Notes and what's still open |
|---|---|---|---|
| 1 | Description of the 29 | An LLM turns each product's **TheFilipinoDoctor** indications into a short, plain description, once, and the team checks it before it goes into `kb/`. (Revised 2026-10-26: MIMS replaced by TheFilipinoDoctor, see 17.) | Built: `scripts/kb_draft_descriptions.py` writes a review sheet to `kb/staging/`; nothing is written to `kb/` automatically. |
| 2 | Name of the tier | The app says **"reviewed"**. (Revised 2026-10-20; was "verified".) | Web-search and general answers are never called reviewed. v1 keeps its old `"not verified"` warning value only for older app builds. |
| 3 | Storing source text | TheFilipinoDoctor text is stored word for word in `kb/passages.csv` as evidence. (Revised 2026-10-26.) | Every row keeps its page URL in `source_ref`, so each fact can be traced. |
| 4 | Unlisted medicines | Keep web search, limited to an allowlist that includes **TheFilipinoDoctor**. (Revised 2026-10-26: MIMS is no longer on it.) | Web answers are cached and shared with all users before review (Mon, 2026-10-19), spoken with a warning that they come from the web and aren't reviewed; marked pending review, 30-day expiry. **Open:** the other allowed sites. |
| 5 | Ingredient descriptions | Used for unlisted generics and brands with the same ingredients. | Same check as the 29: draft once, team checks, store in `kb/ingredients.csv`. Only used when **every** ingredient has a checked description. |
| 6 | Prescription products | Keep Dolfenal and Ponstan; the app says "This is a prescription medicine." | OTC/Rx comes from the FDA portal by hand, not the website. Ascof Lagundi: drop it if the portal lists it as herbal. |
| 7 | Hosting | Paid Railway hosting. | No cold starts, so the plan's Render "waking up" work is no longer needed. |
| 8 | Database | Supabase. | Keep the cache fail-open: a database problem must never fail a scan (already true for the SQLite cache). |
| 9 | Rescan rules | The checks in section 3 below. | Built in M1. **Open:** the OCR-confidence cut-off (0.55 for now), set from labeled scans. |
| 10 | Login | None. User data stays on the phone and isn't stored remotely. | No token handling on the backend. |
| 11 | PR #1 (MIMS as a source) | Superseded by decision 17: MIMS isn't used. | Close PR #1. Its validator change was replaced by the `tfd` source type in M2. |
| 12 | Several medicines in view | Ask the user to set the others aside. | Built (M1). Section 3. |
| 13 | Rate limiting | Limit scans per device and address to stop spam, scripts and attacks. | Section 3. Planned for M4. |
| 14 | Models | Label reading: Groq `qwen/qwen3.8-27b` on the free tier (about 200 scans a day), moving to the Developer plan once pitching starts. Web tier: Claude Sonnet 5.5 with web search. Descriptions of the 29 are drafted once offline and checked by the team. | A self-hosted model on Railway was compared and rejected (cost, 5 to 15 s per scan, weaker accuracy). Details in the development plan, section 3. Local development uses Ollama. |
| 15 | Cost guardrails | Testers will script requests, so Groq and Sonnet calls get hard budgets. | Built in M1: input checks and the raw-text cache before the parse, with answer-cache keys holding every ingredient and strength. Still planned: about 180 Groq calls a day, separate keys for demo and tester devices, web caps of about 3 per device and 20 a day, cached "not found", Anthropic spend limit, alerts at 80%. |
| 16 | Tier wording (2026-10-20) | Reviewed: "Our team has reviewed this information." Ingredient: "Our team hasn't reviewed this product yet. Based on its ingredients, …" Web: "This medicine isn't in our reviewed list. The following information comes from the web and hasn't been reviewed by our team." General: "Warning: this medicine isn't in our reviewed list, so this is general information only and may be inaccurate." | Fixed in `backend/app/speech.py` with snapshot tests. "This still needs a review" was rejected because a listener could take it as something they must do. |
| 17 | Dataset source (2026-10-26) | **TheFilipinoDoctor** is the text source for the 29; **MIMS is dropped** (its drug directory needs an account). | A third-party site, so the two-person check and the FDA-portal lookups are the real safeguards. Box or insert text is still accepted. |
| 18 | Scraper (2026-10-26) | The team's TheFilipinoDoctor scraper stays local and is **not committed**. | Its output goes to `backend/kb/staging/`, which is git-ignored. Only checked rows move into `kb/`. |
| 19 | Roles (2026-10-20) | The backend developer does backend work only; CI, branch protection, CODEOWNERS, Dependabot and Railway setup belong to DevOps. | The M0 CI workflow is offered to DevOps as `backend/ops/backend-ci.yml`. |

## 3. Scan rules

All rules below are built (M1) and use checks the code can make, not the
model's own confidence score. The partial-match rule needs the dataset (M2).

### When to ask for a rescan

1. **Hard rescan:** no text found; too much text (over 2,000 characters:
   "move closer"); no name found; the parsed name or a strength number
   doesn't appear anywhere in the OCR text; or the ingredients and strengths
   can't be paired.
2. **Partial match, specific rescan:** the brand matches the list but the
   strength is missing or different ("I found Biogesic but couldn't read the
   strength. Turn the box and scan the side with the ingredients."). The
   same applies to a combination product read with fewer ingredients than
   the dataset lists. Only an **exact** listed brand triggers this, so a
   near-miss spelling never makes the app name a listed brand.
3. **Answer with a caveat:** a match, but the phone's OCR confidence is
   low. Give the answer, then say "I'm not fully sure of this reading."
4. **Answer normally:** the brand, ingredients and strength all match the
   dataset.
5. **Give up from the 3rd try:** say only what was read clearly ("I can read
   Biogesic but not the strength. Please ask someone to check the label.")
   so the user isn't stuck rescanning forever. The app sends the attempt
   number and resets it after an answer.
6. **Hints by cause:** when the phone can tell the text was small, blurry or
   dark, the message says "move closer" or "find more light" instead of just
   "try again".
7. **Calibration:** set the OCR-confidence cut-off from the team's labeled
   scans, so that no scan in the test set gets a wrong reviewed answer.

### Several medicines in view

- The parse step returns every product it sees. It's "multiple" only when
  the text shows **two different brands**, or **two strengths of the same
  brand**, or an unbranded product with other ingredients. A box that
  mentions another product ("also available: Biogesic for kids") or lists
  several ingredients doesn't count.
- When the app sends text-block positions, a brand printed much smaller than
  the main brand (under 40% of its height) is treated as a mention, not a
  second box. The 40% is a starting value to tune on labeled scans.
- The user hears: "I can see more than one medicine. Please set the others
  aside and scan one at a time." It has its own status, `multiple`.

### Rate limiting (planned, M4)

There's no login, so limits use a device ID and the network address together.

- **Device ID:** the app creates a random ID when it's installed and sends it
  with every scan (`X-Device-Id`, accepted since M1 and logged only as a
  hash). It can be faked, which is why the address limit also applies.
- **Starting limits** (to adjust after the pilot): about 6 scans a minute and
  150 a day per device, and about 30 a minute per address. That allows a
  user to rescan several times in a row but blocks scripts.
- **Web search** costs the most, so it gets a lower limit and a daily cap on
  total spend across all users.
- **When blocked:** the user hears "You're scanning very quickly. Please wait
  a few seconds and try again." The app also doesn't send a new scan while
  one is still running.
- **Counters** are stored in Supabase, so the limits hold across Railway
  restarts and multiple copies of the backend. On Railway, the client
  address comes from the proxy's forwarded header.
- **Weak spot:** the app's API key is built into the app and can be
  extracted. Rate limits and spend caps are the real protection; phone
  attestation (Play Integrity, App Attest) is an option for later.

## 4. What needs doing, in order

**Dataset (team) — gates M2:**
1. Find the 14 brands the scraper couldn't load (Alaxan FR, Ponstan,
   Saridon, Aspilets, Neozep Forte, Sinutab, Robitussin, Ambrolex, Ascof
   Lagundi, Virlix, Zyrtec, Benadryl, Imodium, Gaviscon) and the right
   Solmux page, or use the box.
2. Keep only the strengths and forms a user scans at home (drop the
   Fluimucil injection and inhalation solution).
3. Look up each registration number, expiry and OTC/Rx on the FDA portal
   by hand; fill in the synonyms and PNF columns.
4. Check each drafted description in `kb/staging/descriptions_review.csv`
   against its source, simplify the jargon, and write the 8 ingredient
   descriptions that only appear in combination products.
5. Fill `kb/` with real names in `entered_by` and `checked_by`, two
   different people per row, through a reviewed PR.
6. Collect at least 100 real OCR texts from the app and label which product
   each one is. Without these, nothing can be measured.

**Backend (code):**
1. ~~Tests and the known bugs~~ (M0, merged).
2. ~~Parse every product in view, with ingredient and strength lists~~ (M1, merged).
3. ~~`retrieval.py`: match only when ingredients and strengths agree~~ (M2 branch).
4. ~~Scan rules: rescan levels, hints, retry limit, several medicines,
   prescription wording~~ (M1 and M2).
5. ~~Evaluation script~~ (`eval_kb.py`, M2 branch); run it once the dataset
   and labeled scans exist.
6. Check the general tier on Groq with unknown medicines: the local 4B model
   described a made-up product confidently.
7. Supabase for the cache and the rate-limit counters, failing open (M3).
8. Rate limiting (M4).
9. Smoke test and Railway configuration on the backend side (M5).
10. Web search on allowed sites for unlisted medicines (M6).
11. Admin CRUD (M7).

**Frontend team:**
1. A debug export of OCR text, for the evaluation scans.
2. Review contract v2 (`backend/README.md`) and send back any changes.
3. Switch to `/v2/identify`: send the device ID, the scan attempt number,
   the text-block positions and capture hints; handle `multiple` and
   `gave_up`; say "reviewed" instead of "verified" on screen.
4. Don't send a new scan while one is running, and play the rate-limit
   message (M4).

**DevOps:**
1. Add the backend CI (`backend/ops/backend-ci.yml`) and make its checks
   required on `main`.
2. CODEOWNERS for `backend/kb/`, Dependabot for `backend/` pip packages.
3. Railway environments (M5).

## 5. My professional judgment of the system

**What it is:** an assistive label reader. It reads a medicine box with OCR,
uses an LLM to pick out the name and ingredients, and reads back a short
description that is either from a small team-checked list or clearly marked
as not reviewed. It identifies and informs; it doesn't advise, and that's
the right line to hold.

**What's good:**
- The safety instincts are better than most projects at this stage. Warnings
  come before the description, there's no dosing advice (now also enforced in
  code), the server fails closed without a key, and it asks for a rescan
  instead of guessing.
- The core is still small and clean: one shared pipeline, one module per
  step, and spoken text built on the server so wording stays consistent.
- It's now tested: about 240 automated tests, including every risky pair of
  medicines that share ingredients.
- The tiers (reviewed, ingredient, general, later web) are a sound way to be
  useful without pretending to know everything.

**What's weak:**
- **There's still no evidence that it works on real scans.** There are no
  labeled phone scans and no accuracy numbers, and the dataset is empty.
  The matching is strict on paper; only the evaluation can show it.
- **The dataset is the product, and its source is now a third-party
  website.** TheFilipinoDoctor is convenient, but it isn't official
  labeling. The two-person check and the hand lookups on the FDA portal are
  what make a row trustworthy. The tooling catches format errors, doses and
  missing checkers, not made-up data, so the team's discipline is still the
  real safeguard.
- **The general tier can be confidently wrong.** A small model described a
  made-up product as a pain reliever. It's spoken behind a warning, but it
  should be checked on Groq, and the web tier should replace it for unlisted
  medicines.
- **The plan is larger than the team.** Supabase, rate limiting, Railway,
  web search and admin tools are still ahead.

**Overall:** the backend has gone from about a third of the way to a
pilot to roughly half. What makes it pilot-ready is still the same three
things: (1) the 29 medicines entered and honestly checked, (2) strict
matching (now built), and (3) an evaluation showing zero wrong matches on
real phone scans. I'd put the team's next weeks into (1) and (3), and build
Supabase, rate limiting and Railway alongside. Also ask your instructor
whether a tool that tells people what a medicine is for needs any review or
disclaimer under Philippine rules; I'm not able to judge that.
